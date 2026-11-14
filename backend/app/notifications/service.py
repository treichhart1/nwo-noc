"""Central approval, recipient, policy and durable mock outbox service."""

import hashlib, hmac, json, math, re, string
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, text, or_
from app.core.config import settings
from app.models.entities import (
    User,
    Incident,
    MaintenanceWindow,
    MonitorEvent,
    Site,
    Device,
    Sector,
    NetworkLink,
    AuditLog,
)
from app.customers.models import CustomerAccount, CustomerService
from app.customers.contracts import e164
from app.customers.impact import Target, target_for, analyze, recipients, reachable
from app.notifications.models import (
    NotificationPolicy,
    NotificationTemplate,
    NotificationMaintenance,
    NotificationPlan,
    NotificationRecipient,
    NotificationDelivery,
    NotificationSuppression,
)
from app.notifications.contracts import Policy, PlanRequest, KINDS

BRAND = "Northwest Ohio Broadband"
SUPPORT = "419-954-0586"
OPT_OUT = "Reply STOP to opt out."
DEFAULTS = {
    "outage": "{brand}: A service outage is affecting your area. We are investigating. Reference {event_id}. Text support {support}. {opt_out}",
    "update": "{brand}: Our team continues investigating service disruption {event_id}. No repair time is confirmed. Text {support}. {opt_out}",
    "extended": "{brand}: Service disruption {event_id} is ongoing. Our team is working on it. Text support {support}. {opt_out}",
    "maintenance": "{brand}: Planned maintenance {event_id}, {window}, may interrupt service. Text {support}. {opt_out}",
    "rescheduled": "{brand}: Maintenance {event_id} has been rescheduled to {window}. Text {support}. {opt_out}",
    "cancelled": "{brand}: Planned maintenance {event_id} has been cancelled. Text support {support}. {opt_out}",
    "restoration": "{brand}: Service restoration has been verified for {event_id}. If service remains unavailable, text {support}. {opt_out}",
}
DEFAULTS.update(
    {
        "backhaul": "{brand}: A backhaul outage is disrupting service in your area. Our technicians are investigating {event_id}. Text {support}. {opt_out}",
        "power": "{brand}: A verified tower power interruption is affecting service. Our technicians are investigating {event_id}. Text {support}. {opt_out}",
        "reminder": "{brand}: Reminder: planned maintenance {event_id} at {window} may interrupt service. Text {support}. {opt_out}",
        "delay": "{brand}: Maintenance {event_id} has been delayed; the revised window is {window}. Text {support}. {opt_out}",
    }
)
TEMPLATE_KEYS = tuple(DEFAULTS)
SEVERITY = {"info": 0, "warning": 1, "critical": 2}


def utc(v):
    return (
        v.replace(tzinfo=timezone.utc)
        if v.tzinfo is None
        else v.astimezone(timezone.utc)
    )


def clock():
    return datetime.now(timezone.utc)


def digest(v):
    return hashlib.sha256(
        json.dumps(v, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def contact_hash(v):
    return hmac.new(
        settings.secret_key.encode(), (v or "").encode(), hashlib.sha256
    ).hexdigest()


def audit(db, user_id, action, **details):
    db.add(
        AuditLog(
            user_id=user_id,
            action="notification." + action,
            entity_type="notification",
            entity_id=str(details.get("plan_id", "control")),
            details=details,
        )
    )


def lock(db):
    db.execute(text("SET LOCAL statement_timeout='5000ms'"))
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(950001)")):
        raise ValueError("Customer reconciliation is in progress; retry")
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(960001)")):
        raise ValueError("Notification work in progress; retry")


def policy(db):
    row = db.get(NotificationPolicy, 1)
    return Policy.model_validate(row.config if row else {})


def template(db, key):
    row = db.get(NotificationTemplate, key)
    return (row.body, row.revision) if row else (DEFAULTS[key], 0)


def validate_template(value):
    if not 20 <= len(value) <= 480:
        raise ValueError("Template must be 20–480 characters")
    try:
        fields = {
            f for _, f, fmt, conv in string.Formatter().parse(value) if f is not None
        }
    except ValueError:
        raise ValueError("Invalid template placeholders") from None
    if not {"brand", "support", "opt_out"} <= fields or fields - {
        "brand",
        "support",
        "opt_out",
        "event_id",
        "window",
    }:
        raise ValueError(
            "Use approved placeholders including brand, support and opt_out"
        )
    if any(
        fmt or conv
        for _, f, fmt, conv in string.Formatter().parse(value)
        if f is not None
    ):
        raise ValueError("Formatting operations are not supported")
    return value


def source(db, request):
    if request.incident_id:
        row = db.get(Incident, request.incident_id)
        if not row:
            raise LookupError()
        target = target_for(db, incident_id=row.id)
        severity = row.severity if row.severity in SEVERITY else "warning"
        return (
            row,
            target,
            {
                "incident_id": row.id,
                "revision": row.revision,
                "status": row.status,
                "target": vars(target),
                "started_at": utc(row.started_at).isoformat(),
                "resolved_at": (
                    utc(row.resolved_at or row.closed_at).isoformat()
                    if row.resolved_at or row.closed_at
                    else None
                ),
            },
            severity,
        )
    row = db.get(NotificationMaintenance, request.maintenance_id)
    if not row:
        raise LookupError()
    if row.operations_window_id:
        window = db.get(MaintenanceWindow, row.operations_window_id)
        if (
            not window
            or window.status != row.status
            or utc(window.starts_at) != utc(row.starts_at)
            or utc(window.ends_at) != utc(row.ends_at)
        ):
            raise ValueError(
                "Operations maintenance changed; review the linked schedule before messaging"
            )
    target = target_for(db, target=Target(**row.target))
    return (
        row,
        target,
        {
            "maintenance_id": row.id,
            "revision": row.revision,
            "status": row.status,
            "target": row.target,
            "targets": row.targets or [row.target],
            "starts_at": utc(row.starts_at).isoformat(),
            "ends_at": utc(row.ends_at).isoformat(),
        },
        "info",
    )


def probable_upstream(db, target, incident_id):
    site = target.site_id
    if not site and target.device_id:
        d = db.get(Device, target.device_id)
        site = d.wisp_site_id if d else None
    if not site or target.link_id:
        return []
    links = list(db.scalars(select(NetworkLink)))
    roots = set(db.scalars(select(Site.id).where(Site.site_type == "core-pop")))
    before = reachable(roots, links)
    if site not in before:
        return []
    candidates = []
    for i in db.scalars(
        select(Incident)
        .where(
            Incident.link_id.is_not(None),
            Incident.id != incident_id,
            Incident.status.not_in(["resolved", "closed"]),
        )
        .order_by(Incident.id)
        .limit(100)
    ):
        if site not in reachable(
            roots, links, excluded_links={i.link_id}, allow_unknown=True
        ):
            candidates.append(i.id)
    return candidates


def snapshot(db, request, now=None):
    now = now or clock()
    p = policy(db)
    row, target, context, severity = source(db, request)
    if request.incident_id and request.kind in {
        "maintenance",
        "rescheduled",
        "cancelled",
    }:
        raise ValueError("Maintenance event requires maintenance source")
    if request.maintenance_id and request.kind in {"outage", "update", "extended"}:
        raise ValueError("Outage event requires incident source")
    if request.kind == "restoration":
        ended = (
            (row.resolved_at or row.closed_at)
            if request.incident_id
            else (row.ends_at if row.status == "completed" else None)
        )
        if (
            row.status not in {"resolved", "closed", "completed"}
            or not ended
            or utc(ended) > now
            or not request.evidence.recovery_verified
            or request.evidence.recovery_samples < 3
            or not request.evidence.verified_at
            or not 0 <= (now - utc(request.evidence.verified_at)).total_seconds() <= 300
        ):
            raise ValueError(
                "Restoration requires ended source, three verified recovery samples and fresh operator evidence"
            )
    elif request.incident_id and row.status in {"resolved", "closed"}:
        raise ValueError("Incident is no longer active")
    if request.maintenance_id:
        expected = {
            "cancelled": "cancelled",
            "maintenance": "scheduled",
            "rescheduled": "scheduled",
        }
        if request.kind in expected and row.status != expected[request.kind]:
            raise ValueError("Maintenance lifecycle does not match message")
        if request.kind == "rescheduled" and row.revision < 2:
            raise ValueError("Maintenance has not been rescheduled")
    if (
        request.maintenance_id
        and request.kind in {"maintenance", "rescheduled"}
        and (
            utc(row.ends_at) <= now
            or request.due_at
            and utc(request.due_at) >= utc(row.starts_at)
        )
    ):
        raise ValueError("Advance notice must precede active future maintenance")
    if request.incident_id:
        context["probable_upstream_incident_ids"] = probable_upstream(
            db, target, request.incident_id
        )
    services = list(
        db.scalars(select(CustomerService).order_by(CustomerService.id).limit(10001))
    )
    if len(services) > 10000:
        raise ValueError(
            "Recipient set exceeds reviewed limit; no truncated campaign allowed"
        )
    targets = [Target(**t) for t in context.get("targets", [vars(target)])]
    evaluated = [analyze(db, t, services) for t in targets]
    rank = {"not_affected": 0, "unknown": 1, "probable": 2, "confirmed": 3}
    impacts = []
    for index in range(len(services)):
        choices = [group[index] for group in evaluated]
        best = max(choices, key=lambda r: rank[r["classification"]])
        best = dict(best)
        best["evidence"] = dict(
            best["evidence"],
            evaluated_scopes=[
                {
                    "classification": r["classification"],
                    "reason": r["reason"],
                    "target": r["evidence"]["target"],
                }
                for r in choices
            ],
        )
        impacts.append(best)
    selected = recipients(db, impacts)["recipients"]
    accounts = (
        {
            a.id: a
            for a in db.scalars(
                select(CustomerAccount).where(
                    CustomerAccount.id.in_([r["customer_id"] for r in selected])
                )
            )
        }
        if selected
        else {}
    )
    suppressed = set(db.scalars(select(NotificationSuppression.customer_id)))
    # Missing and unconfirmed evidence never becomes an automatic customer outage.
    sms_allowed = bool(
        request.maintenance_id
        or request.evidence.confirmed
        and request.evidence.event_type != "ups_warning"
    )
    if (
        request.incident_id
        and request.kind != "restoration"
        and (now - utc(row.started_at)).total_seconds() < p.minimum_outage_seconds
    ):
        sms_allowed = False
    # Count actual monitoring transitions only, never fabricated power telemetry.
    device_ids = set()
    from app.models.entities import Device, Sector

    for scope in targets:
        if scope.device_id:
            device_ids.add(scope.device_id)
        if scope.site_id:
            device_ids.update(
                db.scalars(
                    select(Device.id).where(Device.wisp_site_id == scope.site_id)
                )
            )
        if scope.sector_id:
            sector = db.get(Sector, scope.sector_id)
            if sector and sector.device_id:
                device_ids.add(sector.device_id)
        if scope.link_id:
            link = db.get(NetworkLink, scope.link_id)
            if link:
                device_ids.update(i for i in (link.a_device_id, link.z_device_id) if i)
    transitions = (
        dict(
            db.execute(
                select(MonitorEvent.device_id, func.count())
                .where(
                    MonitorEvent.device_id.in_(device_ids),
                    MonitorEvent.created_at
                    >= now - timedelta(seconds=p.flapping_window_seconds),
                    MonitorEvent.to_status.in_(["up", "down"]),
                )
                .group_by(MonitorEvent.device_id)
            ).all()
        )
        if device_ids
        else {}
    )
    flapping = any(n >= p.flapping_transition_limit for n in transitions.values())
    maintenance_services = set()
    if request.incident_id and request.kind != "restoration":
        scopes = []
        for window in db.scalars(
            select(MaintenanceWindow).where(
                MaintenanceWindow.status == "scheduled",
                MaintenanceWindow.starts_at <= now,
                MaintenanceWindow.ends_at >= now,
            )
        ):
            if window.link_id:
                scopes.append(Target(link_id=window.link_id))
            elif window.site_id:
                scopes.append(Target(site_id=window.site_id))
        for window in db.scalars(
            select(NotificationMaintenance).where(
                NotificationMaintenance.status == "scheduled",
                NotificationMaintenance.starts_at <= now,
                NotificationMaintenance.ends_at >= now,
            )
        ):
            scopes.extend(Target(**t) for t in (window.targets or [window.target]))
        for scope in scopes:
            maintenance_services.update(
                r["service_id"]
                for r in analyze(db, scope, services)
                if r["classification"] in {"confirmed", "probable"}
            )
    maintenance_suppressed = bool(
        maintenance_services
        & {
            r["service_id"]
            for r in impacts
            if r["classification"] in {"confirmed", "probable"}
        }
    )
    context["flapping_detected"] = flapping
    context["maintenance_suppressed"] = maintenance_suppressed
    if flapping:
        sms_allowed = False
    if request.evidence.event_type in {
        "high_utilization",
        "monitoring_failure",
        "acknowledgment",
    }:
        sms_allowed = False
    prior = set()
    if request.kind in {"restoration", "cancelled"}:
        s = (
            select(NotificationRecipient.customer_id)
            .join(
                NotificationDelivery,
                NotificationDelivery.recipient_id == NotificationRecipient.id,
            )
            .join(
                NotificationPlan, NotificationPlan.id == NotificationRecipient.plan_id
            )
            .where(
                NotificationDelivery.state == "simulated",
                NotificationDelivery.channel == "sms",
            )
        )
        s = (
            s.where(NotificationPlan.incident_id == request.incident_id)
            if request.incident_id
            else s.where(NotificationPlan.maintenance_id == request.maintenance_id)
        )
        prior = set(db.scalars(s))
    for r in selected:
        if not sms_allowed:
            r.update(
                eligible=False,
                reason=(
                    "flapping"
                    if flapping
                    else (
                        "planned_maintenance_suppression"
                        if maintenance_suppressed
                        else "unconfirmed_or_brief_event"
                    )
                ),
            )
        if r["customer_id"] in suppressed:
            r.update(eligible=False, reason="suppression_list")
        if (
            request.kind in {"restoration", "cancelled"}
            and r["customer_id"] not in prior
        ):
            r.update(eligible=False, reason="no_prior_notice")
        if r["eligible"] and not accounts[r["customer_id"]].consent_recorded_at:
            r.update(eligible=False, reason="consent_timestamp_unverified")
        r["consent_recorded_at"] = (
            utc(accounts[r["customer_id"]].consent_recorded_at).isoformat()
            if accounts[r["customer_id"]].consent_recorded_at
            else None
        )
        if (
            request.incident_id
            and request.kind != "restoration"
            and r["eligible"]
            and set(r["service_ids"]).issubset(maintenance_services)
        ):
            r.update(eligible=False, reason="planned_maintenance_suppression")
        r.pop("phone", None)
        r["contact_hash"] = contact_hash(accounts[r["customer_id"]].phone)
    if sum(r["eligible"] for r in selected) > p.max_sms_per_plan:
        raise ValueError(
            "Recipient storm limit exceeded; narrow scope or obtain reviewed policy change"
        )
    if request.critical_quiet_bypass and (
        severity != "critical" or not p.critical_quiet_bypass
    ):
        raise ValueError("Critical quiet-hours exception is not approved by policy")
    key = request.template_key or (
        "reminder"
        if request.reminder_minutes is not None
        else (
            "backhaul"
            if request.kind == "outage"
            and request.evidence.event_type == "backhaul_outage"
            and request.evidence.confirmed
            else (
                "power"
                if request.kind == "outage"
                and request.evidence.event_type == "tower_power"
                and request.evidence.confirmed
                else request.kind
            )
        )
    )
    allowed = {request.kind}
    if request.kind == "outage":
        allowed.update(["backhaul", "power"])
    if request.kind in {"maintenance", "rescheduled"}:
        allowed.update(["reminder", "delay"])
    if key == "power" and not (
        request.evidence.event_type == "tower_power" and request.evidence.confirmed
    ):
        raise ValueError("Power template requires confirmed power evidence")
    if key == "backhaul" and not (
        request.evidence.event_type == "backhaul_outage" and request.evidence.confirmed
    ):
        raise ValueError("Backhaul template requires confirmed backhaul evidence")
    if key not in allowed:
        raise ValueError("Template does not match operational event")
    body, revision = template(db, key)
    window = "not scheduled"
    if request.maintenance_id:
        zone = ZoneInfo(p.timezone)
        window = f"{utc(row.starts_at).astimezone(zone):%Y-%m-%d %H:%M %Z} to {utc(row.ends_at).astimezone(zone):%Y-%m-%d %H:%M %Z}"
    message = body.format(
        brand=BRAND,
        support=SUPPORT,
        opt_out=OPT_OUT,
        event_id=(
            ("incident-" + str(row.id))
            if request.incident_id
            else "maintenance-" + str(row.id)
        ),
        window=window,
    )
    if len(message) > 640:
        raise ValueError("Rendered message exceeds cost-reviewed limit")
    data = {
        "request": request.model_dump(mode="json"),
        "context": context,
        "policy": p.model_dump(),
        "template_revision": revision,
        "template_key": key,
        "template": body,
        "recipients": selected,
        "impacts": impacts,
        "message": message,
    }
    # A current source timestamp is not part of approval: stable unchanged rows
    # stay valid, while changed mappings/consent/paths invalidate fingerprints.
    return data, severity


def slack_payload(db, data, severity):
    t = data["context"]["target"]
    names = []
    for kind, model in [
        ("site_id", Site),
        ("device_id", Device),
        ("sector_id", Sector),
        ("link_id", NetworkLink),
    ]:
        if t.get(kind):
            r = db.get(model, t[kind])
            names.append(r.name if r else kind + " #" + str(t[kind]))

    def clean(v):
        v = re.sub(r"[^\s@]+@[^\s@]+", "[redacted]", v)
        v = re.sub(r"\+?[0-9][0-9 ()-]{8,}[0-9]", "[redacted]", v)
        return re.sub(r"[<>@&]", "", v)[:160]

    ctx = data["context"]
    incident = ctx.get("incident_id")
    metrics = {
        k: v
        for k, v in data["request"]["evidence"].items()
        if k in {"rssi_dbm", "snr_db", "temperature_c"} and v is not None
    }
    downstream = sorted(
        {
            r["evidence"]["site_id"]
            for r in data["impacts"]
            if r["classification"] in {"confirmed", "probable"}
            and r["evidence"]["site_id"]
            and r["evidence"]["site_id"] != t.get("site_id")
        }
    )
    fields = {
        "event": data["request"]["evidence"]["event_type"],
        "kind": data["request"]["kind"],
        "severity": severity,
        "incident_id": incident,
        "maintenance_id": ctx.get("maintenance_id"),
        "infrastructure": ", ".join(clean(n) for n in names)
        or "Explicit inventory target " + str(t),
        "detected_at": ctx.get("started_at", ctx.get("starts_at")),
        "potential_customer_count": len(
            {
                r["customer_id"]
                for r in data["impacts"]
                if r["classification"] in {"confirmed", "probable"}
            }
        ),
        "downstream_site_ids": downstream,
        "telemetry": metrics,
        "confidence": (
            "operator-confirmed dependency evidence"
            if data["request"]["evidence"]["confirmed"]
            else "suspected; customer outage unconfirmed"
        ),
        "incident_url": (
            (
                data["policy"].get("dashboard_base_url", "")
                + "/?incident="
                + str(incident)
            )
            if incident
            else data["policy"].get("dashboard_base_url", "") + "/?notifications=1"
        ),
        "simulation_only": True,
        "probable_upstream_incident_ids": ctx.get("probable_upstream_incident_ids", []),
    }
    return {
        "text": json.dumps(fields, sort_keys=True),
        "unfurl_links": False,
        "unfurl_media": False,
    }


def create_plan(db, request, user_id, now=None):
    lock(db)
    now = now or clock()
    if request.reminder_minutes is not None:
        if not request.maintenance_id or request.kind not in {
            "maintenance",
            "rescheduled",
        }:
            raise ValueError("Reminder requires scheduled maintenance")
        maintenance = db.get(NotificationMaintenance, request.maintenance_id)
        if not maintenance:
            raise LookupError()
        if request.reminder_minutes not in maintenance.advance_notice_minutes:
            raise ValueError("Reminder interval not configured for this maintenance")
        due = utc(maintenance.starts_at) - timedelta(minutes=request.reminder_minutes)
        if due < now:
            raise ValueError(
                "Reminder time has passed; explicitly review an immediate notice instead"
            )
        if request.due_at and utc(request.due_at) != due:
            raise ValueError("Reminder interval and explicit schedule conflict")
        request = request.model_copy(update={"due_at": due})
    data, severity = snapshot(db, request, now)
    ctx = data["context"]
    sourcekey = (
        f"i:{request.incident_id}"
        if request.incident_id
        else f'm:{request.maintenance_id}:r{ctx["revision"]}'
    )
    if (
        request.kind
        in {"outage", "restoration", "maintenance", "rescheduled", "cancelled"}
        and request.update_number != 0
    ):
        raise ValueError("Initial/lifecycle event uses one deduplicated revision")
    if request.kind in {"update", "extended"} and request.update_number < 1:
        raise ValueError("Updates require an explicit operator update number")
    key = digest(
        [sourcekey, request.kind, request.update_number]
        + ([request.reminder_minutes] if request.reminder_minutes is not None else [])
    )
    fp = digest(data)
    existing = db.scalar(
        select(NotificationPlan).where(NotificationPlan.dedupe_key == key)
    )
    if existing and existing.status != "draft":
        return existing
    if not existing:
        if (
            db.scalar(
                select(func.count(NotificationPlan.id)).where(
                    NotificationPlan.created_at >= now - timedelta(hours=1)
                )
            )
            >= policy(db).max_plans_per_hour
        ):
            raise ValueError("Notification event storm limit reached")
        existing = NotificationPlan(
            dedupe_key=key,
            incident_id=request.incident_id,
            maintenance_id=request.maintenance_id,
            kind=request.kind,
            severity=severity,
            confidence="confirmed" if request.evidence.confirmed else "suspected",
            target=ctx["target"],
            evidence=request.evidence.model_dump(mode="json"),
            snapshot={},
            fingerprint=fp,
            created_by=user_id,
            due_at=request.due_at or now,
            message="",
            slack_payload={},
        )
        db.add(existing)
        db.flush()
    existing.severity = severity
    existing.confidence = "confirmed" if request.evidence.confirmed else "suspected"
    existing.target = ctx["target"]
    existing.evidence = request.evidence.model_dump(mode="json")
    existing.due_at = request.due_at or now
    existing.snapshot = data
    existing.fingerprint = fp
    existing.message = data["message"]
    existing.slack_payload = slack_payload(db, data, severity)
    old = {
        r.customer_id: r
        for r in db.scalars(
            select(NotificationRecipient).where(
                NotificationRecipient.plan_id == existing.id
            )
        )
    }
    for r in old.values():
        r.state = "suppressed"
        r.reason = "no_longer_targeted"
        r.service_ids = []
    for item in data["recipients"]:
        r = old.get(item["customer_id"])
        if not r:
            r = NotificationRecipient(
                plan_id=existing.id, customer_id=item["customer_id"]
            )
            db.add(r)
        r.service_ids = item["service_ids"]
        r.contact_hash = item["contact_hash"]
        r.state = "eligible" if item["eligible"] else "suppressed"
        r.reason = item["reason"]
    p = policy(db)
    segments = max(1, math.ceil(len(existing.message.encode("utf-16-le")) / 2 / 67))
    existing.estimated_cost_microunits = (
        (
            sum(r["eligible"] for r in data["recipients"])
            * segments
            * p.sms_price_microunits
        )
        if p.sms_price_microunits is not None
        else None
    )
    audit(
        db,
        user_id,
        "plan_prepared",
        plan_id=existing.id,
        kind=request.kind,
        eligible=sum(r["eligible"] for r in data["recipients"]),
        simulation_only=True,
    )
    db.flush()
    return existing


def approve(db, ident, user_id, now=None, *, expected_fingerprint=None):
    lock(db)
    from app.notifications.controls import require_processing

    require_processing(db)
    now = now or clock()
    plan = db.get(NotificationPlan, ident)
    if not plan:
        raise LookupError()
    if expected_fingerprint is not None and expected_fingerprint != plan.fingerprint:
        raise ValueError(
            "Draft changed; review the current content and recipients before approval"
        )
    if plan.status != "draft":
        return plan
    current, _ = snapshot(db, PlanRequest.model_validate(plan.snapshot["request"]), now)
    if digest(current) != plan.fingerprint:
        raise ValueError(
            "Source, policy, consent or mapping changed; prepare the draft again before approval"
        )
    p = policy(db)
    rs = list(
        db.scalars(
            select(NotificationRecipient).where(
                NotificationRecipient.plan_id == ident,
                NotificationRecipient.state == "eligible",
            )
        )
    )
    # Reserve pending messages plus rolling 24h delivered simulations. This is
    # conservative for future schedules; no budget/rate limit can be evaded by queues.
    reserved = db.scalar(
        select(func.count(NotificationDelivery.id)).where(
            NotificationDelivery.channel == "sms",
            or_(
                NotificationDelivery.state.in_(["queued", "retry"]),
                NotificationDelivery.completed_at >= now - timedelta(days=1),
            ),
        )
    )
    if reserved + len(rs) > p.max_sms_per_day:
        raise ValueError("Daily SMS reservation cap reached")
    if plan.estimated_cost_microunits is not None:
        costs = db.scalar(
            select(
                func.coalesce(func.sum(NotificationPlan.estimated_cost_microunits), 0)
            ).where(
                NotificationPlan.approved_at >= now - timedelta(days=1),
                NotificationPlan.status.in_(["approved", "complete"]),
            )
        )
        if costs + plan.estimated_cost_microunits > p.daily_budget_microunits:
            raise ValueError("Daily estimated SMS budget exceeded")
    for r in rs:
        db.add(
            NotificationDelivery(
                plan_id=ident,
                channel="sms",
                recipient_key=str(r.customer_id),
                recipient_id=r.id,
                next_attempt_at=plan.due_at,
                budget_charge_microunits=(
                    (plan.estimated_cost_microunits // len(rs))
                    if plan.estimated_cost_microunits is not None
                    else None
                ),
            )
        )
    if SEVERITY[plan.severity] >= SEVERITY[p.slack_min_severity] or plan.maintenance_id:
        db.add(
            NotificationDelivery(
                plan_id=ident,
                channel="slack",
                recipient_key=(
                    p.slack_maintenance_channel
                    if plan.maintenance_id
                    else (
                        p.slack_critical_channel
                        if plan.severity == "critical"
                        else p.slack_channel
                    )
                ),
                next_attempt_at=plan.due_at,
            )
        )
        ctx = plan.snapshot["context"]
        if plan.severity == "critical":
            db.add(
                NotificationDelivery(
                    plan_id=ident,
                    channel="slack",
                    recipient_key=p.escalation_channel,
                    next_attempt_at=max(utc(plan.due_at), now)
                    + timedelta(seconds=p.escalation_after_seconds),
                )
            )
    db.add(
        NotificationDelivery(
            plan_id=ident,
            channel="internal",
            recipient_key="dashboard",
            next_attempt_at=plan.due_at,
        )
    )
    plan.status = "approved"
    plan.approved_by = user_id
    plan.approved_at = now
    audit(
        db,
        user_id,
        "approved",
        plan_id=ident,
        sms_recipients=len(rs),
        simulation_only=True,
    )
    db.flush()
    return plan


def quiet_due(now, p, bypass=False):
    if bypass:
        return now
    zone = ZoneInfo(p.timezone)
    local = now.astimezone(zone)
    hour = local.hour
    quiet = (
        (p.quiet_start <= hour < p.quiet_end)
        if p.quiet_start < p.quiet_end
        else (hour >= p.quiet_start or hour < p.quiet_end)
    )
    if not quiet:
        return now
    day = local.date() + timedelta(
        days=1 if hour >= p.quiet_start and p.quiet_start > p.quiet_end else 0
    )
    return datetime.combine(day, time(p.quiet_end), zone).astimezone(timezone.utc)
