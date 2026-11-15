"""Field operations only document NOC state; inventory owns physical changes."""

import hashlib, json, re
from io import BytesIO
from urllib.parse import urlsplit
from sqlalchemy import select, func, text, or_, and_
from app.core.config import settings
from app.models.entities import Site, Device, User, Incident, NetworkLink, AuditLog
from app.equipment import service as inventory
from app.equipment.models import Asset, Location, BulkMovement, Product, Maintenance
from app.equipment.contracts import MaintenanceInput
from app.tower_health.models import TowerFinding, TowerCurrent
from .models import (
    QRLabel,
    Grant,
    Template,
    WorkOrder,
    Event,
    Inspection,
    Receipt,
    Attachment,
    now,
)

TERMINAL = {"completed", "cancelled"}
TRANSITIONS = {
    "open": {"assigned", "scheduled", "cancelled"},
    "assigned": {"scheduled", "in_progress", "blocked", "cancelled"},
    "scheduled": {"assigned", "in_progress", "blocked", "cancelled"},
    "in_progress": {"waiting_for_parts", "blocked", "completed", "cancelled"},
    "waiting_for_parts": {"in_progress", "blocked", "cancelled"},
    "blocked": {"assigned", "scheduled", "in_progress", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}
DEFAULT_ITEMS = {
    "structure": [
        "Visible structural damage",
        "Corrosion",
        "Mounting hardware",
        "Antenna mounting",
        "Grounding and bonding",
        "Cable support and weatherproofing",
    ],
    "power": [
        "AC power status",
        "DC supply status",
        "UPS and battery condition",
        "Surge protection",
        "Cabinet ventilation",
        "Visible wiring problems",
    ],
    "network": [
        "Router and switch condition",
        "Backhaul radio condition",
        "Access point condition",
        "Ethernet and fiber connections",
        "Cable labeling",
        "Cabinet organization",
    ],
    "site": [
        "Water intrusion",
        "Animal damage",
        "Security concerns",
        "Access problems",
        "Vegetation or physical obstructions",
    ],
}


def default_items():
    return [
        {"key": f"{g}_{i}", "label": label, "group": g}
        for g, labels in DEFAULT_ITEMS.items()
        for i, label in enumerate(labels)
    ]


def get(db, cls, id):
    row = db.get(cls, id)
    if row is None:
        raise LookupError("Record unavailable")
    return row


def audit(db, actor, action, entity, ident, details=None):
    db.add(
        AuditLog(
            user_id=actor,
            action="field." + action,
            entity_type=entity,
            entity_id=str(ident),
            details={"documentation_only": True, **(details or {})},
        )
    )


def admin(u):
    if u.role != "admin":
        raise PermissionError("Administrator required")


def tech(db, id):
    u = get(db, User, id)
    if not u.is_active or u.role not in {"technician", "admin"}:
        raise ValueError("Active technician/admin required")
    return u


def scope(db, u):
    return (
        select(Grant.site_id)
        .where(Grant.technician_id == u.id, Grant.active.is_(True))
        .union(
            select(WorkOrder.site_id).where(
                WorkOrder.technician_id == u.id, WorkOrder.status.notin_(TERMINAL)
            )
        )
    )


def site_access(db, u, id):
    site = get(db, Site, id)
    if u.role == "technician" and not db.scalar(
        select(Site.id).where(Site.id == id, Site.id.in_(scope(db, u)))
    ):
        raise PermissionError("Site not assigned")
    return site


def work_access(db, u, id, write=False):
    w = get(db, WorkOrder, id)
    if u.role == "technician" and w.technician_id != u.id:
        raise PermissionError("Work order not assigned")
    if write and (
        u.role not in {"admin", "technician"}
        or (u.role == "technician" and w.technician_id != u.id)
    ):
        raise PermissionError("Field write not authorized")
    return w


def asset_site(db, a):
    loc = db.get(Location, a.location_id) if a.location_id else None
    return loc.site_id if loc else None


def asset_access(db, u, id):
    a = get(db, Asset, id)
    if u.role == "technician":
        assigned = db.scalar(
            select(WorkOrder.id).where(
                WorkOrder.asset_id == id, WorkOrder.technician_id == u.id
            )
        )
        site = asset_site(db, a)
        if not assigned:
            if not site:
                raise PermissionError("Asset not assigned")
            site_access(db, u, site)
    return a


def mutation(db, u, action, b, fn):
    # Always acquire the inventory lock first: replacements/parts use that service.
    inventory.lock(db)
    db.execute(text("SELECT pg_advisory_xact_lock(1000101)"))
    payload = b.model_dump(mode="json")
    inventory.clean(payload)
    digest = hashlib.sha256(
        json.dumps(
            {"action": action, "body": payload}, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    key = str(b.operation_key)
    old = db.scalar(
        select(Receipt).where(Receipt.actor_id == u.id, Receipt.operation_key == key)
    )
    if old:
        if old.digest != digest:
            raise ValueError("Operation key already used for different submission")
        return old.response
    result = fn()
    db.flush()
    db.add(Receipt(actor_id=u.id, operation_key=key, digest=digest, response=result))
    audit(
        db,
        u.id,
        action,
        "field_operations",
        result.get("id", "batch"),
        {"reason": b.reason},
    )
    return result


def event(db, w, u, kind, reason, data):
    db.add(
        Event(work_order_id=w.id, actor_id=u.id, kind=kind, reason=reason, data=data)
    )
    w.updated_at = now()


def work_view(w):
    return inventory.view(w)


def revision(w, expected):
    if w.revision != expected:
        raise ValueError("Work order changed; refresh before retry")
    if w.status in TERMINAL:
        raise ValueError("Work order is closed")


def instant(value):
    if value is not None and value.tzinfo is None:
        raise ValueError("Timezone-aware schedule required")


def label(db, u, b):
    admin(u)
    get(db, Site, b.site_id) if b.site_id else get(db, Asset, b.asset_id)
    q = select(QRLabel).where(
        QRLabel.revoked_at.is_(None),
        QRLabel.site_id == b.site_id if b.site_id else QRLabel.asset_id == b.asset_id,
    )
    old = db.scalar(q)
    if old and not b.regenerate:
        return inventory.view(old)
    if old:
        old.revoked_at = now()
        db.flush()
    row = QRLabel(site_id=b.site_id, asset_id=b.asset_id, created_by=u.id)
    db.add(row)
    db.flush()
    return inventory.view(row)


def revoke(db, u, id):
    admin(u)
    r = get(db, QRLabel, id)
    if r.revoked_at is None:
        r.revoked_at = now()
    return inventory.view(r)


def origin():
    raw = settings.field_public_base_url.rstrip("/")
    p = urlsplit(raw)
    if (
        p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.query
        or p.fragment
        or p.path
        or p.port not in {None, 443}
    ):
        raise ValueError("Configure FIELD_PUBLIC_BASE_URL as an approved HTTPS origin")
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", p.hostname):
        raise ValueError("Invalid HTTPS host")
    return raw


def label_url(row):
    return origin() + "/?qr=" + row.identifier


def label_svg(row):
    import qrcode
    from qrcode.image.svg import SvgPathImage

    qr = qrcode.make(
        label_url(row),
        image_factory=SvgPathImage,
        border=4,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
    )
    out = BytesIO()
    qr.save(out)
    return out.getvalue()


def resolve(db, u, identifier):
    row = db.scalar(
        select(QRLabel).where(
            QRLabel.identifier == str(identifier), QRLabel.revoked_at.is_(None)
        )
    )
    if row is None:
        raise LookupError("QR identifier unavailable or revoked")
    if row.site_id:
        site_access(db, u, row.site_id)
    else:
        asset_access(db, u, row.asset_id)
    return {
        "kind": "tower" if row.site_id else "asset",
        "site_id": row.site_id,
        "asset_id": row.asset_id,
        "label_id": row.id,
    }


def grant(db, u, b):
    admin(u)
    get(db, Site, b.site_id)
    tech(db, b.technician_id)
    row = db.scalar(
        select(Grant).where(
            Grant.site_id == b.site_id, Grant.technician_id == b.technician_id
        )
    )
    if row is None:
        row = Grant(site_id=b.site_id, technician_id=b.technician_id, updated_by=u.id)
        db.add(row)
    row.active = b.active
    row.updated_by = u.id
    row.updated_at = now()
    db.flush()
    return inventory.view(row)


def template(db, u, b):
    admin(u)
    version = (
        db.scalar(select(func.max(Template.version)).where(Template.code == b.code))
        or 0
    ) + 1
    for old in db.scalars(
        select(Template).where(Template.code == b.code, Template.active.is_(True))
    ):
        old.active = False
    row = Template(
        code=b.code,
        version=version,
        name=b.name,
        items=[x.model_dump() for x in b.items],
        created_by=u.id,
    )
    db.add(row)
    db.flush()
    return inventory.view(row)


def related_incident(db, site, id):
    i = get(db, Incident, id)
    related = i.site_id == site
    if i.device_id:
        related = related or get(db, Device, i.device_id).wisp_site_id == site
    if i.link_id:
        link = get(db, NetworkLink, i.link_id)
        related = related or site in {link.a_site_id, link.z_site_id}
    if not related:
        raise ValueError("Incident belongs to another tower")
    return i


def create_work(db, u, b):
    admin(u)
    get(db, Site, b.site_id)
    instant(b.scheduled_at)
    if b.asset_id and asset_site(db, get(db, Asset, b.asset_id)) != b.site_id:
        raise ValueError("Asset is not at selected tower")
    if b.incident_id:
        related_incident(db, b.site_id, b.incident_id)
    if b.finding_id and get(db, TowerFinding, b.finding_id).site_id != b.site_id:
        raise ValueError("Finding belongs to another tower")
    if b.template_id and not get(db, Template, b.template_id).active:
        raise ValueError("Select active template")
    if b.technician_id:
        tech(db, b.technician_id)
    if b.scheduled_at and not b.technician_id:
        raise ValueError("Schedule requires technician")
    row = WorkOrder(
        **b.model_dump(exclude={"operation_key", "reason"}),
        created_by=u.id,
        status=(
            "scheduled" if b.scheduled_at else "assigned" if b.technician_id else "open"
        ),
    )
    db.add(row)
    db.flush()
    event(db, row, u, "created", b.reason, {"status": row.status})
    return work_view(row)


def completion(db, w, notes, follow_up):
    if not notes or len(notes) < 10:
        raise ValueError("Completion work details required")
    if w.template_id and not db.scalar(
        select(Inspection.id).where(Inspection.work_order_id == w.id)
    ):
        raise ValueError("Required inspection incomplete")
    flagged = db.scalar(
        select(Inspection.id).where(
            Inspection.work_order_id == w.id, Inspection.qualified_review.is_(True)
        )
    )
    if flagged and not follow_up:
        raise ValueError("Qualified structural/electrical review follow-up required")
    w.completed_at = now()
    w.completion_notes = notes
    w.follow_up = follow_up


def update_work(db, u, w, b):
    db.refresh(w)
    revision(w, b.expected_revision)
    if u.role == "admin":
        if b.technician_id is not None:
            tech(db, b.technician_id)
            w.technician_id = b.technician_id
        if b.scheduled_at is not None:
            instant(b.scheduled_at)
            w.scheduled_at = b.scheduled_at
    elif b.technician_id is not None or b.scheduled_at is not None:
        raise PermissionError("Administrator manages assignments/schedules")
    if b.status != w.status and b.status not in TRANSITIONS[w.status]:
        raise ValueError("Invalid work-order transition")
    if (
        b.status
        in {
            "assigned",
            "scheduled",
            "in_progress",
            "waiting_for_parts",
            "blocked",
            "completed",
        }
        and not w.technician_id
    ):
        raise ValueError("Technician assignment required")
    if b.status == "scheduled" and not w.scheduled_at:
        raise ValueError("Schedule required")
    if b.status == "completed":
        completion(db, w, b.notes, b.follow_up)
    if b.status in {"blocked", "waiting_for_parts"} and not b.follow_up:
        raise ValueError("Follow-up required")
    old = w.status
    w.status = b.status
    w.follow_up = b.follow_up
    w.revision += 1
    event(
        db,
        w,
        u,
        "status_changed",
        b.reason,
        {
            "before": old,
            "after": w.status,
            "notes": b.notes,
            "technician_id": w.technician_id,
            "scheduled_at": w.scheduled_at.isoformat() if w.scheduled_at else None,
            "follow_up": w.follow_up,
        },
    )
    db.flush()
    return work_view(w)


def note(db, u, w, b):
    db.refresh(w)
    revision(w, b.expected_revision)
    w.revision += 1
    event(db, w, u, "technician_note", b.reason, {"notes": b.notes})
    db.flush()
    return work_view(w)


def inspect(db, u, w, b):
    db.refresh(w)
    revision(w, b.expected_revision)
    if w.status != "in_progress":
        raise ValueError("Start work before inspection")
    t = get(db, Template, b.template_id)
    if w.template_id and w.template_id != t.id:
        raise ValueError("Use work-order template version")
    if not t.active and w.template_id != t.id:
        raise ValueError("Template inactive")
    keys = [r.key for r in b.results]
    if len(keys) != len(set(keys)) or set(keys) != {i["key"] for i in t.items}:
        raise ValueError("Every snapshot checklist item must be answered exactly once")
    result = [r.model_dump() for r in b.results]
    failed = {r["key"] for r in result if r["outcome"] == "fail"}
    qualified = any(
        i["key"] in failed and i["group"] in {"structure", "power"} for i in t.items
    )
    row = Inspection(
        work_order_id=w.id,
        template_id=t.id,
        asset_id=w.asset_id,
        technician_id=u.id,
        inspected_at=b.inspected_at,
        snapshot={
            "name": t.name,
            "code": t.code,
            "version": t.version,
            "items": t.items,
        },
        results=result,
        notes=b.notes,
        qualified_review=qualified,
    )
    if w.asset_id:
        maintenance = inventory.add_maintenance(
            db,
            get(db, Asset, w.asset_id),
            MaintenanceInput(
                inspected_at=b.inspected_at,
                work_performed=b.notes,
                technician_id=u.id,
                follow_up=(
                    "Qualified structural/electrical review required; no safety certification"
                    if qualified
                    else None
                ),
                reason=b.reason,
            ),
            u.id,
        )
        row.equipment_maintenance_id = maintenance.id
    db.add(row)
    w.revision += 1
    db.flush()
    event(
        db,
        w,
        u,
        "inspection_recorded",
        b.reason,
        {"inspection_id": row.id, "qualified_review": qualified},
    )
    return inventory.view(row)


def replacement(db, u, w, b):
    db.refresh(w)
    revision(w, b.expected_revision)
    if w.status != "in_progress":
        raise ValueError("Start assigned work before replacement")
    old = get(db, Asset, b.old_asset_id)
    if (w.asset_id and w.asset_id != old.id) or asset_site(db, old) != w.site_id:
        raise ValueError("Old asset does not match work order/tower")
    spare = get(db, Asset, b.replacement.new_asset_id)
    if u.role == "technician" and spare.technician_id not in {None, u.id}:
        raise PermissionError("Spare assigned to another technician")
    result = inventory.replace_asset(db, old, b.replacement, u.id)
    old.disposition = b.disposition
    event(
        db,
        w,
        u,
        "equipment_replaced",
        b.reason,
        {
            "old_asset_id": old.id,
            "new_asset_id": b.replacement.new_asset_id,
            "installation_notes": b.installation_notes,
            "removal_notes": b.removal_notes,
            "removed_disposition": b.disposition,
        },
    )
    w.revision += 1
    if b.complete:
        completion(db, w, b.installation_notes, b.follow_up)
        w.status = "completed"
        event(
            db,
            w,
            u,
            "status_changed",
            b.reason,
            {"after": "completed", "source": "documented_replacement"},
        )
    db.flush()
    return {"id": w.id, "work_order": work_view(w), "inventory": result}


def parts(db, u, w, b):
    db.refresh(w)
    revision(w, b.expected_revision)
    if w.status != "in_progress":
        raise ValueError("Start work before consuming stock")
    m = b.movement
    dest = get(db, Location, m.to_location_id)
    if m.action != "install" or dest.site_id != w.site_id:
        raise ValueError("Explicit installation at work tower required")
    if u.role == "technician":
        source = get(db, Location, m.from_location_id)
        if source.kind != "technician_stock" or source.technician_id != u.id:
            raise PermissionError(
                "Use assigned technician stock; warehouse transfers require administrator"
            )
    result = inventory.bulk_move(db, m, u.id)
    w.revision += 1
    event(
        db,
        w,
        u,
        "parts_consumed",
        b.reason,
        {
            "bulk_movement_id": result["movement"]["id"],
            "product_id": m.product_id,
            "quantity": str(m.quantity),
            "unit": get(db, Product, m.product_id).unit,
        },
    )
    db.flush()
    return {"id": w.id, "work_order": work_view(w), "movement": result}


def upload(db, u, w, b):
    if w.status in TERMINAL:
        raise ValueError("Closed work order attachments immutable")
    if b.restricted_financial:
        raise ValueError("Use NOC100 restricted financial attachment workflow")
    if b.inspection_id and get(db, Inspection, b.inspection_id).work_order_id != w.id:
        raise ValueError("Inspection belongs to another work order")
    if (
        db.scalar(
            select(func.count())
            .select_from(Attachment)
            .where(Attachment.work_order_id == w.id)
        )
        >= 20
    ):
        raise ValueError("Maximum twenty attachments per work order")
    raw = inventory.verify_file(b)
    row = Attachment(
        work_order_id=w.id,
        inspection_id=b.inspection_id,
        actor_id=u.id,
        filename=b.filename,
        media_type=b.media_type,
        size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        data=raw,
    )
    db.add(row)
    db.flush()
    event(
        db,
        w,
        u,
        "attachment_added",
        b.reason,
        {"attachment_id": row.id, "size": row.size},
    )
    return inventory.view(row, exclude={"data"}) | {"unscanned": True}


def work_detail(db, u, w, offset=0):
    result = work_view(w)
    result["events"] = [
        inventory.view(e)
        for e in db.scalars(
            select(Event)
            .where(Event.work_order_id == w.id)
            .order_by(Event.id.desc())
            .offset(offset)
            .limit(50)
        )
    ]
    result["next_offset"] = offset + 50 if len(result["events"]) == 50 else None
    result["inspections"] = [
        inventory.view(i)
        for i in db.scalars(
            select(Inspection)
            .where(Inspection.work_order_id == w.id)
            .order_by(Inspection.id.desc())
            .limit(50)
        )
    ]
    result["attachments"] = [
        inventory.view(a, exclude={"data"}) | {"unscanned": True}
        for a in db.scalars(
            select(Attachment)
            .where(Attachment.work_order_id == w.id)
            .order_by(Attachment.id)
            .limit(20)
        )
    ]
    if w.incident_id:
        incident = get(db, Incident, w.incident_id)
        result["incident"] = {
            "id": incident.id,
            "title": incident.title,
            "status": incident.status,
            "severity": incident.severity,
        }
    if w.template_id:
        result["template"] = inventory.view(get(db, Template, w.template_id))
    return result


def tower(db, u, id, offset=0):
    site = site_access(db, u, id)
    assets = db.scalars(
        select(Asset)
        .join(Location, Location.id == Asset.location_id)
        .where(Location.site_id == id)
        .order_by(Asset.id)
        .offset(offset)
        .limit(50)
    ).all()
    findings = db.scalars(
        select(TowerFinding)
        .where(TowerFinding.site_id == id, TowerFinding.state == "active")
        .order_by(TowerFinding.id.desc())
        .limit(50)
    ).all()
    health = db.get(TowerCurrent, id)
    health_fresh = bool(health and (now() - health.assessed_at).total_seconds() <= 300)
    monitoring = {
        "state": health.report.get("state", "unknown") if health_fresh else "unknown",
        "assessed_at": health.assessed_at.isoformat() if health else None,
        "fresh": health_fresh,
        "coverage": health.report.get("coverage") if health_fresh else None,
    }
    orders = select(WorkOrder).where(WorkOrder.site_id == id)
    if u.role == "technician":
        orders = orders.where(WorkOrder.technician_id == u.id)
    return {
        "id": site.id,
        "name": site.name,
        "site_type": site.site_type,
        "address": site.address,
        "latitude": site.latitude,
        "longitude": site.longitude,
        "monitoring": monitoring,
        "status": site.status,
        "status_basis": "documented inventory; monitoring freshness shown separately",
        "assets": [
            {
                "id": a.id,
                "asset_tag": a.asset_tag,
                "state": a.state,
                "recorded_firmware": a.recorded_firmware,
            }
            for a in assets
        ],
        "next_offset": offset + 50 if len(assets) == 50 else None,
        "alarms": [
            {
                "id": f.id,
                "rule": f.rule,
                "priority": f.priority,
                "last_seen": f.last_seen.isoformat(),
                "incident_id": f.incident_id,
                "evidence_fresh": (now() - f.last_seen).total_seconds() <= 300,
            }
            for f in findings
        ],
        "work_orders": [
            work_view(w)
            for w in db.scalars(orders.order_by(WorkOrder.id.desc()).limit(50))
        ],
        "safety": "Inspection observations do not certify structural/electrical safety",
    }


def asset_detail(db, u, id):
    a = asset_access(db, u, id)
    d = inventory.detail(db, a, False)
    # Financials/customer mappings/private documents never travel through field API.
    keys = [
        "id",
        "public_id",
        "asset_tag",
        "state",
        "condition",
        "recorded_firmware",
        "hardware_revision",
        "installed_at",
        "location",
        "product",
        "availability",
        "telemetry",
        "device_bindings",
        "shared_incidents",
        "historical_incidents",
    ]
    return {k: d.get(k) for k in keys} | {
        "site_id": asset_site(db, a),
        "revision": a.revision,
        "history": [
            {
                k: r[k]
                for k in ["id", "action", "actor_id", "reason", "created_at"]
                if k in r
            }
            for r in d["history"][:50]
        ],
        "maintenance": [
            inventory.view(m)
            for m in db.scalars(
                select(Maintenance)
                .where(Maintenance.asset_id == a.id)
                .order_by(Maintenance.id.desc())
                .limit(50)
            )
        ],
    }
