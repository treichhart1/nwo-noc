"""Auditable physical inventory. Changes never configure equipment or monitoring."""

import csv, io, json, hashlib, base64, re, struct, zlib
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4
from sqlalchemy import select, func, text, or_, and_
from app.models.entities import Device, Site, User, AuditLog, Incident, NetworkLink
from app.customers.models import CustomerService
from app.core.operator_text import clean_operator_text
from .models import (
    Category,
    Manufacturer,
    Product,
    Location,
    Asset,
    DeviceBinding,
    Movement,
    BulkBalance,
    BulkMovement,
    Maintenance,
    Attachment,
    Financial,
    FinancialChange,
    CostEntry,
    StockPolicy,
    ImportBatch,
)
from .contracts import AssetInput, FinancialInput

SERVICEABLE = {"new", "good", "refurbished"}
INSTALLED = {"installed", "in_service"}
TRANSITIONS = {
    "ordered": {"received", "retired"},
    "received": {"available", "installed", "returned", "under_maintenance", "retired"},
    "available": {
        "reserved",
        "installed",
        "under_maintenance",
        "failed",
        "returned",
        "retired",
    },
    "reserved": {"available", "installed", "under_maintenance", "failed", "returned"},
    "installed": {"in_service", "under_maintenance", "failed", "removed"},
    "in_service": {"under_maintenance", "failed", "removed"},
    "under_maintenance": {
        "available",
        "installed",
        "in_service",
        "failed",
        "removed",
        "returned",
        "retired",
    },
    "failed": {"under_maintenance", "removed", "returned", "retired"},
    "removed": {"available", "installed", "under_maintenance", "returned", "retired"},
    "returned": {"received", "available", "under_maintenance", "retired"},
    "retired": set(),
}


def clock():
    return datetime.now(timezone.utc)


def lock(db):
    db.execute(text("SET LOCAL statement_timeout='5000ms'"))
    db.execute(text("SELECT pg_advisory_xact_lock(1000100)"))


def required(db, model, ident):
    row = db.get(model, ident)
    if row is None:
        raise ValueError("Referenced inventory record unavailable")
    return row


def value(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, (Decimal, UUID)):
        return str(v)
    return v


def view(row, exclude=()):
    return {
        c.name: value(getattr(row, c.name))
        for c in row.__table__.columns
        if c.name not in exclude
    }


def clean(data):
    if isinstance(data, dict):
        for v in data.values():
            clean(v)
    elif isinstance(data, list):
        for v in data:
            clean(v)
    elif isinstance(data, str):
        clean_operator_text(data)


def audit(db, actor, action, entity, ident, details=None):
    db.add(
        AuditLog(
            user_id=actor,
            action="equipment." + action,
            entity_type=entity,
            entity_id=str(ident),
            details={"documentation_only": True, **(details or {})},
        )
    )


def record(db, a, actor, action, reason, before):
    clean(reason)
    db.add(
        Movement(
            asset_id=a.id,
            actor_id=actor,
            action=action,
            reason=reason,
            before=before,
            after=view(a),
        )
    )
    audit(db, actor, action, "equipment_asset", a.id)


def technician(db, ident):
    if ident is None:
        return
    u = required(db, User, ident)
    if not u.is_active or u.role not in {"operator", "admin"}:
        raise ValueError("Technician must be an active NOC operator/admin")


def create_category(db, b, actor):
    lock(db)
    clean(b.model_dump())
    if db.scalar(select(Category.id).where(Category.code == b.code)):
        raise ValueError("Category already exists")
    row = Category(code=b.code, name=b.name)
    db.add(row)
    db.flush()
    audit(db, actor, "category_created", "equipment_category", row.id)
    return row


def create_product(db, b, actor):
    lock(db)
    required(db, Category, b.category_id)
    clean(b.model_dump())
    key = b.manufacturer.strip().casefold()
    maker = db.scalar(select(Manufacturer).where(Manufacturer.key == key))
    if maker is None:
        maker = Manufacturer(name=b.manufacturer.strip(), key=key)
        db.add(maker)
        db.flush()
    model_key = b.model.strip().casefold()
    revision = b.hardware_revision.strip().casefold()
    if db.scalar(
        select(Product.id).where(
            Product.manufacturer_id == maker.id,
            Product.model_key == model_key,
            Product.hardware_revision == revision,
        )
    ):
        raise ValueError("Catalog model/revision already exists")
    row = Product(
        **b.model_dump(
            exclude={"reason", "manufacturer", "model", "hardware_revision"}
        ),
        manufacturer_id=maker.id,
        model=b.model.strip(),
        model_key=model_key,
        hardware_revision=revision,
    )
    db.add(row)
    db.flush()
    audit(db, actor, "product_created", "equipment_product", row.id)
    return row


def create_location(db, b, actor):
    lock(db)
    clean(b.model_dump())
    technician(db, b.technician_id)
    if b.site_id:
        required(db, Site, b.site_id)
    if b.service_id:
        required(db, CustomerService, b.service_id)
    if db.scalar(select(Location.id).where(Location.code == b.code)):
        raise ValueError("Location code already exists")
    row = Location(**b.model_dump(exclude={"reason"}))
    db.add(row)
    db.flush()
    audit(db, actor, "location_created", "equipment_location", row.id)
    return row


def resolve(db, ident):
    if str(ident).isdigit():
        return required(db, Asset, int(ident))
    try:
        uid = UUID(str(ident))
    except ValueError:
        raise ValueError("Valid protected asset identifier required") from None
    row = db.scalar(select(Asset).where(Asset.public_id == uid))
    if row is None:
        raise LookupError()
    return row


def check_location(db, location, state, condition, device=None):
    if state not in {"ordered"} and location is None:
        raise ValueError("Current physical inventory location required")
    if state in {"available", "reserved"} and condition not in SERVICEABLE:
        raise ValueError("Verify condition before counting available/reserved spares")
    if state in INSTALLED:
        if location is None or location.kind not in {"tower", "customer_premises"}:
            raise ValueError(
                "Installed equipment requires a tower or reviewed customer location"
            )
        if condition == "scrapped":
            raise ValueError("Scrapped equipment cannot be installed")
    if condition == "scrapped" and state != "retired":
        raise ValueError("Scrapped equipment must be retired")
    if device:
        if location is None or location.kind not in {"tower", "customer_premises"}:
            raise ValueError(
                "Remove the logical-device binding before storing physical equipment offsite"
            )
        site = location.site_id
        if location.service_id:
            service = required(db, CustomerService, location.service_id)
            site = service.site_id
        if not site or device.wisp_site_id != site:
            raise ValueError(
                "Device and physical/serving tower mapping disagree; review inventory instead of moving monitoring records"
            )


def identity_matches(db, product, serial=None, mac=None, tag=None):
    conditions = []
    if serial:
        conditions.append(
            and_(
                Asset.manufacturer_id == product.manufacturer_id,
                Asset.serial_key == serial.strip().lower(),
            )
        )
    if mac:
        conditions.append(Asset.mac_address == mac)
    if tag:
        conditions.append(Asset.asset_tag == tag.upper())
    return (
        list(db.scalars(select(Asset).where(or_(*conditions)).limit(20)))
        if conditions
        else []
    )


def end_binding(db, a, at):
    rows = list(
        db.scalars(
            select(DeviceBinding).where(
                DeviceBinding.asset_id == a.id, DeviceBinding.ended_at.is_(None)
            )
        )
    )
    for row in rows:
        row.ended_at = at
    a.device_id = None
    db.flush()


def bind(db, a, device_id, at):
    if device_id is None:
        return
    d = required(db, Device, device_id)
    location = required(db, Location, a.location_id) if a.location_id else None
    check_location(db, location, a.state, a.condition, d)
    other = db.scalar(select(Asset.id).where(Asset.device_id == d.id, Asset.id != a.id))
    if other:
        raise ValueError("Monitoring device already linked to a current physical asset")
    old = db.scalar(
        select(DeviceBinding).where(
            DeviceBinding.asset_id == a.id, DeviceBinding.ended_at.is_(None)
        )
    )
    if old and old.device_id != d.id:
        raise ValueError(
            "Remove the prior binding through the lifecycle workflow first"
        )
    a.device_id = d.id
    if not old:
        overlap = db.scalar(
            select(DeviceBinding.id)
            .where(
                or_(DeviceBinding.device_id == d.id, DeviceBinding.asset_id == a.id),
                or_(DeviceBinding.ended_at.is_(None), DeviceBinding.ended_at > at),
            )
            .limit(1)
        )
        if overlap:
            raise ValueError(
                "Historical physical binding overlaps; review installation evidence instead of guessing dates"
            )
        db.add(DeviceBinding(asset_id=a.id, device_id=d.id, started_at=at))


def create_asset(db, b, actor):
    lock(db)
    clean(b.model_dump(mode="json"))
    p = required(db, Product, b.product_id)
    if not p.individually_tracked:
        raise ValueError("Use bulk stock transactions for non-individual materials")
    technician(db, b.technician_id)
    location = required(db, Location, b.location_id) if b.location_id else None
    d = required(db, Device, b.device_id) if b.device_id else None
    if identity_matches(db, p, b.serial_number, b.mac_address, b.asset_tag):
        raise ValueError(
            "Asset tag, manufacturer serial or physical MAC already documented"
        )
    # Detect pre-existing NOC equipment before creating an unlinked physical duplicate.
    conditions = []
    if b.serial_number:
        conditions.append(
            func.lower(func.trim(Device.serial_number)) == b.serial_number.lower()
        )
    if b.mac_address:
        conditions.append(
            func.lower(func.replace(Device.mac_address, "-", ":")) == b.mac_address
        )
    if b.asset_tag:
        conditions.append(func.upper(Device.asset_tag) == b.asset_tag)
    if conditions:
        devices = list(db.scalars(select(Device).where(or_(*conditions)).limit(21)))
        maker = required(db, Manufacturer, p.manufacturer_id)
        matches = [
            item
            for item in devices
            if not item.vendor
            or item.vendor.strip().casefold() == maker.key
            or (
                b.mac_address
                and item.mac_address
                and item.mac_address.lower().replace("-", ":") == b.mac_address
            )
        ]
        if any(item.id != b.device_id for item in matches) or (matches and d is None):
            raise ValueError(
                "Existing NOC device identity requires explicit reconciliation/linking"
            )
    if d and db.scalar(select(Asset.id).where(Asset.device_id == d.id)):
        raise ValueError("Existing monitored device already has an asset")
    check_location(db, location, b.state, b.condition, d)
    if d and b.state not in INSTALLED | {"under_maintenance", "failed"}:
        raise ValueError(
            "Bind monitored devices only for installed/operational equipment"
        )
    now = clock()
    a = Asset(
        **b.model_dump(exclude={"reason", "device_id", "asset_tag"}),
        asset_tag=b.asset_tag or "NWO-" + uuid4().hex[:16].upper(),
        manufacturer_id=p.manufacturer_id,
        serial_key=b.serial_number.lower() if b.serial_number else None,
    )
    if b.state == "retired":
        a.retired_at = now
        a.retirement_disposition = "retired"
    db.add(a)
    db.flush()
    if d:
        if b.state not in INSTALLED | {"under_maintenance", "failed"}:
            raise ValueError(
                "Bind monitored devices only for installed/operational equipment"
            )
        bind(db, a, d.id, b.installed_at or now)
    db.flush()
    record(db, a, actor, "initial_documentation", b.reason, {})
    return a


def move_asset(db, a, b, actor):
    lock(db)
    db.refresh(a)
    if a.revision != b.expected_revision:
        raise ValueError("Asset changed; reload revision before moving")
    before = view(a)
    target = b.to_state
    if target != a.state and target not in TRANSITIONS[a.state]:
        raise ValueError("Invalid equipment lifecycle transition")
    if a.state == "retired":
        raise ValueError("Retired history is immutable")
    location = (
        required(db, Location, b.to_location_id)
        if b.to_location_id
        else required(db, Location, a.location_id) if a.location_id else None
    )
    condition = b.condition or a.condition
    if b.scrap:
        if target != "retired":
            raise ValueError("Scrap requires retirement")
        condition = "scrapped"
    if b.technician_id:
        technician(db, b.technician_id)
    device = (
        required(db, Device, b.device_id or a.device_id)
        if (b.device_id or a.device_id)
        else None
    )
    check_location(
        db,
        location,
        target,
        condition,
        (
            None
            if target in {"removed", "returned", "retired", "available", "received"}
            else device
        ),
    )
    if (
        a.state in INSTALLED
        and target != "removed"
        and location
        and location.id != a.location_id
    ):
        raise ValueError("Document removal before transferring installed equipment")
    if a.device_id and device and a.device_id != device.id:
        raise ValueError("Remove the current device binding before relinking equipment")
    now = clock()
    if (
        target in {"removed", "returned", "retired", "available", "received"}
        and a.device_id
    ):
        end_binding(db, a, now)
        device = None
    if target not in INSTALLED | {"under_maintenance", "failed"} and b.device_id:
        raise ValueError("Device binding requires installed operational context")
    a.location_id = location.id if location else None
    a.state = target
    a.condition = condition
    a.updated_at = now
    a.revision += 1
    if b.technician_id:
        a.technician_id = b.technician_id
    if target == "installed" and before["state"] != "installed":
        a.installed_at = now
    if before["state"] in {"failed", "under_maintenance"} and target in {
        "available",
        "installed",
        "in_service",
    }:
        a.documented_operational_status = "unknown"
    if target == "failed":
        a.documented_operational_status = "failed"
    if target == "retired":
        a.retired_at = now
        a.retirement_disposition = "scrapped" if b.scrap else "retired"
    if device and a.device_id is None:
        bind(db, a, device.id, now)
    db.flush()
    record(
        db,
        a,
        actor,
        "transfer" if before["state"] == target else "lifecycle_changed",
        b.reason,
        before,
    )
    return a


def replace_asset(db, old, b, actor):
    lock(db)
    db.refresh(old)
    new = required(db, Asset, b.new_asset_id)
    db.refresh(new)
    if (
        old.id == new.id
        or old.revision != b.old_revision
        or new.revision != b.new_revision
    ):
        raise ValueError("Replacement revisions changed or assets are identical")
    if (
        old.state not in INSTALLED | {"failed", "under_maintenance"}
        or new.state not in {"available", "reserved", "removed"}
        or new.condition not in SERVICEABLE
    ):
        raise ValueError(
            "Replacement requires an installed/failed asset and verified spare"
        )
    removed_location = required(db, Location, b.removed_to_location_id)
    installed_location = required(db, Location, old.location_id)
    if installed_location.kind not in {"tower", "customer_premises"}:
        raise ValueError("Old asset is not at an installed location")
    if new.device_id:
        raise ValueError("Replacement spare already bound to monitoring")
    if removed_location.id == installed_location.id:
        raise ValueError("Document a separate removed/repair stock location")
    old_before = view(old)
    new_before = view(new)
    device_id = old.device_id
    now = clock()
    end_binding(db, old, now)
    old.state = "removed"
    old.location_id = removed_location.id
    old.revision += 1
    old.updated_at = now
    new.location_id = installed_location.id
    new.state = "installed"
    new.installed_at = now
    new.replacement_of_id = old.id
    new.revision += 1
    new.updated_at = now
    if b.inherit_device and device_id:
        bind(db, new, device_id, now)
    db.flush()
    record(db, old, actor, "replaced_removed", b.reason, old_before)
    record(db, new, actor, "replacement_installed", b.reason, new_before)
    return {
        "old": view(old),
        "new": view(new),
        "monitoring_configuration_changes": 0,
        "ipam_changes": 0,
        "new_telemetry_requires_post_binding_sample": True,
    }


def bulk_move(db, b, actor):
    lock(db)
    clean(b.model_dump(mode="json"))
    p = required(db, Product, b.product_id)
    if p.individually_tracked:
        raise ValueError("Use individual asset lifecycle transactions")
    if p.unit == "each" and b.quantity % 1:
        raise ValueError("Whole quantities required for unit each")
    digest = hashlib.sha256(
        json.dumps(b.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    existing = db.scalar(
        select(BulkMovement).where(BulkMovement.event_key == b.event_key)
    )
    if existing:
        if existing.digest != digest:
            raise ValueError("Bulk event key conflicts with prior movement")
        return {"movement": view(existing), "duplicate": True}
    dest = required(db, Location, b.to_location_id)
    if b.action == "receive":
        if b.from_location_id or b.from_state or b.to_state != "available":
            raise ValueError("Receipt adds only available stock without a source")
    else:
        if not b.from_location_id or not b.from_state:
            raise ValueError("Existing source bucket required")
        if b.from_state == "retired":
            raise ValueError("Retired stock cannot be silently recovered")
        required(db, Location, b.from_location_id)
    allowed = {
        "transfer": None,
        "install": "installed",
        "remove": None,
        "reserve": "reserved",
        "release": "available",
        "repair": "repair",
        "retire": "retired",
        "scrap": "retired",
        "receive": "available",
    }
    if allowed[b.action] and b.to_state != allowed[b.action]:
        raise ValueError("Action and destination stock state disagree")
    if b.action == "transfer" and b.to_state != b.from_state:
        raise ValueError("Transfer retains stock state")
    if b.action == "install" and dest.kind not in {"tower", "customer_premises"}:
        raise ValueError("Installed material requires an installation location")
    if b.action == "remove" and b.from_state != "installed":
        raise ValueError("Removal requires installed material")
    if b.action == "release" and b.from_state != "reserved":
        raise ValueError("Release requires reserved material")
    if b.from_location_id == b.to_location_id and b.from_state == b.to_state:
        raise ValueError("Source and destination buckets are identical")
    source = None
    if b.from_location_id:
        source = db.scalar(
            select(BulkBalance)
            .where(
                BulkBalance.product_id == p.id,
                BulkBalance.location_id == b.from_location_id,
                BulkBalance.state == b.from_state,
            )
            .with_for_update()
        )
        if (
            not source
            or source.quantity < b.quantity
            or b.expected_source_revision != source.revision
        ):
            raise ValueError("Insufficient stock or source revision changed")
    target = db.scalar(
        select(BulkBalance)
        .where(
            BulkBalance.product_id == p.id,
            BulkBalance.location_id == b.to_location_id,
            BulkBalance.state == b.to_state,
        )
        .with_for_update()
    )
    if target is None:
        target = BulkBalance(
            product_id=p.id,
            location_id=b.to_location_id,
            state=b.to_state,
            quantity=Decimal(0),
        )
        db.add(target)
        db.flush()
    if source:
        source.quantity -= b.quantity
        source.revision += 1
    target.quantity += b.quantity
    target.revision += 1
    row = BulkMovement(
        **b.model_dump(exclude={"reason", "expected_source_revision"}),
        digest=digest,
        reason=b.reason,
        actor_id=actor,
    )
    db.add(row)
    db.flush()
    audit(
        db,
        actor,
        "bulk_movement",
        "equipment_bulk_movement",
        row.id,
        {"action": b.action},
    )
    return {"movement": view(row), "duplicate": False}


def add_maintenance(db, a, b, actor):
    lock(db)
    clean(b.model_dump(mode="json"))
    tech = b.technician_id or actor
    technician(db, tech)
    from app.tower_health.models import TowerFinding

    if b.tower_finding_id:
        finding = required(db, TowerFinding, b.tower_finding_id)
        related = db.scalar(
            select(DeviceBinding.id).where(
                DeviceBinding.asset_id == a.id,
                DeviceBinding.device_id == finding.device_id,
            )
        )
        if not related:
            raise ValueError("Maintenance finding belongs to different equipment")
    for part in b.parts:
        product = required(db, Product, part.product_id)
        if product.unit == "each" and part.quantity % 1:
            raise ValueError("Whole replacement parts required")
        if part.bulk_movement_id:
            movement = required(db, BulkMovement, part.bulk_movement_id)
            if (
                movement.product_id != part.product_id
                or movement.quantity < part.quantity
                or movement.action not in {"install", "remove", "repair"}
            ):
                raise ValueError("Parts and referenced stock movement disagree")
    row = Maintenance(
        **b.model_dump(exclude={"reason", "technician_id"}, mode="python"),
        asset_id=a.id,
        technician_id=tech,
        actor_id=actor,
    )
    row.parts = [p.model_dump(mode="json") for p in b.parts]
    db.add(row)
    db.flush()
    db.add(
        Movement(
            asset_id=a.id,
            actor_id=actor,
            action="maintenance_recorded",
            reason=b.reason,
            before={},
            after={"maintenance_id": row.id, "status": row.status},
        )
    )
    audit(db, actor, "maintenance_recorded", "equipment_maintenance", row.id)
    return row


def verify_file(b):
    try:
        raw = base64.b64decode(b.contents_base64, validate=True)
    except Exception:
        raise ValueError("Valid bounded base64 attachment required") from None
    if not raw or len(raw) > 512 * 1024:
        raise ValueError("Attachment maximum512KiB")
    clean(b.filename)
    suffix = b.filename.rsplit(".", 1)[-1].lower()
    if b.media_type == "image/png":
        if suffix != "png" or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("PNG content/type mismatch")
        pos = 8
        chunks = 0
        ended = False
        while pos + 12 <= len(raw):
            size = struct.unpack("!I", raw[pos : pos + 4])[0]
            kind = raw[pos + 4 : pos + 8]
            end = pos + 12 + size
            if end > len(raw) or size > 512 * 1024:
                raise ValueError("Malformed PNG bounds")
            data = raw[pos + 8 : pos + 8 + size]
            crc = struct.unpack("!I", raw[pos + 8 + size : end])[0]
            if zlib.crc32(kind + data) & 0xFFFFFFFF != crc:
                raise ValueError("Malformed PNG checksum")
            if chunks == 0:
                if kind != b"IHDR" or size != 13:
                    raise ValueError("PNG header missing")
                width, height = struct.unpack("!II", data[:8])
                if not width or not height or width * height > 20_000_000:
                    raise ValueError("Image dimensions exceed policy")
            chunks += 1
            if chunks > 2000:
                raise ValueError("Too many PNG chunks")
            pos = end
            if kind == b"IEND":
                if size or end != len(raw):
                    raise ValueError("Trailing PNG content")
                ended = True
                break
        if not ended:
            raise ValueError("Incomplete PNG")
    elif b.media_type == "image/jpeg":
        if (
            suffix not in {"jpg", "jpeg"}
            or not raw.startswith(b"\xff\xd8\xff")
            or not raw.endswith(b"\xff\xd9")
        ):
            raise ValueError("JPEG content/type mismatch")
    else:
        if suffix != "txt":
            raise ValueError("Only plain-text documents supported")
        try:
            content = raw.decode("utf8")
        except UnicodeError:
            raise ValueError("UTF8 text required") from None
        clean(content)
        if "\x00" in content or re.search(r"<(?:script|html|svg)\b", content, re.I):
            raise ValueError("Active content not supported")
    return raw


def add_attachment(db, maintenance, b, actor):
    lock(db)
    raw = verify_file(b)
    if (
        db.scalar(
            select(func.count(Attachment.id)).where(
                Attachment.maintenance_id == maintenance.id
            )
        )
        >= 5
    ):
        raise ValueError("Maximumfive attachments per maintenance record")
    row = Attachment(
        maintenance_id=maintenance.id,
        filename=b.filename,
        media_type=b.media_type,
        restricted_financial=b.restricted_financial,
        data=raw,
        size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        actor_id=actor,
    )
    db.add(row)
    db.flush()
    audit(
        db,
        actor,
        "attachment_added",
        "equipment_attachment",
        row.id,
        {"size": len(raw)},
    )
    return view(row, exclude={"data"}) | {"unscanned": True, "download_only": True}


def set_financial(db, a, b, actor):
    lock(db)
    clean(b.model_dump(mode="json"))
    row = db.get(Financial, a.id)
    before = view(row) if row else {}
    data = b.model_dump(exclude={"reason"})
    if row is None:
        row = Financial(asset_id=a.id, updated_by=actor)
        db.add(row)
    for key, v in data.items():
        setattr(row, key, v)
    row.updated_by = actor
    row.updated_at = clock()
    db.flush()
    db.add(
        FinancialChange(
            asset_id=a.id,
            actor_id=actor,
            reason=b.reason,
            before=before,
            after=view(row),
        )
    )
    audit(
        db, actor, "financial_updated", "equipment_asset", a.id
    )  # Never put money in general audit DTOs.
    return financial_view(db, a)


def add_cost(db, b, actor):
    lock(db)
    clean(b.model_dump(mode="json"))
    asset = None
    location = None
    if b.maintenance_id:
        m = required(db, Maintenance, b.maintenance_id)
        asset = required(db, Asset, m.asset_id)
        location = b.location_id
        if location:
            required(db, Location, location)
        kind = "repair"
        if db.scalar(select(CostEntry.id).where(CostEntry.maintenance_id == m.id)):
            raise ValueError("Repair cost already documented; do not double count")
    else:
        movement = required(db, BulkMovement, b.bulk_movement_id)
        if movement.action != "receive":
            raise ValueError("Only actual bulk receipt costs are recorded here")
        if db.scalar(
            select(CostEntry.id).where(CostEntry.bulk_movement_id == movement.id)
        ):
            raise ValueError("Receipt cost already documented")
        location = movement.to_location_id
        if b.location_id and b.location_id != location:
            raise ValueError("Receipt cost location must match the recorded receipt")
        kind = "bulk_receipt"
    row = CostEntry(
        **b.model_dump(exclude={"reason", "location_id"}),
        asset_id=asset.id if asset else None,
        location_id=location,
        kind=kind,
        actor_id=actor,
    )
    db.add(row)
    db.flush()
    audit(db, actor, "cost_recorded", "equipment_cost", row.id)
    return view(row)


def financial_view(db, a, as_of=None):
    row = db.get(Financial, a.id)
    as_of = as_of or clock().date()
    data = view(row) if row else None
    book = None
    missing = []
    if not row:
        missing = ["financial record"]
    else:
        for field in [
            "purchase_cost",
            "salvage_value",
            "depreciation_method",
            "useful_life_months",
            "currency",
        ]:
            if getattr(row, field) is None:
                missing.append(field)
        if not a.purchase_date:
            missing.append("purchase_date")
        if a.purchase_date and as_of < a.purchase_date:
            missing.append("as_of_precedes_purchase")
        if not missing:
            months = max(
                0,
                (as_of.year - a.purchase_date.year) * 12
                + as_of.month
                - a.purchase_date.month
                - int(as_of.day < a.purchase_date.day),
            )
            months = min(months, row.useful_life_months)
            book = (
                row.purchase_cost
                - (row.purchase_cost - row.salvage_value)
                * Decimal(months)
                / Decimal(row.useful_life_months)
            ).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    repairs = db.execute(
        select(CostEntry.currency, func.sum(CostEntry.amount))
        .where(CostEntry.asset_id == a.id, CostEntry.kind == "repair")
        .group_by(CostEntry.currency)
    ).all()
    return {
        "record": data,
        "repair_actuals_by_currency": {
            currency: str(amount) for currency, amount in repairs
        },
        "book_value": str(book) if book is not None else None,
        "as_of": as_of.isoformat(),
        "missing_depreciation_inputs": missing,
        "basis": "Explicit straight-line whole-calendar-month assumptions only; no currency conversion or valuation guarantee",
        "history": [
            view(r)
            for r in db.scalars(
                select(FinancialChange)
                .where(FinancialChange.asset_id == a.id)
                .order_by(FinancialChange.id.desc())
                .limit(100)
            )
        ],
    }


def cost_report(db, site_id=None):
    query = (
        select(
            Financial.currency,
            func.sum(Financial.purchase_cost),
            func.count(Financial.purchase_cost),
            func.sum(Financial.replacement_estimate),
            func.sum(Financial.installation_labor_estimate),
        )
        .join(Asset, Asset.id == Financial.asset_id)
        .outerjoin(Location, Location.id == Asset.location_id)
    )
    scope = select(func.count(Asset.id)).outerjoin(
        Location, Location.id == Asset.location_id
    )
    if site_id:
        query = query.where(Location.site_id == site_id, Asset.state.in_(INSTALLED))
        scope = scope.where(Location.site_id == site_id, Asset.state.in_(INSTALLED))
    rows = db.execute(query.group_by(Financial.currency)).all()
    known = sum(row[2] for row in rows)
    actual = select(
        CostEntry.currency, CostEntry.kind, func.sum(CostEntry.amount)
    ).outerjoin(Location, Location.id == CostEntry.location_id)
    if site_id:
        actual = actual.where(Location.site_id == site_id)
    return {
        "known_purchase_by_currency": [
            {
                "currency": currency,
                "purchase_cost": str(cost) if cost is not None else None,
                "known_purchase_count": count,
                "replacement_estimate": str(replace) if replace is not None else None,
                "installation_labor_estimate": (
                    str(labor) if labor is not None else None
                ),
            }
            for currency, cost, count, replace, labor in rows
        ],
        "missing_purchase_cost_count": db.scalar(scope) - known,
        "actual_event_costs": [
            {"currency": currency, "kind": kind, "amount": str(amount)}
            for currency, kind, amount in db.execute(
                actual.group_by(CostEntry.currency, CostEntry.kind)
            )
        ],
        "scope": (
            "Current installed assets at tower; actual event costs at recorded event location"
            if site_id
            else "Documented inventory purchase basis and actual cost events"
        ),
        "estimated_costs_are_not_actuals": True,
        "currency_conversion": False,
    }


def set_stock_policy(db, b, actor, ident=None):
    lock(db)
    clean(b.model_dump(mode="json"))
    if b.product_id:
        p = required(db, Product, b.product_id)
        if p.unit != b.unit:
            raise ValueError("Policy/product units disagree")
    if b.category_id:
        required(db, Category, b.category_id)
    if b.location_id:
        required(db, Location, b.location_id)
    row = required(db, StockPolicy, ident) if ident else StockPolicy()
    for key, v in b.model_dump(exclude={"reason"}).items():
        setattr(row, key, v)
    db.add(row)
    db.flush()
    audit(db, actor, "stock_policy_saved", "equipment_stock_policy", row.id)
    return row


def planning(db, *, limit=100):
    policies = list(
        db.scalars(select(StockPolicy).order_by(StockPolicy.id).limit(limit))
    )
    products = {
        p.id: p for p in db.scalars(select(Product).order_by(Product.id).limit(5001))
    }
    if len(products) > 5000:
        raise ValueError("Narrow spare planning catalog")
    counts = {}
    for product, loc, count in db.execute(
        select(Asset.product_id, Asset.location_id, func.count())
        .where(Asset.state == "available", Asset.condition.in_(SERVICEABLE))
        .group_by(Asset.product_id, Asset.location_id)
    ):
        counts[(product, loc)] = Decimal(count)
    for product, loc, count in db.execute(
        select(
            BulkBalance.product_id,
            BulkBalance.location_id,
            func.sum(BulkBalance.quantity),
        )
        .where(BulkBalance.state == "available")
        .group_by(BulkBalance.product_id, BulkBalance.location_id)
    ):
        counts[(product, loc)] = counts.get((product, loc), Decimal(0)) + count
    result = []

    def add(rule, evidence, action, priority="medium"):
        result.append(
            {
                "rule": rule,
                "evidence": evidence,
                "action": action,
                "priority": priority,
                "confidence": "documented_inventory_evidence",
                "automatic_ordering": False,
                "automatic_remediation": False,
            }
        )

    for policy in policies:
        ids = {
            p.id
            for p in products.values()
            if p.unit == policy.unit
            and (
                (policy.product_id and p.id == policy.product_id)
                or (policy.category_id and p.category_id == policy.category_id)
            )
        }
        available = sum(
            (
                qty
                for (product, loc), qty in counts.items()
                if product in ids
                and (not policy.location_id or loc == policy.location_id)
            ),
            Decimal(0),
        )
        if available < policy.minimum_available:
            add(
                "low_stock",
                {
                    "policy_id": policy.id,
                    "product_id": policy.product_id,
                    "category_id": policy.category_id,
                    "location_id": policy.location_id,
                    "available": str(available),
                    "minimum": str(policy.minimum_available),
                    "unit": policy.unit,
                },
                "Review verified available stock, outstanding reservations and purchase lead times; order only after approval",
                "high" if policy.critical else "medium",
            )
    for p in products.values():
        qty = sum(
            (n for (product, _), n in counts.items() if product == p.id), Decimal(0)
        )
        if p.critical and qty == 0:
            add(
                "critical_model_without_spare",
                {
                    "product_id": p.id,
                    "model": p.model,
                    "verified_available": str(qty),
                    "unit": p.unit,
                },
                "Investigate replacement compatibility and procure only after explicit approval",
                "high",
            )
        if (
            p.planned_retirement_date
            and p.planned_retirement_date <= clock().date() + timedelta(days=90)
        ):
            add(
                "model_retirement_review",
                {
                    "product_id": p.id,
                    "planned_date": p.planned_retirement_date.isoformat(),
                    "source": p.lifecycle_source,
                },
                "Verify the documented lifecycle plan and compatible replacements; this is not a vendor failure prediction",
            )
    warning_days = max((p.warranty_warning_days for p in policies), default=60)
    warranties = list(
        db.scalars(
            select(Asset)
            .where(
                Asset.state != "retired",
                Asset.warranty_end.is_not(None),
                Asset.warranty_end <= clock().date() + timedelta(days=warning_days),
            )
            .order_by(Asset.warranty_end)
            .limit(100)
        )
    )
    for a in warranties:
        add(
            "warranty_review",
            {
                "asset_id": a.id,
                "warranty_end": a.warranty_end.isoformat(),
                "expired": a.warranty_end < clock().date(),
                "review_horizon_days": warning_days,
            },
            "Review documented warranty and supplier terms; missing warranty data is unknown",
        )
    failures = db.execute(
        select(Movement.asset_id, func.count())
        .where(
            Movement.action == "lifecycle_changed",
            Movement.after["state"].as_string() == "failed",
            Movement.at >= clock() - timedelta(days=90),
        )
        .group_by(Movement.asset_id)
        .having(func.count() >= 3)
        .limit(100)
    ).all()
    for ident, count in failures:
        add(
            "repeated_documented_failures",
            {"asset_id": ident, "documented_failure_events_90d": count},
            "Review maintenance evidence and environmental causes; no guaranteed future failure is claimed",
            "high",
        )
    return {
        "items": result[:500],
        "policy_count": len(policies),
        "bounded": len(result) > 500 or len(policies) == limit,
        "unknown_condition_not_counted_as_available": True,
        "no_automatic_orders": True,
    }


def integrated_view(db, a):
    """Do not attribute a replacement's telemetry to the removed physical asset."""
    result = {
        "current_device": None,
        "availability": {
            "state": "unknown",
            "reason": "No current monitored-device binding",
        },
        "telemetry": {"state": "unavailable", "metrics": {}, "power": {}},
        "ipam_assignments": [],
        "interfaces": [],
        "active_alarms": [],
        "shared_incidents": [],
        "historical_incidents": [],
    }
    bindings = list(
        db.scalars(
            select(DeviceBinding)
            .where(DeviceBinding.asset_id == a.id)
            .order_by(DeviceBinding.id.desc())
            .limit(100)
        )
    )
    result["device_bindings"] = [view(b) for b in bindings]
    current = next((b for b in bindings if b.ended_at is None), None)
    now = clock()
    if a.device_id and current:
        d = required(db, Device, a.device_id)
        result["current_device"] = {
            k: value(getattr(d, k))
            for k in [
                "id",
                "name",
                "vendor",
                "model",
                "model_name",
                "firmware_version",
                "wisp_site_id",
                "ip_address",
                "mac_address",
                "serial_number",
            ]
        }
        result["current_device"]["identity_review_required"] = bool(
            (
                a.serial_number
                and d.serial_number
                and a.serial_number.lower() != d.serial_number.strip().casefold()
            )
            or (
                a.mac_address
                and d.mac_address
                and a.mac_address != d.mac_address.lower().replace("-", ":")
            )
        )
        if (
            d.authorized
            and d.monitor_enabled
            and d.monitor_last_checked_at
            and d.monitor_last_checked_at >= current.started_at
            and timedelta(0)
            <= now - d.monitor_last_checked_at
            <= timedelta(seconds=300)
        ):
            result["availability"] = {
                "state": (
                    d.monitor_status
                    if d.monitor_status in {"up", "down", "degraded"}
                    else "unknown"
                ),
                "observed_at": d.monitor_last_checked_at.isoformat(),
                "source": "existing NOC monitor",
                "physical_fault_established": False,
            }
        else:
            result["availability"] = {
                "state": "unknown",
                "reason": "Missing, stale, disabled, unauthorized or pre-binding monitoring evidence",
            }
        from app.ipam.models import Assignment, Interface

        result["ipam_assignments"] = [
            view(r)
            for r in db.scalars(
                select(Assignment)
                .where(
                    Assignment.device_id == d.id,
                    Assignment.state.in_(["assigned", "reserved"]),
                )
                .order_by(Assignment.id)
                .limit(100)
            )
        ]
        result["interfaces"] = [
            view(r)
            for r in db.scalars(
                select(Interface)
                .where(Interface.device_id == d.id)
                .order_by(Interface.id)
                .limit(100)
            )
        ]
        from app.tower_health.models import TowerCurrent, TowerFinding

        snapshot = db.get(TowerCurrent, d.wisp_site_id) if d.wisp_site_id else None
        equipment = (
            next(
                (
                    r
                    for r in snapshot.report.get("equipment", [])
                    if r["device_id"] == d.id
                ),
                None,
            )
            if snapshot
            else None
        )
        metrics = {}
        if equipment:
            for name, item in equipment.get("metric_states", {}).items():
                t = (
                    datetime.fromisoformat(item["observed_at"])
                    if item.get("observed_at")
                    else None
                )
                usable = bool(
                    t
                    and t >= current.started_at
                    and timedelta(0) <= now - t <= timedelta(seconds=300)
                    and item.get("state") == "available"
                )
                metrics[name] = {
                    **item,
                    "value": item.get("value") if usable else None,
                    "state": (
                        item.get("state", "missing")
                        if usable
                        else (
                            "pre_binding"
                            if t and t < current.started_at
                            else "stale" if t else "missing"
                        )
                    ),
                }
            from app.tower_health.contracts import Power
            from app.tower_health.analytics import power

            power_states = {
                k: v
                for k, v in metrics.items()
                if k in Power.model_fields and v["state"] == "available"
            }
            latest = max(
                (v["observed_at"] for v in power_states.values()), default=None
            )
            paired = {
                k: v["value"]
                for k, v in power_states.items()
                if v["observed_at"] == latest
            }
            result["telemetry"] = {
                "state": (
                    "recorded_metrics"
                    if any(v["state"] == "available" for v in metrics.values())
                    else "unavailable"
                ),
                "metrics": metrics,
                "power": power(paired),
                "source": "Existing NOC-98 derived telemetry; no new equipment collection",
                "unknown_is_not_healthy": True,
            }
        findings = list(
            db.scalars(
                select(TowerFinding)
                .where(
                    TowerFinding.device_id == d.id,
                    TowerFinding.state.in_(["active", "acknowledged"]),
                )
                .order_by(TowerFinding.id.desc())
                .limit(100)
            )
        )
        for f in findings:
            at = f.evidence.get("observed_at")
            if at and datetime.fromisoformat(at) >= current.started_at:
                result["active_alarms"].append(
                    {
                        "id": f.id,
                        "rule": f.rule,
                        "state": f.state,
                        "priority": f.priority,
                        "last_seen": f.last_seen.isoformat(),
                        "evidence_fresh": timedelta(0)
                        <= now - datetime.fromisoformat(at)
                        <= timedelta(seconds=300),
                    }
                )
        result["shared_incidents"] = [
            {
                "id": i.id,
                "title": i.title,
                "status": i.status,
                "started_at": i.started_at.isoformat(),
                "physical_cause_verified": False,
            }
            for i in db.scalars(
                select(Incident)
                .where(
                    Incident.device_id == d.id,
                    Incident.status.not_in(["resolved", "closed"]),
                )
                .order_by(Incident.id.desc())
                .limit(50)
            )
        ]
    if bindings:
        clauses = [
            and_(
                Incident.device_id == b.device_id,
                Incident.started_at >= b.started_at,
                Incident.started_at < (b.ended_at or now),
            )
            for b in bindings
        ]
        result["historical_incidents"] = [
            {
                "id": i.id,
                "title": i.title,
                "status": i.status,
                "started_at": i.started_at.isoformat(),
                "scope": "Logical-device investigation during recorded binding; physical cause unverified",
            }
            for i in db.scalars(
                select(Incident)
                .where(or_(*clauses))
                .order_by(Incident.id.desc())
                .limit(100)
            )
        ]
    return result


def detail(db, a, financial=False):
    p = required(db, Product, a.product_id)
    maker = required(db, Manufacturer, p.manufacturer_id)
    category = required(db, Category, p.category_id)
    location = required(db, Location, a.location_id) if a.location_id else None
    now = clock().date()
    age_basis = a.purchase_date or (a.installed_at.date() if a.installed_at else None)
    result = view(a) | {
        "product": view(p) | {"manufacturer": maker.name, "category": category.name},
        "location": view(location) if location else None,
        "age_days": (now - age_basis).days if age_basis else None,
        "age_basis": (
            "purchase_date"
            if a.purchase_date
            else "installation_date" if a.installed_at else "unknown"
        ),
        "warranty_status": (
            "unknown"
            if not a.warranty_end
            else "expired" if a.warranty_end < now else "documented_coverage"
        ),
        "qr_url": "/?asset=" + str(a.public_id),
        "qr_requires_authentication": True,
        "history": [
            view(r)
            for r in db.scalars(
                select(Movement)
                .where(Movement.asset_id == a.id)
                .order_by(Movement.id.desc())
                .limit(100)
            )
        ],
        "maintenance": [
            view(r)
            for r in db.scalars(
                select(Maintenance)
                .where(Maintenance.asset_id == a.id)
                .order_by(Maintenance.id.desc())
                .limit(100)
            )
        ],
        "replacement_assets": [
            view(r)
            for r in db.scalars(
                select(Asset).where(Asset.replacement_of_id == a.id).limit(100)
            )
        ],
        **integrated_view(db, a),
    }
    if financial:
        result["financial"] = financial_view(db, a)
    return result


def dashboard(db, financial=False):
    by_state = dict(
        db.execute(select(Asset.state, func.count()).group_by(Asset.state)).all()
    )
    by_maker = db.execute(
        select(Manufacturer.name, func.count(Asset.id))
        .join(Asset, Asset.manufacturer_id == Manufacturer.id)
        .group_by(Manufacturer.name)
    ).all()
    by_category = db.execute(
        select(Category.name, func.count(Asset.id))
        .join(Product, Product.category_id == Category.id)
        .join(Asset, Asset.product_id == Product.id)
        .group_by(Category.name)
    ).all()
    by_site = db.execute(
        select(Location.site_id, func.count(Asset.id))
        .join(Asset, Asset.location_id == Location.id)
        .where(Location.site_id.is_not(None))
        .group_by(Location.site_id)
    ).all()
    result = {
        "asset_count": sum(by_state.values()),
        "by_state": by_state,
        "by_manufacturer": [{"name": name, "count": n} for name, n in by_maker],
        "by_category": [{"name": name, "count": n} for name, n in by_category],
        "by_tower": [{"site_id": ident, "count": n} for ident, n in by_site],
        "verified_available_spares": db.scalar(
            select(func.count())
            .select_from(Asset)
            .where(Asset.state == "available", Asset.condition.in_(SERVICEABLE))
        ),
        "unknown_serial_count": db.scalar(
            select(func.count()).select_from(Asset).where(Asset.serial_number.is_(None))
        ),
        "unknown_warranty_count": db.scalar(
            select(func.count()).select_from(Asset).where(Asset.warranty_end.is_(None))
        ),
        "recent_movements": [
            view(r)
            for r in db.scalars(select(Movement).order_by(Movement.id.desc()).limit(20))
        ],
        "read_only_network": True,
        "automatic_ordering": False,
        "financial_access": financial,
    }
    if financial:
        result["costs"] = cost_report(db)
    return result


ASSET_COLUMNS = set(AssetInput.model_fields) - {"reason"}
FIN_COLUMNS = set(FinancialInput.model_fields) - {"reason"}
INTS = {"product_id", "device_id", "location_id", "technician_id", "useful_life_months"}


def parse_csv(value, reason, financial=False):
    reader = csv.DictReader(io.StringIO(value))
    headers = reader.fieldnames
    if (
        not headers
        or len(set(headers)) != len(headers)
        or set(headers) - (ASSET_COLUMNS | FIN_COLUMNS)
    ):
        raise ValueError("Unknown/duplicate CSV columns")
    if not financial and set(headers) & FIN_COLUMNS:
        raise PermissionError("Administrator required for financial import fields")
    rows = []
    for raw in reader:
        if None in raw or len(rows) >= 300:
            raise ValueError("Malformed CSV or maximum300 rows exceeded")
        data = {
            k: (int(v) if k in INTS else v)
            for k, v in raw.items()
            if v not in ("", None)
        }
        money = {k: v for k, v in data.items() if k in FIN_COLUMNS}
        asset = {k: v for k, v in data.items() if k in ASSET_COLUMNS}
        if not any(
            asset.get(k)
            for k in ["asset_tag", "device_id", "serial_number", "mac_address"]
        ):
            raise ValueError(
                "Imports require a stable asset/device/serial/MAC identity"
            )
        b = AssetInput(**asset, reason=reason)
        f = FinancialInput(**money, reason=reason) if money else None
        rows.append(
            {
                "asset": b.model_dump(mode="json"),
                "financial": f.model_dump(mode="json") if f else None,
            }
        )
    if not rows:
        raise ValueError("No import rows")
    return rows


def preview_rows(db, rows, actor, source):
    lock(db)
    errors = []
    save = db.begin_nested()
    from sqlalchemy.exc import IntegrityError

    try:
        for index, row in enumerate(rows):
            try:
                with db.begin_nested():
                    device_id = row["asset"].get("device_id")
                    if device_id and db.scalar(
                        select(DeviceBinding.id)
                        .where(DeviceBinding.device_id == device_id)
                        .limit(1)
                    ):
                        raise ValueError(
                            "Existing physical binding history requires explicit replacement/reinstallation review"
                        )
                    a = create_asset(db, AssetInput.model_validate(row["asset"]), actor)
                    if row.get("financial"):
                        set_financial(
                            db,
                            a,
                            FinancialInput.model_validate(row["financial"]),
                            actor,
                        )
            except (ValueError, IntegrityError):
                errors.append(
                    {
                        "row": index + 1,
                        "error": "Invalid lifecycle/location/product mapping or duplicate device, tag, serial or MAC; no existing asset overwritten",
                    }
                )
    finally:
        save.rollback()
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    batch = ImportBatch(
        actor_id=actor, source=source, digest=digest, rows=rows, errors=errors
    )
    db.add(batch)
    db.flush()
    audit(
        db,
        actor,
        "import_preview",
        "equipment_import",
        batch.id,
        {"rows": len(rows), "errors": len(errors)},
    )
    return {
        "id": batch.id,
        "digest": digest,
        "row_count": len(rows),
        "errors": errors,
        "dry_run": True,
        "requires_approval": True,
    }


def csv_preview(db, b, actor, financial=False):
    try:
        rows = parse_csv(b.csv_text, b.reason, financial)
    except PermissionError:
        raise
    except Exception:
        raise ValueError("Invalid CSV schema/values; no inventory imported") from None
    return preview_rows(db, rows, actor, "csv")


def device_preview(db, b, actor):
    product = required(db, Product, b.product_id)
    maker = required(db, Manufacturer, product.manufacturer_id)
    rows = []
    for ident in b.device_ids:
        d = required(db, Device, ident)
        if d.vendor and d.vendor.strip().casefold() != maker.key:
            raise ValueError(
                "Selected catalog manufacturer conflicts with existing device"
            )
        if (d.model or d.model_name) and (
            d.model or d.model_name
        ).strip().casefold() != product.model_key:
            raise ValueError(
                "Selected catalog model conflicts with device; reconcile deliberately instead of overwriting"
            )
        warnings = []
        try:
            serial = AssetInput.serial(d.serial_number)
        except ValueError:
            serial = None
            warnings.append("source_serial_unavailable")
        try:
            mac = AssetInput.mac(d.mac_address)
        except ValueError:
            mac = None
            warnings.append("source_physical_mac_unavailable")
        data = AssetInput(
            product_id=product.id,
            device_id=d.id,
            asset_tag=d.asset_tag,
            serial_number=serial,
            mac_address=mac,
            location_id=b.location_id,
            state=b.state,
            recorded_firmware=d.firmware_version,
            reason=b.reason,
        )
        rows.append(
            {
                "asset": data.model_dump(mode="json"),
                "financial": None,
                "warnings": warnings,
            }
        )
    return preview_rows(db, rows, actor, "noc_inventory")


def apply_import(db, ident, b, actor, financial=False):
    lock(db)
    batch = required(db, ImportBatch, ident)
    if (
        not b.approved
        or batch.state != "preview"
        or b.digest != batch.digest
        or batch.errors
        or clock() - batch.created_at > timedelta(hours=24)
    ):
        raise ValueError(
            "Explicit current approval of unchanged error-free preview required"
        )
    if any(r.get("financial") for r in batch.rows) and not financial:
        raise PermissionError("Administrator required for financial import")
    ids = []
    for row in batch.rows:
        device_id = row["asset"].get("device_id")
        if device_id and db.scalar(
            select(DeviceBinding.id)
            .where(DeviceBinding.device_id == device_id)
            .limit(1)
        ):
            raise ValueError(
                "Device binding changed; review replacement/reinstallation rather than importing a duplicate"
            )
        a = create_asset(
            db, AssetInput.model_validate({**row["asset"], "reason": b.reason}), actor
        )
        if row.get("financial"):
            set_financial(
                db,
                a,
                FinancialInput.model_validate({**row["financial"], "reason": b.reason}),
                actor,
            )
        ids.append(a.id)
    batch.state = "applied"
    batch.applied_at = clock()
    batch.applied_ids = ids
    audit(
        db, actor, "import_applied", "equipment_import", batch.id, {"assets": len(ids)}
    )
    return {
        "created_assets": len(ids),
        "asset_ids": ids,
        "equipment_configuration_changes": 0,
    }


def asset_query(
    *,
    q="",
    state=None,
    site_id=None,
    category_id=None,
    product_id=None,
    location_id=None,
    manufacturer_id=None,
):
    query = (
        select(Asset)
        .join(Product, Product.id == Asset.product_id)
        .join(Manufacturer, Manufacturer.id == Asset.manufacturer_id)
        .outerjoin(Location, Location.id == Asset.location_id)
    )
    for col, val in [
        (Asset.state, state),
        (Location.site_id, site_id),
        (Product.category_id, category_id),
        (Asset.product_id, product_id),
        (Asset.location_id, location_id),
        (Asset.manufacturer_id, manufacturer_id),
    ]:
        if val is not None:
            query = query.where(col == val)
    if q:
        query = query.where(
            or_(
                *[
                    col.icontains(q, autoescape=True)
                    for col in [
                        Asset.asset_tag,
                        Asset.serial_number,
                        Asset.mac_address,
                        Product.model,
                        Manufacturer.name,
                    ]
                ]
            )
        )
    return query


def export_assets(db, query, offset=0, financial=False):
    rows = list(db.scalars(query.order_by(Asset.id).offset(offset).limit(1000)))
    finances = (
        {
            r.asset_id: r
            for r in db.scalars(
                select(Financial).where(Financial.asset_id.in_([a.id for a in rows]))
            )
        }
        if financial
        else {}
    )
    fields = [
        "asset_tag",
        "public_id",
        "product_id",
        "serial_number",
        "mac_address",
        "device_id",
        "location_id",
        "state",
        "condition",
        "recorded_firmware",
        "purchase_date",
        "warranty_end",
    ]
    money = (
        [
            "currency",
            "purchase_cost",
            "replacement_estimate",
            "installation_labor_estimate",
            "salvage_value",
            "depreciation_method",
            "useful_life_months",
        ]
        if financial
        else []
    )
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(fields + money)
    for a in rows:
        data = view(a)
        f = view(finances[a.id]) if a.id in finances else {}
        vals = [data.get(k) for k in fields] + [f.get(k) for k in money]
        writer.writerow(
            [
                (
                    "'" + v
                    if isinstance(v, str)
                    and v.lstrip().startswith(("=", "+", "-", "@"))
                    else v
                )
                for v in vals
            ]
        )
    return out.getvalue(), offset + 1000 if len(rows) == 1000 else None


def patch_metadata(db, a, b, actor):
    lock(db)
    db.refresh(a)
    if a.revision != b.expected_revision or a.state == "retired":
        raise ValueError("Asset revision changed or historical asset retired")
    updates = b.model_dump(exclude={"reason", "expected_revision"}, exclude_unset=True)
    clean(updates)
    allowed = set(AssetInput.model_fields) - {"reason"}
    fields = {key: getattr(a, key) for key in allowed if hasattr(a, key)}
    candidate = AssetInput(**{**fields, **updates, "reason": b.reason})
    p = required(db, Product, a.product_id)
    if any(
        row.id != a.id
        for row in identity_matches(
            db, p, candidate.serial_number, candidate.mac_address, a.asset_tag
        )
    ):
        raise ValueError("Updated identity duplicates another physical asset")
    before = view(a)
    for key in updates:
        setattr(a, key, getattr(candidate, key))
    a.serial_key = a.serial_number.lower() if a.serial_number else None
    a.revision += 1
    a.updated_at = clock()
    db.flush()
    record(db, a, actor, "metadata_updated", b.reason, before)
    return a


def product_planning(db, p, b, actor):
    lock(db)
    before = view(p)
    data = b.model_dump(exclude={"reason"}, exclude_unset=True)
    clean(data)
    if data.get("critical") is None:
        data.pop("critical", None)
    for key, v in data.items():
        setattr(p, key, v)
    if p.planned_retirement_date and not p.lifecycle_source:
        raise ValueError("Document lifecycle planning source")
    db.flush()
    audit(
        db,
        actor,
        "product_planning_updated",
        "equipment_product",
        p.id,
        {"before": before, "after": view(p), "reason": b.reason},
    )
    return p


def maintenance_status(db, m, b, actor):
    lock(db)
    db.refresh(m)
    clean(b.model_dump())
    if m.status != b.expected_status or m.status == "completed":
        raise ValueError("Maintenance status changed or completed history is immutable")
    if b.status == "planned" and m.status != "planned":
        raise ValueError("Do not silently reset completed field work to a plan")
    before = {"maintenance_id": m.id, "status": m.status, "follow_up": m.follow_up}
    m.status = b.status
    if "follow_up" in b.model_fields_set:
        m.follow_up = b.follow_up
    db.flush()
    db.add(
        Movement(
            asset_id=m.asset_id,
            actor_id=actor,
            action="maintenance_status_changed",
            reason=b.reason,
            before=before,
            after={
                "maintenance_id": m.id,
                "status": m.status,
                "follow_up": m.follow_up,
            },
        )
    )
    audit(db, actor, "maintenance_status_changed", "equipment_maintenance", m.id)
    return m
