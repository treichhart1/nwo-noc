"""Independent public approval over canonical incidents and the NOC-96 outbox."""

from datetime import timedelta, timezone
from collections import Counter
from sqlalchemy import select, func, text
from app.models.entities import (
    User,
    Incident,
    Device,
    Sector,
    NetworkLink,
    MonitorCheck,
    MonitoringAlert,
    AuditLog,
)
from app.notifications import service as ns
from app.notifications.models import (
    NotificationMaintenance,
    NotificationPlan,
    NotificationRecipient,
    NotificationDelivery,
    NotificationTemplate,
)
from app.notifications.contracts import PlanRequest, Evidence
from app.customers.impact import Target, target_for, analyze, recipients
from app.customers.models import CustomerService, CustomerAccount
from app.field import service as field
from app.field.models import WorkOrder
from app.maintenance_ops.models import Schedule
from .models import Policy, Area, Publication, Revision, Recovery, Event, Message
from .contracts import *

clock = ns.clock


def get(db, cls, id):
    r = db.get(cls, id)
    if not r:
        raise LookupError("Communication record unavailable")
    return r


def reader(u):
    if u.role not in {"admin", "operator"}:
        raise PermissionError("Communications operator required")


def admin(u):
    field.admin(u)


def view(r):
    from app.equipment.service import view as safe_view

    return safe_view(r)


def event(db, u, action, reason, p=None, r=None, details=None):
    db.add(
        Event(
            publication_id=p.id if p else None,
            revision_id=r.id if r else None,
            actor_id=u.id,
            action=action,
            reason=reason,
        )
    )
    db.add(
        AuditLog(
            user_id=u.id,
            action="customer_status." + action,
            entity_type="customer_status",
            entity_id=str(p.id) if p else None,
            details={
                "revision_id": r.id if r else None,
                "external_delivery": False,
                "reviewed_values": details or {},
            },
        )
    )


def policy(db):
    row = db.get(Policy, 1)
    return (
        view(row)
        if row
        else {
            "id": 1,
            "enabled": False,
            "baseline_status": "investigating",
            "baseline_approved_at": None,
            "stability_seconds": 300,
            "minimum_samples": 3,
            "maximum_gap_seconds": 120,
            "revision": 0,
        }
    )


def configure(db, u, b):
    admin(u)
    old = policy(db)
    if old["revision"] != b.expected_revision:
        raise ValueError("Policy revision changed")
    row = db.get(Policy, 1)
    if not row:
        row = Policy(id=1, updated_by=u.id)
        db.add(row)
    for k in [
        "enabled",
        "baseline_status",
        "stability_seconds",
        "minimum_samples",
        "maximum_gap_seconds",
    ]:
        setattr(row, k, getattr(b, k))
    row.baseline_approved_at = clock() if b.enabled else row.baseline_approved_at
    row.revision = old["revision"] + 1
    row.updated_by = u.id
    db.flush()
    event(db, u, "policy_reviewed", b.reason, details=view(row))
    return view(row)


def area(db, u, b):
    admin(u)
    if not b.publicly_approved:
        raise ValueError("Explicitly approved generic service-area name required")
    row = Area(
        label=b.label,
        created_by=u.id,
        public_details={
            k: getattr(b, k)
            for k in ("county", "technology", "group_label", "description")
        },
    )
    db.add(row)
    db.flush()
    event(db, u, "area_approved", b.reason)
    return view(row)


def source(db, p):
    row = (
        get(db, Incident, p.incident_id)
        if p.incident_id
        else get(db, NotificationMaintenance, p.maintenance_id)
    )
    scope = (
        target_for(db, incident_id=p.incident_id)
        if p.incident_id
        else target_for(db, target=Target(**row.target))
    )
    data = {
        "incident_id": p.incident_id,
        "maintenance_id": p.maintenance_id,
        "revision": row.revision,
        "status": row.status,
        "starts_at": ns.utc(
            row.started_at if p.incident_id else row.starts_at
        ).isoformat(),
        "ends_at": (
            ns.utc(row.resolved_at or row.closed_at).isoformat()
            if p.incident_id and (row.resolved_at or row.closed_at)
            else (ns.utc(row.ends_at).isoformat() if p.maintenance_id else None)
        ),
        "targets": [vars(scope)] if p.incident_id else (row.targets or [row.target]),
        "service_affecting": p.service_affecting,
    }
    return row, data


def source_digest(db, p):
    return ns.digest(source(db, p)[1])


def create(db, u, b):
    reader(u)
    cls = Incident if b.incident_id else NotificationMaintenance
    row = get(db, cls, b.incident_id or b.maintenance_id)
    existing = (
        db.scalar(select(Publication).where(Publication.incident_id == b.incident_id))
        if b.incident_id
        else db.scalar(
            select(Publication).where(Publication.maintenance_id == b.maintenance_id)
        )
    )
    if existing:
        if (
            existing.work_order_id != b.work_order_id
            or existing.schedule_id != b.schedule_id
            or existing.service_affecting
            != (True if b.incident_id else b.service_affecting)
        ):
            raise ValueError(
                "Existing source association differs; review original communication case"
            )
        return view(existing)
    scope = (
        target_for(db, incident_id=b.incident_id)
        if b.incident_id
        else Target(**row.target)
    )
    for id, model in [(b.work_order_id, WorkOrder), (b.schedule_id, Schedule)]:
        if id:
            linked = get(db, model, id)
            if not scope.site_id or linked.site_id != scope.site_id:
                raise ValueError(
                    "Maintenance/work order must match the explicitly documented source site"
                )
    p = Publication(
        incident_id=b.incident_id,
        maintenance_id=b.maintenance_id,
        work_order_id=b.work_order_id,
        schedule_id=b.schedule_id,
        service_affecting=True if b.incident_id else b.service_affecting,
        created_by=u.id,
    )
    db.add(p)
    db.flush()
    event(db, u, "source_linked", b.reason, p)
    return view(p)


def validate_recovery(db, p, proof_id):
    proof = get(db, Recovery, proof_id)
    if (
        proof.publication_id != p.id
        or not proof.verified
        or proof.source_digest != source_digest(db, p)
        or proof.policy_revision != policy(db)["revision"]
        or not 0 <= (clock() - proof.created_at).total_seconds() <= 300
    ):
        raise ValueError("Fresh matching recovery assessment required")
    current = recovery_evidence(db, p)
    if not current["verified"]:
        raise ValueError("Monitoring recovery is no longer stable")
    return proof


def recovery_evidence(db, p):
    row, data = source(db, p)
    pol = policy(db)
    now = clock()
    start = now - timedelta(seconds=pol["stability_seconds"])
    after = start - timedelta(seconds=pol["maximum_gap_seconds"])
    ids = set()
    for d in data["targets"]:
        t = Target(**d)
        if t.device_id:
            ids.add(t.device_id)
        if t.site_id:
            ids.update(
                db.scalars(
                    select(Device.id).where(
                        Device.wisp_site_id == t.site_id, Device.enabled.is_(True)
                    )
                )
            )
        if t.sector_id:
            s = get(db, Sector, t.sector_id)
            if s.device_id:
                ids.add(s.device_id)
        if t.link_id:
            l = get(db, NetworkLink, t.link_id)
            ids.update(i for i in (l.a_device_id, l.z_device_id) if i)
    if len(ids) > 100:
        raise ValueError("Recovery device budget exceeded; review a narrower source")
    checks = (
        list(
            db.scalars(
                select(MonitorCheck)
                .where(
                    MonitorCheck.device_id.in_(ids),
                    MonitorCheck.checked_at >= after,
                    MonitorCheck.checked_at <= now,
                )
                .order_by(MonitorCheck.device_id, MonitorCheck.checked_at)
                .limit(2001)
            )
        )
        if ids
        else []
    )
    if len(checks) > 2000:
        raise ValueError("Recovery evidence budget exceeded")
    reasons = []
    samples = {}
    count = 100
    if (
        row.status not in {"resolved", "closed", "completed"}
        or not data["ends_at"]
        or ns.utc(__import__("datetime").datetime.fromisoformat(data["ends_at"])) > now
    ):
        reasons.append("source_not_ended")
    if not ids:
        reasons.append("no_documented_monitored_devices")
    for id in sorted(ids):
        xs = [c for c in checks if c.device_id == id]
        samples[str(id)] = [c.id for c in xs]
        count = min(count, len(xs))
        if (
            len(xs) < pol["minimum_samples"]
            or not xs
            or xs[0].checked_at > start
            or (now - xs[-1].checked_at).total_seconds() > pol["maximum_gap_seconds"]
            or any(c.status != "up" for c in xs)
            or any(
                (b.checked_at - a.checked_at).total_seconds()
                > pol["maximum_gap_seconds"]
                for a, b in zip(xs, xs[1:])
            )
        ):
            reasons.append("incomplete_or_unstable_device:" + str(id))
    if ids and db.scalar(
        select(func.count())
        .select_from(MonitoringAlert)
        .where(
            MonitoringAlert.device_id.in_(ids),
            MonitoringAlert.state == "active",
            MonitoringAlert.severity.in_(["warning", "critical"]),
        )
    ):
        reasons.append("active_monitoring_alerts")
    return {
        "verified": not reasons,
        "reasons": reasons,
        "window_start": start.isoformat(),
        "window_end": now.isoformat(),
        "device_check_ids": samples,
        "minimum_samples": 0 if not ids else count,
        "scope": "stored monitoring checks; not end-user or physical fault proof",
    }


def assess(db, u, p, b):
    reader(u)
    data = recovery_evidence(db, p)
    r = Recovery(
        publication_id=p.id,
        source_digest=source_digest(db, p),
        policy_revision=policy(db)["revision"],
        evidence=data,
        verified=data["verified"],
        created_by=u.id,
    )
    db.add(r)
    db.flush()
    event(db, u, "recovery_assessed", b.reason, p)
    return view(r)


def draft(db, u, p, b):
    reader(u)
    if p.version != b.expected_version:
        raise ValueError("Communication case changed")
    row, data = source(db, p)
    areas = [get(db, Area, id) for id in b.area_ids]
    if any(not a.active for a in areas):
        raise ValueError("Approved active public areas required")
    if b.status == "resolved" and p.service_affecting:
        validate_recovery(db, p, b.recovery_id)
    if (
        b.status == "resolved"
        and not p.service_affecting
        and (row.status != "completed" or ns.utc(row.ends_at) > clock())
    ):
        raise ValueError("Maintenance completion must be recorded first")
    if b.status == "all_systems_operational" and not (
        p.maintenance_id and ns.utc(row.starts_at) > clock()
    ):
        raise ValueError(
            "Use resolved status for an incident and separately review the global baseline"
        )
    if b.status == "maintenance_underway" and (
        not p.maintenance_id
        or not ns.utc(row.starts_at) <= clock() <= ns.utc(row.ends_at)
    ):
        raise ValueError("Recorded maintenance window must be underway")
    if b.estimated_restoration_at and b.estimated_restoration_at <= clock():
        raise ValueError(
            "Restoration estimate must be future and independently approved"
        )
    payload = {
        "title": b.title,
        "description": b.description,
        "status": b.status,
        "areas": [a.label for a in areas],
        "impact": b.impact,
        "area_details": [
            dict(a.public_details or {}, id=a.identifier, label=a.label) for a in areas
        ],
        "starts_at": data["starts_at"] if p.maintenance_id else None,
        "ends_at": data["ends_at"] if p.maintenance_id else None,
        "estimated_restoration_at": (
            b.estimated_restoration_at.isoformat()
            if b.estimated_restoration_at
            else None
        ),
    }
    p.version += 1
    r = Revision(
        publication_id=p.id,
        number=p.version - 1,
        base_version=p.version,
        source_digest=source_digest(db, p),
        payload=payload,
        recovery_id=b.recovery_id,
        withdraw=b.withdraw,
        created_by=u.id,
    )
    db.add(r)
    db.flush()
    event(db, u, "public_drafted", b.reason, p, r)
    return view(r)


def transition(db, u, r, b):
    reader(u)
    p = get(db, Publication, r.publication_id)
    if p.version != b.expected_version or r.base_version != p.version:
        raise ValueError("Review the latest communication revision")
    allowed = {
        "request_approval": ("draft", "pending_approval"),
        "approve": ("pending_approval", "approved"),
        "reject": ("pending_approval", "rejected"),
        "publish": ("approved", "published"),
    }
    old, new = allowed[b.action]
    if r.state != old:
        raise ValueError("Invalid public revision transition")
    if b.action != "request_approval":
        admin(u)
    if b.action in {"approve", "publish"}:
        if source_digest(db, p) != r.source_digest:
            raise ValueError(
                "Incident or maintenance changed; prepare a new public revision"
            )
        if r.payload["status"] == "resolved" and p.service_affecting:
            validate_recovery(db, p, r.recovery_id)
    if b.action == "approve":
        r.approved_by = u.id
        r.approved_at = clock()
    if b.action == "publish":
        approver = get(db, User, r.approved_by)
        if not approver.is_active or approver.role != "admin":
            raise ValueError("Public approval is no longer valid")
        if not policy(db)["enabled"]:
            raise ValueError("Public status remains disabled")
        if (
            not p.published_revision_id
            and not r.withdraw
            and db.scalar(
                select(func.count())
                .select_from(Publication)
                .where(Publication.published_revision_id.is_not(None))
            )
            >= 200
        ):
            raise ValueError(
                "Public announcement budget reached; withdraw obsolete approved records"
            )
        r.published_at = clock()
        new = (
            "withdrawn"
            if r.withdraw
            else (
                "resolved"
                if r.payload["status"] == "resolved"
                else ("updated" if p.published_revision_id else "published")
            )
        )
        p.published_revision_id = None if r.withdraw else r.id
    r.state = new
    db.flush()
    event(db, u, "public_" + new, b.reason, p, r)
    return view(r)


def request_case(db, request):
    ref = request.evidence.reference
    if not ref.startswith("noc109:"):
        return None, None
    parts = ref.split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        raise ValueError("Invalid communication source reference")
    p = get(db, Publication, int(parts[1]))
    if (
        p.incident_id != request.incident_id
        or p.maintenance_id != request.maintenance_id
    ):
        raise ValueError("Communication source mismatch")
    if request.kind == "restoration":
        validate_recovery(db, p, int(parts[2]))
    if p.maintenance_id and not p.service_affecting:
        raise ValueError("Non-interrupting maintenance cannot target customer SMS")
    return p, int(parts[2]) or None


def refine_impacts(db, request, impacts):
    p, _ = request_case(db, request)
    if not p:
        return impacts
    from app.unified.models import Node, Edge, Association, Circuit
    from app.unified import service as us

    _, data = source(db, p)
    nodes = list(db.scalars(select(Node).limit(10001)))
    edges = list(db.scalars(select(Edge).limit(20001)))
    if len(nodes) > 10000 or len(edges) > 20000:
        raise ValueError("Unified topology budget exceeded")
    fn = set()
    fe = set()
    if p.incident_id:
        for a in db.scalars(
            select(Association).where(Association.incident_id == p.incident_id)
        ):
            if a.node_id:
                fn.add(a.node_id)
            if a.edge_id:
                fe.add(a.edge_id)
    for t in data["targets"]:
        fn.update(
            n.id
            for n in nodes
            if (t.get("device_id") and n.device_id == t["device_id"])
            or (t.get("sector_id") and n.sector_id == t["sector_id"])
            or (t.get("site_id") and n.site_id == t["site_id"] and not n.service_id)
        )
        fe.update(
            e.id
            for e in edges
            if t.get("link_id") and e.network_link_id == t["link_id"]
        )
    unified = us.impact(db, fn, fe) if fn or fe else None
    represented = set(
        db.scalars(select(Circuit.service_id).where(Circuit.service_id.is_not(None)))
    )
    accounts = {
        a.id: a
        for a in db.scalars(
            select(CustomerAccount).where(
                CustomerAccount.id.in_([r["customer_id"] for r in impacts])
            )
        )
    }
    out = []
    for item in impacts:
        r = dict(item)
        r["evidence"] = dict(r["evidence"])
        r["evidence"]["legacy_classification"] = r["classification"]
        if unified and r["service_id"] in unified["preserved_services"]:
            r.update(
                classification="not_affected", reason="verified_unified_alternate_path"
            )
        elif unified and r["service_id"] in represented:
            if r["service_id"] in unified["estimated_services"]:
                r.update(classification="unknown", reason="unified_path_uncertain")
            elif r["service_id"] in unified["potentially_affected_services"]:
                if r["classification"] == "confirmed":
                    r["reason"] = "confirmed_documented_dependency_with_unified_impact"
                else:
                    r.update(
                        classification="unknown",
                        reason="unified_model_is_not_confirmation",
                    )
            else:
                r.update(
                    classification="not_affected",
                    reason="unified_dependency_not_affected",
                )
        elif represented and r["service_id"] in represented:
            r.update(
                classification="unknown", reason="source_not_mapped_to_unified_topology"
            )
        if r["classification"] == "probable":
            r.update(
                classification="unknown", reason="estimated_impact_excluded_by_policy"
            )
        account = accounts.get(r["customer_id"])
        if (
            not account
            or account.source_deleted_at
            or (clock() - ns.utc(account.last_synced_at)).total_seconds() > 86400
        ):
            r.update(
                classification="unknown", reason="stale_or_missing_customer_account"
            )
        out.append(r)
    return out


def impact_preview(db, p):
    services = list(
        db.scalars(select(CustomerService).order_by(CustomerService.id).limit(10001))
    )
    if len(services) > 10000:
        raise ValueError("Customer impact budget exceeded")
    _, data = source(db, p)
    groups = [analyze(db, Target(**t), services) for t in data["targets"]]
    rank = {"not_affected": 0, "unknown": 1, "probable": 2, "confirmed": 3}
    impacts = [
        max((g[i] for g in groups), key=lambda x: rank[x["classification"]])
        for i in range(len(services))
    ]
    request = PlanRequest(
        incident_id=p.incident_id,
        maintenance_id=p.maintenance_id,
        evidence=Evidence(
            reference=f"noc109:{p.id}:0",
            event_type="tower_outage" if p.incident_id else "planned_maintenance",
            confirmed=False,
        ),
    )
    if p.maintenance_id and not p.service_affecting:
        return {
            "impacts": impacts,
            "classification_counts": dict(
                Counter(i["classification"] for i in impacts)
            ),
            "recipients": [],
            "eligible_count": 0,
            "service_affecting": False,
        }
    impacts = refine_impacts(db, request, impacts)
    selection = recipients(db, impacts)
    return {
        "impacts": impacts,
        "classification_counts": dict(Counter(i["classification"] for i in impacts)),
        **selection,
        "service_affecting": p.service_affecting,
        "classification_is_dependency_evidence_not_live_customer_test": True,
    }


def prepare_message(db, u, p, b):
    reader(u)
    proof = validate_recovery(db, p, b.recovery_id) if b.kind == "restoration" else None
    req = PlanRequest(
        incident_id=p.incident_id,
        maintenance_id=p.maintenance_id,
        kind=b.kind,
        template_key=b.kind,
        update_number=b.update_number,
        due_at=b.due_at,
        evidence=Evidence(
            reference=f"noc109:{p.id}:{proof.id if proof else 0}",
            event_type=b.event_type,
            confirmed=b.impact_confirmed,
            recovery_verified=bool(proof),
            recovery_samples=(
                min(100, proof.evidence["minimum_samples"]) if proof else 0
            ),
            verified_at=proof.created_at if proof else None,
        ),
    )
    plan = ns.create_plan(db, req, u.id)
    if (
        plan.snapshot.get("request", {}).get("evidence", {}).get("reference")
        != req.evidence.reference
    ):
        raise ValueError(
            "This transition already has a legacy notification; review it instead of creating a duplicate"
        )
    existing = db.scalar(select(Message).where(Message.plan_id == plan.id))
    if existing and existing.publication_id != p.id:
        raise ValueError("Notification transition already associated elsewhere")
    if not existing:
        existing = Message(
            publication_id=p.id,
            plan_id=plan.id,
            recovery_id=proof.id if proof else None,
            created_by=u.id,
        )
        db.add(existing)
    db.flush()
    event(db, u, "sms_drafted", b.reason, p)
    return message_view(db, existing)


def message_view(db, m):
    p = get(db, NotificationPlan, m.plan_id)
    return {
        **view(m),
        "status": p.status,
        "kind": p.kind,
        "fingerprint": p.fingerprint,
        "message": p.message,
        "due_at": p.due_at.isoformat(),
        "approved_by": p.approved_by,
        "recipients": p.snapshot.get("recipients", []),
        "impacts": p.snapshot.get("impacts", []),
        "deliveries": [
            {
                "id": d.id,
                "recipient_id": d.recipient_id,
                "state": d.state,
                "attempts": d.attempts,
                "last_error": d.last_error,
                "provider_status": d.provider_status,
            }
            for d in db.scalars(
                select(NotificationDelivery)
                .where(NotificationDelivery.plan_id == p.id)
                .order_by(NotificationDelivery.id)
                .limit(200)
            )
        ],
        "delivery_counts": dict(
            db.execute(
                select(NotificationDelivery.state, func.count())
                .where(NotificationDelivery.plan_id == p.id)
                .group_by(NotificationDelivery.state)
            ).all()
        ),
        "external_delivery_enabled": False,
    }


def decide_message(db, u, m, b):
    admin(u)
    # Share the native worker lock before reading state; cancellation cannot
    # overwrite a receipt committed while the operator was waiting.
    ns.lock(db)
    p = db.scalar(
        select(NotificationPlan)
        .where(NotificationPlan.id == m.plan_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not p:
        raise LookupError("Notification plan unavailable")
    if p.fingerprint != b.fingerprint:
        raise ValueError("Recipient/content preview changed")
    if b.action == "approve":
        ns.approve(db, p.id, u.id, expected_fingerprint=b.fingerprint)
    else:
        if p.status not in {"draft", "approved"}:
            raise ValueError("Notification is no longer cancellable")
        p.status = "rejected" if b.action == "reject" else "cancelled"
        for d in db.scalars(
            select(NotificationDelivery).where(
                NotificationDelivery.plan_id == p.id,
                NotificationDelivery.state.in_(["queued", "retry"]),
            )
        ):
            d.state = "suppressed"
            d.last_error = "administrator_cancelled"
            d.completed_at = clock()
    event(db, u, "sms_" + b.action, b.reason, get(db, Publication, m.publication_id))
    return message_view(db, m)


def linked_approval(db, plan, user_id):
    if plan.snapshot.get("retention"):
        from app.retention.communications import snapshot as cancellation_snapshot
        admin(get(db, User, user_id))
        cancellation_snapshot(db, plan)
        return
    if plan.snapshot.get("prospect"):
        if plan.incident_id or plan.maintenance_id or plan.kind not in {"signup_application", "installation_scheduling"}:
            raise ValueError("Prospect source cannot replace incident/maintenance approval")
        from app.signup.communications import snapshot as prospect_snapshot
        prospect_snapshot(db, plan)  # Explicit current source/consent, never fake incident evidence.
        return
    request = PlanRequest.model_validate(plan.snapshot["request"])
    p, _ = request_case(db, request)
    if p:
        admin(get(db, User, user_id))


TEMPLATES = {
    "outage": "{brand}: We’re aware of an internet service outage affecting your area. Our technicians are investigating. We’ll provide updates as they become available. Support {support}. {opt_out}",
    "maintenance": "{brand}: Scheduled network maintenance may affect your internet service during {window}. Thank you for your patience. Support {support}. {opt_out}",
    "restoration": "{brand}: Service has been restored following the network outage. If you’re still experiencing problems, please contact support {support}. {opt_out}",
    "extended": "{brand}: Repairs are continuing in your area. We apologize for the interruption and will provide another update when available. Support {support}. {opt_out}",
}


def install_templates(db, u, b):
    admin(u)
    added = []
    for key, body in TEMPLATES.items():
        ns.validate_template(body)
        if not db.get(NotificationTemplate, key):
            db.add(NotificationTemplate(key=key, body=body, revision=1))
            added.append(key)
    event(db, u, "templates_initialized", b.reason)
    return {
        "created": added,
        "existing_templates_overwritten": False,
        "edit_in": "/api/notifications/templates",
    }
