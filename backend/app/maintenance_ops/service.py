import calendar, json, hashlib, csv, io
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select, func, or_, and_, text
from app.core.config import settings
from app.models.entities import Site, User, Incident, MaintenanceWindow
from app.field import service as field
from app.field.models import WorkOrder, Event, Template, now
from app.field.contracts import WorkInput
from app.equipment import service as inv
from app.equipment.models import Asset, Product, Maintenance, CostEntry
from app.equipment.contracts import MaintenanceInput, CostInput
from app.tower_health.models import TowerFinding
from .models import (
    Schedule,
    Occurrence,
    ServiceRecord,
    Availability,
    Reminder,
    Category,
    Document,
    Version,
    DocumentEvent,
)
from . import storage

TYPES = [
    "tower_inspection",
    "backhaul_radio",
    "router_switch",
    "ups_battery",
    "power_supply",
    "cabinet",
    "grounding_surge",
    "fiber_enclosure",
    "warranty_review",
    "site_security",
]


def due(s, index):
    anchor = s.starts_at.astimezone(timezone.utc)
    if s.recurrence in {"weekly", "custom_days"}:
        return anchor + timedelta(
            days=(7 if s.recurrence == "weekly" else s.custom_days) * index
        )
    months = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}[
        s.recurrence
    ] * index
    y, m = divmod(anchor.year * 12 + anchor.month - 1 + months, 12)
    m += 1
    if y > 9998:
        raise ValueError("Recurrence horizon exceeded")
    return anchor.replace(
        year=y, month=m, day=min(anchor.day, calendar.monthrange(y, m)[1])
    )


def visible_sites(db, u):
    return field.scope(db, u)


def schedule_access(db, u, id, write=False):
    s = field.get(db, Schedule, id)
    if write:
        field.admin(u)
    elif u.role == "technician":
        if s.technician_id != u.id:
            field.site_access(db, u, s.site_id)
    return s


def create_schedule(db, u, b):
    field.admin(u)
    field.site_access(db, u, b.site_id)
    if (
        b.asset_id
        and field.asset_site(db, field.get(db, Asset, b.asset_id)) != b.site_id
    ):
        raise ValueError("Asset/tower mismatch")
    if b.technician_id:
        field.tech(db, b.technician_id)
    if b.template_id and not field.get(db, Template, b.template_id).active:
        raise ValueError("Active inspection template required")
    row = Schedule(
        **b.model_dump(exclude={"operation_key", "reason"}),
        next_due_at=b.starts_at,
        created_by=u.id
    )
    db.add(row)
    db.flush()
    return inv.view(row)


def update_schedule(db, u, s, b):
    field.admin(u)
    db.refresh(s)
    if s.revision != b.expected_revision:
        raise ValueError("Schedule revision changed")
    if b.technician_id:
        field.tech(db, b.technician_id)
    s.active = b.active
    s.technician_id = b.technician_id
    s.revision += 1
    db.flush()
    return inv.view(s)


def generate(db, instant=None):
    instant = instant or now()
    inv.lock(db)
    rows = db.scalars(
        select(Schedule)
        .where(
            Schedule.active.is_(True),
            Schedule.next_due_at - Schedule.lead_days * text("INTERVAL '1 day'")
            <= instant,
        )
        .order_by(Schedule.next_due_at, Schedule.id)
        .limit(100)
    ).all()
    made = []
    blocked = 0
    for s in rows:
        if s.next_due_at > instant + timedelta(days=s.lead_days):
            continue
        creator = field.get(db, User, s.created_by)
        if not creator.is_active or creator.role != "admin":
            blocked += 1
            continue
        old = db.scalar(
            select(Occurrence).where(
                Occurrence.schedule_id == s.id,
                Occurrence.occurrence_index == s.next_index,
            )
        )
        if old:
            raise ValueError(
                "Occurrence/cursor inconsistent; administrator review required"
            )
        # Do not silently follow a moved/retired asset to a different tower.
        if s.asset_id:
            asset = field.get(db, Asset, s.asset_id)
            if asset.state == "retired" or field.asset_site(db, asset) != s.site_id:
                blocked += 1
                continue
        if s.technician_id:
            try:
                field.tech(db, s.technician_id)
            except ValueError:
                blocked += 1
                continue
        b = WorkInput(
            operation_key=__import__("uuid").uuid4(),
            reason="Recurring maintenance occurrence documented internally; no dispatch",
            site_id=s.site_id,
            asset_id=s.asset_id,
            template_id=s.template_id,
            technician_id=s.technician_id,
            description=s.name,
            priority=s.priority,
            scheduled_at=s.next_due_at if s.technician_id else None,
            required_equipment=s.required_equipment,
        )
        # A previously assigned immutable template remains valid after a newer version.
        if s.template_id and not field.get(db, Template, s.template_id).active:
            b.template_id = None
            w = field.create_work(db, creator, b)
            db.get(WorkOrder, w["id"]).template_id = s.template_id
        else:
            w = field.create_work(db, creator, b)
        o = Occurrence(
            schedule_id=s.id,
            occurrence_index=s.next_index,
            due_at=s.next_due_at,
            work_order_id=w["id"],
        )
        db.add(o)
        s.next_index += 1
        s.next_due_at = due(s, s.next_index)
        s.revision += 1
        db.flush()
        field.audit(
            db,
            creator.id,
            "maintenance_occurrence",
            "maintenance_occurrence",
            o.id,
            {"schedule_id": s.id, "work_order_id": w["id"], "external_delivery": False},
        )
        made.append(
            {"occurrence_id": o.id, "work_order_id": w["id"], "schedule_id": s.id}
        )
    return {
        "generated": made,
        "blocked": blocked,
        "bounded_schedules": len(rows),
        "external_delivery": False,
        "dispatches": 0,
    }


def service_record(db, u, b):
    w = field.work_access(db, u, b.work_order_id, True)
    db.refresh(w)
    field.revision(w, b.expected_revision)
    if not w.asset_id or w.status != "in_progress":
        raise ValueError("Assigned in-progress asset work required")
    if u.role != "admin" and (b.repair_cost is not None or b.currency is not None):
        raise PermissionError("Financial data requires administrator")
    if b.maintenance.technician_id not in {None, u.id} and u.role != "admin":
        raise PermissionError("Cannot impersonate technician")
    if b.maintenance.status != "completed":
        raise ValueError("Service records document completed service actions")
    if b.maintenance.tower_finding_id:
        field.get(db, TowerFinding, b.maintenance.tower_finding_id)
    m = inv.add_maintenance(db, field.get(db, Asset, w.asset_id), b.maintenance, u.id)
    row = ServiceRecord(
        work_order_id=w.id,
        asset_id=w.asset_id,
        site_id=w.site_id,
        technician_id=m.technician_id,
        equipment_maintenance_id=m.id,
        incident_id=w.incident_id,
        serviced_at=m.inspected_at,
        diagnostics=b.diagnostics,
        labor_minutes=b.labor_minutes,
        warranty_claim=b.warranty_claim,
    )
    db.add(row)
    db.flush()
    if b.repair_cost is not None:
        inv.add_cost(
            db,
            CostInput(
                maintenance_id=m.id,
                amount=b.repair_cost,
                currency=b.currency,
                reason=b.reason,
            ),
            u.id,
        )
    elif b.currency is not None:
        raise ValueError("No currency without recorded cost")
    w.revision += 1
    field.event(
        db,
        w,
        u,
        "service_recorded",
        b.reason,
        {"service_record_id": row.id, "maintenance_id": m.id},
    )
    return inv.view(row)


def service_views(db, rows, financial=False):
    ids = [r.equipment_maintenance_id for r in rows]
    ms = (
        {
            r.id: r
            for r in db.scalars(select(Maintenance).where(Maintenance.id.in_(ids)))
        }
        if ids
        else {}
    )
    costs = (
        {
            r.maintenance_id: r
            for r in db.scalars(
                select(CostEntry).where(CostEntry.maintenance_id.in_(ids))
            )
        }
        if ids and financial
        else {}
    )
    return [
        inv.view(r)
        | {"maintenance": inv.view(ms[r.equipment_maintenance_id])}
        | (
            {
                "repair_cost": (
                    inv.view(costs[r.equipment_maintenance_id])
                    if r.equipment_maintenance_id in costs
                    else None
                )
            }
            if financial
            else {}
        )
        for r in rows
    ]


def service_view(db, row, financial=False):
    return service_views(db, [row], financial)[0]


def doc_access(db, u, id, write=False):
    d = field.get(db, Document, id)
    if d.deleted_at and u.role != "admin":
        raise LookupError("Document unavailable")
    if u.role != "admin":
        if d.visibility == "admin" or (
            u.role == "technician" and d.visibility != "technician"
        ):
            raise PermissionError("Document role restriction")
        if u.role == "technician":
            field.site_access(db, u, d.site_id)
    if write and u.role not in {"admin", "technician"}:
        raise PermissionError("Read-only maintenance operator")
    return d


def doc_event(db, u, d, action, reason, version=None):
    db.add(
        DocumentEvent(
            document_id=d.id,
            version_id=version,
            actor_id=u.id,
            action=action,
            reason=reason,
        )
    )


def create_document(db, u, b):
    if u.role not in {"admin", "technician"}:
        raise PermissionError("Document write unavailable")
    field.site_access(db, u, b.site_id)
    field.get(db, Category, b.category_id)
    if (
        b.asset_id
        and field.asset_site(db, field.get(db, Asset, b.asset_id)) != b.site_id
    ):
        raise ValueError("Asset/tower mismatch")
    if u.role == "technician" and b.visibility != "technician":
        raise PermissionError("Administrator sets restricted classifications")
    d = Document(**b.model_dump(exclude={"operation_key", "reason"}), created_by=u.id)
    db.add(d)
    db.flush()
    doc_event(db, u, d, "created", b.reason)
    return inv.view(d)


def upload_version(db, u, d, b):
    db.refresh(d)
    if d.deleted_at or d.revision != b.expected_revision:
        raise ValueError("Document revision changed or deleted")
    raw = storage.validate(b)
    key = storage.put(raw)
    state, source = storage.scan(raw)
    n = (
        db.scalar(select(func.max(Version.number)).where(Version.document_id == d.id))
        or 0
    ) + 1
    v = Version(
        document_id=d.id,
        number=n,
        filename=b.filename,
        media_type=b.media_type,
        storage_key=key,
        sha256=key,
        size=len(raw),
        scan_state=state,
        scan_source=source,
        scanned_at=now() if source != "disabled" else None,
        uploaded_by=u.id,
        change_description=b.change_description,
    )
    db.add(v)
    d.revision += 1
    db.flush()
    doc_event(db, u, d, "version_uploaded", b.reason, v.id)
    return inv.view(v, exclude={"storage_key"})


def trusted_clean(v):
    return v.scan_state == "clean" and (
        v.scan_source == "clamav"
        or (
            v.scan_source == "synthetic-fixture"
            and settings.offline_mode
            and settings.app_env == "staging"
            and settings.synthetic_monitoring_enabled
        )
    )


def control_document(db, u, d, b):
    field.admin(u)
    db.refresh(d)
    if d.revision != b.expected_revision:
        raise ValueError("Document revision changed")
    v = field.get(db, Version, b.version_id) if b.version_id else None
    if v and v.document_id != d.id:
        raise ValueError("Version/document mismatch")
    if b.action in {"approve", "rescan"}:
        if not v or d.deleted_at:
            raise ValueError("Active document and version required")
        raw = storage.read(v.storage_key)
        if b.action == "rescan":
            v.scan_state, v.scan_source = storage.scan(raw)
            v.scanned_at = now()
        elif not trusted_clean(v):
            raise ValueError("Clean malware scan required before approval")
        else:
            d.approved_revision = v.number
    elif b.action == "delete":
        d.deleted_at = now()
    elif b.action == "restore":
        d.deleted_at = None
    elif b.action == "hold":
        d.legal_hold = True
    elif b.action == "release_hold":
        d.legal_hold = False
    d.revision += 1
    doc_event(db, u, d, b.action, b.reason, v.id if v else None)
    db.flush()
    return inv.view(d)


def document_detail(db, u, d, offset=0):
    versions = db.scalars(
        select(Version)
        .where(Version.document_id == d.id)
        .order_by(Version.number.desc())
        .offset(offset)
        .limit(50)
    ).all()
    return inv.view(d) | {
        "versions": [inv.view(v, exclude={"storage_key"}) for v in versions],
        "next_offset": offset + 50 if len(versions) == 50 else None,
        "retention_eligible_at": (
            (d.deleted_at + timedelta(days=d.retention_days)).isoformat()
            if d.deleted_at and not d.legal_hold
            else None
        ),
        "physical_purge": "not automatic; retain file and database backups together",
    }


def download(db, u, d, v):
    if d.deleted_at or v.document_id != d.id or not trusted_clean(v):
        raise PermissionError("Document not approved/clean")
    if u.role != "admin" and d.approved_revision != v.number:
        raise PermissionError("Only approved version available to this role")
    raw = storage.read(v.storage_key)
    if len(raw) != v.size or hashlib.sha256(raw).hexdigest() != v.sha256:
        raise ValueError("Document checksum mismatch")
    return raw


def calendar_rows(
    db,
    u,
    start,
    end,
    site_id=None,
    technician_id=None,
    category_id=None,
    maintenance_type=None,
):
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or end <= start
        or end - start > timedelta(days=62)
    ):
        raise ValueError("Calendar requires aware interval up to62 days")
    work = (
        select(WorkOrder, Occurrence, Schedule)
        .outerjoin(Occurrence, Occurrence.work_order_id == WorkOrder.id)
        .outerjoin(Schedule, Schedule.id == Occurrence.schedule_id)
        .where(
            func.coalesce(
                WorkOrder.completed_at,
                Occurrence.due_at,
                WorkOrder.scheduled_at,
                WorkOrder.created_at,
            )
            >= start,
            func.coalesce(
                WorkOrder.completed_at,
                Occurrence.due_at,
                WorkOrder.scheduled_at,
                WorkOrder.created_at,
            )
            < end,
        )
    )
    if u.role == "technician":
        work = work.where(WorkOrder.technician_id == u.id)
    if site_id:
        work = work.where(WorkOrder.site_id == site_id)
    if technician_id:
        work = work.where(WorkOrder.technician_id == technician_id)
    if category_id:
        work = (
            work.join(Asset, Asset.id == WorkOrder.asset_id)
            .join(Product, Product.id == Asset.product_id)
            .where(Product.category_id == category_id)
        )
    if maintenance_type:
        work = work.where(Schedule.maintenance_type == maintenance_type)
    result = []
    work_rows = db.execute(work.order_by(WorkOrder.id.desc()).limit(501)).all()
    bounded = len(work_rows) > 500
    for w, o, s in work_rows[:500]:
        at = w.completed_at or (o.due_at if o else w.scheduled_at or w.created_at)
        result.append(
            {
                "kind": "work_order",
                "id": w.id,
                "title": w.description,
                "site_id": w.site_id,
                "asset_id": w.asset_id,
                "technician_id": w.technician_id,
                "at": at.isoformat(),
                "status": w.status,
                "overdue": at < now() and w.status not in field.TERMINAL,
                "maintenance_type": s.maintenance_type if s else "ad_hoc",
                "estimated_minutes": s.estimated_minutes if s else None,
            }
        )
    schedules = select(Schedule).where(
        Schedule.active.is_(True), Schedule.next_due_at < end
    )
    if u.role == "technician":
        schedules = schedules.where(Schedule.technician_id == u.id)
    if site_id:
        schedules = schedules.where(Schedule.site_id == site_id)
    if technician_id:
        schedules = schedules.where(Schedule.technician_id == technician_id)
    if maintenance_type:
        schedules = schedules.where(Schedule.maintenance_type == maintenance_type)
    if category_id:
        schedules = (
            schedules.join(Asset, Asset.id == Schedule.asset_id)
            .join(Product, Product.id == Asset.product_id)
            .where(Product.category_id == category_id)
        )
    schedule_rows = db.scalars(schedules.order_by(Schedule.id).limit(101)).all()
    bounded = bounded or len(schedule_rows) > 100
    for s in schedule_rows[:100]:
        anchor = s.starts_at.astimezone(timezone.utc)
        if s.recurrence in {"weekly", "custom_days"}:
            distance = (start - anchor).days // (
                7 if s.recurrence == "weekly" else s.custom_days
            )
        else:
            months = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}[
                s.recurrence
            ]
            distance = (
                (start.year - anchor.year) * 12 + start.month - anchor.month
            ) // months
        index = max(s.next_index, distance - 1, 0)
        for _ in range(100):
            at = due(s, index)
            index += 1
            if at >= end:
                break
            if at >= start:
                result.append(
                    {
                        "kind": "projected_schedule",
                        "id": s.id,
                        "title": s.name,
                        "site_id": s.site_id,
                        "asset_id": s.asset_id,
                        "technician_id": s.technician_id,
                        "at": at.isoformat(),
                        "status": "not_generated",
                        "overdue": at < now(),
                        "maintenance_type": s.maintenance_type,
                        "estimated_minutes": s.estimated_minutes,
                    }
                )
            if len(result) > 1000:
                break
    if not category_id and not maintenance_type:
        windows = select(MaintenanceWindow).where(
            MaintenanceWindow.starts_at < end, MaintenanceWindow.ends_at > start
        )
        if site_id:
            windows = windows.where(MaintenanceWindow.site_id == site_id)
        if u.role == "technician":
            windows = windows.where(MaintenanceWindow.site_id.in_(visible_sites(db, u)))
        if not technician_id:
            for w in db.scalars(windows.order_by(MaintenanceWindow.id).limit(100)):
                result.append(
                    {
                        "kind": "network_maintenance",
                        "id": w.id,
                        "title": w.description,
                        "site_id": w.site_id,
                        "at": w.starts_at.isoformat(),
                        "ends_at": w.ends_at.isoformat(),
                        "status": w.status,
                    }
                )
        avail = select(Availability).where(
            Availability.starts_at < end, Availability.ends_at > start
        )
        if u.role == "technician":
            avail = avail.where(Availability.technician_id == u.id)
        if technician_id:
            avail = avail.where(Availability.technician_id == technician_id)
        if not site_id:
            for a in db.scalars(avail.limit(100)):
                result.append(
                    {
                        "kind": "recorded_availability",
                        "id": a.id,
                        "title": a.state,
                        "technician_id": a.technician_id,
                        "at": a.starts_at.isoformat(),
                        "ends_at": a.ends_at.isoformat(),
                        "status": "recorded",
                    }
                )
    services = select(ServiceRecord).where(
        ServiceRecord.serviced_at >= start, ServiceRecord.serviced_at < end
    )
    if u.role == "technician":
        services = services.where(ServiceRecord.technician_id == u.id)
    if site_id:
        services = services.where(ServiceRecord.site_id == site_id)
    if technician_id:
        services = services.where(ServiceRecord.technician_id == technician_id)
    if category_id:
        services = (
            services.join(Asset, Asset.id == ServiceRecord.asset_id)
            .join(Product, Product.id == Asset.product_id)
            .where(Product.category_id == category_id)
        )
    if maintenance_type:
        services = (
            services.join(
                Occurrence, Occurrence.work_order_id == ServiceRecord.work_order_id
            )
            .join(Schedule, Schedule.id == Occurrence.schedule_id)
            .where(Schedule.maintenance_type == maintenance_type)
        )
    completed = db.scalars(services.order_by(ServiceRecord.id.desc()).limit(251)).all()
    bounded = bounded or len(completed) > 250
    for r in completed[:250]:
        result.append(
            {
                "kind": "service_record",
                "id": r.id,
                "title": "Equipment service #" + str(r.id),
                "site_id": r.site_id,
                "asset_id": r.asset_id,
                "technician_id": r.technician_id,
                "at": r.serviced_at.isoformat(),
                "status": "completed",
                "work_order_id": r.work_order_id,
            }
        )
    return {
        "items": sorted(result, key=lambda x: x["at"])[:1000],
        "truncated": bounded or len(result) > 1000,
        "timezone": "UTC; client presents local time",
        "availability": "Recorded only; absence is not availability",
    }


def reminders(db, instant=None):
    instant = instant or now()
    active = {}
    schedules = db.scalars(
        select(Schedule)
        .where(Schedule.active.is_(True))
        .order_by(Schedule.id)
        .limit(1000)
    ).all()
    pending = db.execute(
        select(Occurrence, WorkOrder, Schedule)
        .join(WorkOrder, WorkOrder.id == Occurrence.work_order_id)
        .join(Schedule, Schedule.id == Occurrence.schedule_id)
        .where(
            Occurrence.due_at <= instant + timedelta(days=365),
            WorkOrder.status.notin_(field.TERMINAL),
        )
        .limit(1000)
    ).all()
    assets = db.scalars(
        select(Asset)
        .where(
            or_(
                Asset.state == "failed",
                and_(
                    Asset.warranty_end.is_not(None),
                    Asset.warranty_end
                    <= instant.date()
                    + timedelta(days=settings.maintenance_warranty_warning_days),
                    Asset.warranty_end >= instant.date(),
                ),
            )
        )
        .limit(1000)
    ).all()
    repeats = db.execute(
        select(ServiceRecord.asset_id, func.count())
        .where(
            ServiceRecord.serviced_at
            >= instant - timedelta(days=settings.maintenance_service_history_days)
        )
        .group_by(ServiceRecord.asset_id)
        .having(func.count() >= settings.maintenance_repeat_service_count)
        .limit(1000)
    ).all()
    findings = db.scalars(
        select(TowerFinding)
        .where(TowerFinding.state == "active")
        .order_by(TowerFinding.id.desc())
        .limit(1000)
    ).all()

    def add(key, site, asset, kind, evidence):
        active[key] = (site, asset, kind, evidence)

    for s in schedules:
        if s.next_due_at <= instant + timedelta(days=s.due_soon_days):
            add(
                "schedule:" + str(s.id),
                s.site_id,
                s.asset_id,
                (
                    "overdue"
                    if s.next_due_at + timedelta(days=s.overdue_days) < instant
                    else "due_soon"
                ),
                {
                    "schedule_id": s.id,
                    "due_at": s.next_due_at.isoformat(),
                    "reason": "Documented recurrence due",
                    "suggestion": "Review schedule and internal work; no dispatch",
                    "confidence": "documented",
                },
            )
    for o, w, s in pending:
        if o.due_at <= instant + timedelta(days=s.due_soon_days):
            add(
                "occurrence:" + str(o.id),
                w.site_id,
                w.asset_id,
                (
                    "missed_appointment"
                    if o.due_at + timedelta(days=s.overdue_days) < instant
                    else "due_soon"
                ),
                {
                    "work_order_id": w.id,
                    "due_at": o.due_at.isoformat(),
                    "reason": "Scheduled work remains open",
                    "suggestion": "Review assigned maintenance",
                    "confidence": "documented",
                },
            )
    from app.equipment.models import Location

    locations = {
        l.id: l.site_id
        for l in db.scalars(
            select(Location).where(
                Location.id.in_([a.location_id for a in assets if a.location_id])
            )
        )
    }
    for a in assets:
        site = locations.get(a.location_id)
        if site:
            add(
                "asset:" + str(a.id),
                site,
                a.id,
                "repair_needed" if a.state == "failed" else "warranty_review",
                {
                    "asset_id": a.id,
                    "warranty_end": (
                        a.warranty_end.isoformat() if a.warranty_end else None
                    ),
                    "reason": (
                        "Documented failed state"
                        if a.state == "failed"
                        else "Documented warranty within configured warning horizon"
                    ),
                    "suggestion": "Review service records and manufacturer coverage; no purchases",
                    "confidence": "documented",
                },
            )
    repeat_rows = {
        a.id: a
        for a in db.scalars(select(Asset).where(Asset.id.in_([a for a, c in repeats])))
    }
    repeat_locations = {
        l.id: l.site_id
        for l in db.scalars(
            select(Location).where(
                Location.id.in_(
                    [a.location_id for a in repeat_rows.values() if a.location_id]
                )
            )
        )
    }
    for asset_id, count in repeats:
        a = repeat_rows.get(asset_id)
        site = repeat_locations.get(a.location_id) if a else None
        if site:
            add(
                "repeat:" + str(asset_id),
                site,
                asset_id,
                "repeat_service",
                {
                    "asset_id": asset_id,
                    "service_records_in_window": count,
                    "window_days": settings.maintenance_service_history_days,
                    "reason": "Configured repeated-service threshold reached; service actions are not all confirmed failures",
                    "suggestion": "Inspect fault and repair history for recurring causes",
                    "confidence": "limited; no failure probability",
                },
            )
    for f in findings:
        if 0 <= (instant - f.last_seen).total_seconds() <= 300:
            add(
                "finding:" + str(f.id),
                f.site_id,
                None,
                "monitoring_review",
                {
                    "finding_id": f.id,
                    "rule": f.rule,
                    "last_seen": f.last_seen.isoformat(),
                    "reason": "Fresh existing finding, not a predicted failure",
                    "suggestion": "Qualified review of power/RF/temperature and service evidence",
                    "confidence": f.confidence,
                },
            )
    prior = {
        r.key: r
        for r in db.scalars(select(Reminder).where(Reminder.key.in_(list(active))))
    }
    for key, (site, asset, kind, evidence) in active.items():
        r = prior.get(key)
        if r is None:
            r = Reminder(
                key=key, site_id=site, asset_id=asset, kind=kind, evidence=evidence
            )
            db.add(r)
        r.active = True
        r.last_seen = instant
        r.evidence = evidence
        r.kind = kind
    existing = db.scalars(
        select(Reminder).where(Reminder.active.is_(True)).limit(5001)
    ).all()
    complete = (
        all(
            len(rows) < 1000 for rows in [schedules, pending, assets, repeats, findings]
        )
        and len(existing) <= 5000
    )
    if complete:
        for r in existing:
            if r.key not in active:
                r.active = False
    db.flush()
    return {
        "active": len(active),
        "complete": complete,
        "external_delivery": False,
        "scope": "Bounded internal evidence; no predictions or dispatch. Omitted rows do not recover when bounds reached.",
    }


def report(db, u, start, end, site_id=None, technician_id=None, financial=False):
    if financial:
        field.admin(u)
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or end <= start
        or end - start > timedelta(days=366)
    ):
        raise ValueError("Report range maximum366 days")
    conditions = [ServiceRecord.serviced_at >= start, ServiceRecord.serviced_at < end]
    if u.role == "technician":
        conditions.append(ServiceRecord.technician_id == u.id)
    if site_id:
        conditions.append(ServiceRecord.site_id == site_id)
    if technician_id:
        conditions.append(ServiceRecord.technician_id == technician_id)
    records = db.scalars(
        select(ServiceRecord)
        .where(*conditions)
        .order_by(ServiceRecord.id.desc())
        .limit(1001)
    ).all()
    rows = service_views(db, records[:1000], financial)
    labor = db.execute(
        select(
            func.sum(ServiceRecord.labor_minutes),
            func.count(ServiceRecord.id),
            func.count(ServiceRecord.labor_minutes),
        ).where(*conditions)
    ).one()
    counts = dict(
        db.execute(
            select(ServiceRecord.asset_id, func.count())
            .where(*conditions)
            .group_by(ServiceRecord.asset_id)
        ).all()
    )
    costs = {}
    if financial:
        costs = {
            c: str(v)
            for c, v in db.execute(
                select(CostEntry.currency, func.sum(CostEntry.amount))
                .join(
                    ServiceRecord,
                    ServiceRecord.equipment_maintenance_id == CostEntry.maintenance_id,
                )
                .where(*conditions)
                .group_by(CostEntry.currency)
            )
        }
    wcond = [
        func.coalesce(Occurrence.due_at, WorkOrder.scheduled_at, WorkOrder.created_at)
        >= start,
        func.coalesce(Occurrence.due_at, WorkOrder.scheduled_at, WorkOrder.created_at)
        < end,
    ]
    if u.role == "technician":
        wcond.append(WorkOrder.technician_id == u.id)
    if site_id:
        wcond.append(WorkOrder.site_id == site_id)
    if technician_id:
        wcond.append(WorkOrder.technician_id == technician_id)
    work_summary = [
        {"site_id": site, "technician_id": tech, "status": status, "count": count}
        for site, tech, status, count in db.execute(
            select(
                WorkOrder.site_id,
                WorkOrder.technician_id,
                WorkOrder.status,
                func.count(),
            )
            .outerjoin(Occurrence, Occurrence.work_order_id == WorkOrder.id)
            .where(*wcond)
            .group_by(WorkOrder.site_id, WorkOrder.technician_id, WorkOrder.status)
        )
    ]
    from app.equipment.models import BulkMovement

    pc = [
        Event.kind == "parts_consumed",
        Event.created_at >= start,
        Event.created_at < end,
    ]
    if u.role == "technician":
        pc.append(WorkOrder.technician_id == u.id)
    if site_id:
        pc.append(WorkOrder.site_id == site_id)
    if technician_id:
        pc.append(WorkOrder.technician_id == technician_id)
    parts = [
        {"product_id": p, "unit": unit, "quantity": str(qty)}
        for p, unit, qty in db.execute(
            select(
                BulkMovement.product_id, Product.unit, func.sum(BulkMovement.quantity)
            )
            .select_from(Event)
            .join(WorkOrder, WorkOrder.id == Event.work_order_id)
            .join(
                BulkMovement,
                BulkMovement.id == Event.data["bulk_movement_id"].as_integer(),
            )
            .join(Product, Product.id == BulkMovement.product_id)
            .where(*pc)
            .group_by(BulkMovement.product_id, Product.unit)
        )
    ]
    due_at = func.coalesce(
        Occurrence.due_at, WorkOrder.scheduled_at, WorkOrder.created_at
    )
    pending = [*wcond, WorkOrder.status.notin_(field.TERMINAL)]
    overdue = db.scalar(
        select(func.count())
        .select_from(WorkOrder)
        .outerjoin(Occurrence, Occurrence.work_order_id == WorkOrder.id)
        .where(*pending, due_at < now())
    )
    upcoming = db.scalar(
        select(func.count())
        .select_from(WorkOrder)
        .outerjoin(Occurrence, Occurrence.work_order_id == WorkOrder.id)
        .where(*pending, due_at >= now())
    )
    faults = dict(
        db.execute(
            select(ServiceRecord.asset_id, func.count())
            .join(Maintenance, Maintenance.id == ServiceRecord.equipment_maintenance_id)
            .where(
                *conditions,
                Maintenance.fault_description.is_not(None),
                Maintenance.fault_description != ""
            )
            .group_by(ServiceRecord.asset_id)
        ).all()
    )
    return {
        "services": rows,
        "truncated": len(records) > 1000,
        "labor_minutes_known": labor[0] if labor[0] is not None else None,
        "labor_unknown": labor[1] - labor[2],
        "service_counts_by_asset": counts,
        "repair_costs_by_currency": costs if financial else None,
        "work_summary": work_summary,
        "overdue_work_count": overdue,
        "upcoming_work_count": upcoming,
        "reported_faults_by_asset": faults,
        "parts_consumed": parts,
        "parts_basis": "Actual NOC100 bulk ledger referenced by field consumption events; service parts notes are not added again",
        "external_delivery": False,
    }


def csv_report(data, financial):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "service_id",
            "site_id",
            "asset_id",
            "technician_id",
            "serviced_at",
            "labor_minutes",
            "warranty_claim",
        ]
        + (["repair_cost", "currency"] if financial else [])
    )

    def safe(v):
        s = "" if v is None else str(v)
        return "'" + s if s.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else s

    for r in data["services"]:
        values = [
            r[k]
            for k in [
                "id",
                "site_id",
                "asset_id",
                "technician_id",
                "serviced_at",
                "labor_minutes",
                "warranty_claim",
            ]
        ]
        if financial:
            values += [
                (r.get("repair_cost") or {}).get(k) for k in ["amount", "currency"]
            ]
        w.writerow([safe(v) for v in values])
    return out.getvalue()
