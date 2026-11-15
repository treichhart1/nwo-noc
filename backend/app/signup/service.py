import json, math, hashlib
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select, func, or_, text
from cryptography.fernet import InvalidToken
from app.models.entities import User, Site, Sector, NetworkLink, AuditLog
from app.customers.models import CustomerAccount, CustomerService
from app.field import service as field
from app.field.models import WorkOrder, now
from app.field.contracts import WorkInput as FieldWorkInput
from app.equipment import service as inventory
from app.core.config import settings
from app.tower_health.models import TowerCurrent
from app.maintenance_ops import storage
from app.maintenance_ops.service import trusted_clean
from .models import *
from . import crypto, public_service, providers
from .contracts import STATES

CLOSED = {"completed", "rejected", "cancelled"}
GRAPH = {
    "submitted": {"under_review", "cancelled", "rejected"},
    "under_review": {
        "qualification_pending",
        "site_survey_required",
        "cancelled",
        "rejected",
    },
    "qualification_pending": {
        "qualified",
        "site_survey_required",
        "under_review",
        "cancelled",
        "rejected",
    },
    "site_survey_required": {
        "survey_scheduled",
        "under_review",
        "qualification_pending",
        "cancelled",
        "rejected",
    },
    "survey_scheduled": {
        "qualified",
        "site_survey_required",
        "under_review",
        "cancelled",
        "rejected",
    },
    "qualified": {"quote_pending", "under_review", "cancelled"},
    "quote_pending": {"under_review", "cancelled"},
    "quote_sent": {"quote_pending", "under_review", "cancelled"},
    "customer_accepted": {"installation_pending", "under_review", "cancelled"},
    "installation_pending": {"under_review", "cancelled"},
    "installation_scheduled": {"installed", "under_review", "cancelled"},
    "installed": {"activation_pending", "under_review", "cancelled"},
    "activation_pending": {"completed", "under_review", "cancelled"},
    "completed": set(),
    "rejected": set(),
    "cancelled": set(),
}
TEMPLATES = {
    "application_received": "We received your service application for review. Availability is not guaranteed.",
    "information_required": "Please contact our office about information needed for your application.",
    "survey_scheduling": "Please contact our office to confirm a proposed site survey.",
    "quote_available": "An authorized quote is available for your review. No payment is processed here.",
    "installation_scheduling": "Please contact our office to confirm proposed installation arrangements.",
    "status_update": "Your application has an update. Contact our office using your receipt.",
}


def staff(u):
    if u.role not in {"admin", "operator"}:
        raise PermissionError("Application operator required")


def audit(db, u, action, id):
    db.add(
        AuditLog(
            user_id=u.id,
            action="signup." + action,
            entity_type="signup_application",
            entity_id=str(id),
            details={"documentation_only": True, "external_delivery": False},
        )
    )


def get(db, id):
    return field.get(db, Application, id)


def check(a, revision):
    if not a.wrapped_key or a.redacted_at:
        raise ValueError("Applicant data has been redacted")
    if a.revision != revision:
        raise ValueError("Application revision changed")


def event(db, a, u, action, reason, data=None):
    db.add(
        Event(
            application_id=a.id,
            actor_id=u.id if u else None,
            action=action,
            reason_ciphertext=crypto.seal(a, reason) if a.wrapped_key else None,
            data=data or {},
        )
    )
    a.updated_at = now()
    a.revision += 1
    if u:
        audit(db, u, action, a.id)


def pii(a):
    return crypto.unseal(a, a.pii_ciphertext)


def work_access(db, u, mapping):
    w = field.get(db, WorkOrder, mapping.work_order_id)
    if u.role not in {"admin", "operator"} and (
        u.role != "technician" or w.technician_id != u.id
    ):
        raise PermissionError("Assigned application work required")
    return w


def matches(db, a):
    data = pii(a)
    if data.get("redacted"):
        return {"customers": [], "services": [], "applications": []}
    accounts = db.scalars(
        select(CustomerAccount)
        .where(
            CustomerAccount.source_deleted_at.is_(None),
            or_(
                func.lower(CustomerAccount.email) == data["email"].lower(),
                CustomerAccount.phone == data["phone"],
            ),
        )
        .order_by(CustomerAccount.id)
        .limit(20)
    ).all()
    services = db.scalars(
        select(CustomerService)
        .where(CustomerService.source_deleted_at.is_(None))
        .order_by(CustomerService.id)
        .limit(5001)
    ).all()
    addr = public_service.normalized_address(data["service_address"])
    compact = " ".join(
        replace
        for replace in [
            data["service_address"]["street"],
            data["service_address"].get("unit", ""),
            data["service_address"]["city"],
            data["service_address"]["state"],
            data["service_address"]["postal_code"],
        ]
        if replace
    ).upper()
    found = []
    for s in services[:5000]:
        if s.customer_id in {c.id for c in accounts} or (
            s.address
            and " ".join(s.address.upper().replace(",", " ").split()) == compact
        ):
            found.append(
                {
                    "id": s.id,
                    "external_id": s.external_id,
                    "customer_id": s.customer_id,
                    "plan": s.plan,
                    "service_type": s.service_type,
                    "site_id": s.site_id,
                    "sector_id": s.sector_id,
                    "mapping_state": s.mapping_state,
                    "basis": "Exact contact or normalized documented address; ownership not verified",
                }
            )
    duplicates = db.scalars(
        select(Application)
        .where(
            Application.id != a.id,
            or_(
                Application.email_hash == a.email_hash,
                Application.phone_hash == a.phone_hash,
                Application.address_hash == a.address_hash,
            ),
        )
        .order_by(Application.id.desc())
        .limit(20)
    ).all()
    return {
        "customers": [
            {
                "id": c.id,
                "external_id": c.external_id,
                "sms_opt_out": c.sms_opt_out,
                "last_synced_at": c.last_synced_at.isoformat(),
                "basis": "Exact contact match; operator verification required",
            }
            for c in accounts
        ],
        "services": found[:50],
        "applications": [
            {
                "id": r.id,
                "reference": r.reference,
                "state": r.state,
                "basis": "Potential duplicate; never auto-merged",
            }
            for r in duplicates
        ],
        "truncated": len(services) > 5000,
        "production_writes": 0,
    }


def distance(lat, lon, lat2, lon2):
    r1, r2 = map(math.radians, (lat, lat2))
    dlat = r2 - r1
    dlon = math.radians(lon2 - lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


def bearing(lat1, lon1, lat2, lon2):
    p1, p2 = map(math.radians, (lat1, lat2))
    d = math.radians(lon2 - lon1)
    return (
        math.degrees(
            math.atan2(
                math.sin(d) * math.cos(p2),
                math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(d),
            )
        )
        + 360
    ) % 360


def contains(poly, lat, lon):
    inside = False
    for a, b in zip(poly, poly[1:]):
        x1, y1 = a
        x2, y2 = b
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def qualify(db, u, a, b):
    staff(u)
    check(a, b.expected_revision)
    data = pii(a)
    coords = None
    if b.latitude is not None:
        coords = {
            "latitude": b.latitude,
            "longitude": b.longitude,
            "source": b.coordinate_source,
        }
    elif b.use_provider:
        if not b.consent_address_transfer:
            raise ValueError("Explicit address-provider transfer approval required")
        minute = now().replace(second=0, microsecond=0)
        key = crypto.digest("geocoder-rate", "provider")
        if db.get(PublicBudget, (key, minute)):
            raise ValueError("Provider request budget used; retry next minute")
        db.add(PublicBudget(key=key, window=minute, count=1))
        db.flush()
        coords = providers.geocode(data["service_address"])
    snapshot = {
        "preliminary": True,
        "status": "requires_site_survey",
        "evidence": [],
        "uncertainty": [
            "No guaranteed service, signal, line of sight, construction cost or service date"
        ],
        "signal_estimate": "unavailable",
        "line_of_sight": "unverified",
        "terrain": "unavailable unless recorded by technician",
        "capacity": "not inferred from proximity or interface throughput",
    }
    if not coords:
        snapshot["evidence"].append(
            {
                "reason": "No validated address coordinates; site survey or approved geocoding required"
            }
        )
    else:
        data["coordinates"] = coords
        a.pii_ciphertext = crypto.seal(a, data)
        lat, lon = coords["latitude"], coords["longitude"]
        sites = db.scalars(
            select(Site)
            .where(Site.latitude.is_not(None), Site.longitude.is_not(None))
            .order_by(Site.id)
            .limit(1001)
        ).all()
        if len(sites) > 1000:
            raise ValueError("Qualification tower scope exceeds1000")
        nearby = sorted(
            [(distance(lat, lon, t.latitude, t.longitude), t) for t in sites],
            key=lambda x: x[0],
        )
        candidates = [(d, t) for d, t in nearby if d <= 30][:10]
        ids = [t.id for d, t in candidates]
        sectors = db.scalars(
            select(Sector).where(Sector.site_id.in_(ids)).order_by(Sector.id).limit(500)
        ).all()
        current = {
            r.site_id: r
            for r in db.scalars(
                select(TowerCurrent).where(TowerCurrent.site_id.in_(ids))
            )
        }
        links = db.scalars(
            select(NetworkLink)
            .where(or_(NetworkLink.a_site_id.in_(ids), NetworkLink.z_site_id.in_(ids)))
            .limit(100)
        ).all()
        for d, t in candidates:
            radios = []
            for s in sectors:
                if s.site_id != t.id:
                    continue
                angle = (
                    None
                    if s.azimuth is None
                    else abs(
                        (bearing(t.latitude, t.longitude, lat, lon) - s.azimuth + 180)
                        % 360
                        - 180
                    )
                )
                aligned = (
                    angle is not None
                    and s.beamwidth is not None
                    and angle <= s.beamwidth / 2
                )
                radios.append(
                    {
                        "sector_id": s.id,
                        "direction_match": aligned,
                        "direction_known": angle is not None
                        and s.beamwidth is not None,
                        "frequency_mhz": s.frequency_mhz,
                        "documented_capacity_mbps": s.capacity_mbps,
                        "signal_prediction": "unavailable",
                    }
                )
            health = current.get(t.id)
            fresh = bool(
                health and 0 <= (now() - health.assessed_at).total_seconds() <= 300
            )
            snapshot["evidence"].append(
                {
                    "tower_id": t.id,
                    "distance_km": round(d, 3),
                    "sectors": radios,
                    "monitoring_state": (
                        health.report.get("state", "unknown") if fresh else "unknown"
                    ),
                    "monitoring_fresh": fresh,
                    "backhaul_links": [
                        {
                            "id": l.id,
                            "documented_capacity_mbps": l.capacity_mbps,
                            "link_type": l.link_type,
                        }
                        for l in links
                        if t.id in {l.a_site_id, l.z_site_id}
                    ],
                    "reason": "Geographic/directional candidate only; RF/LOS and available capacity unverified",
                }
            )
        fiber = db.execute(
            select(NetworkLink, Site)
            .join(Site, NetworkLink.a_site_id == Site.id)
            .where(NetworkLink.link_type == "fiber")
            .limit(501)
        ).all()
        if len(fiber) > 500:
            snapshot["uncertainty"].append(
                "Fiber distance omitted: documented link scope exceeds500"
            )
        else:
            ends = {t.id: t for t in sites}
            distances = []
            for link, start in fiber:
                end = ends.get(link.z_site_id)
                if end and start.latitude is not None and start.longitude is not None:
                    # Local equirectangular projection; only distance to straight documented endpoints.
                    scale = math.cos(math.radians(lat))
                    ax, ay = (start.longitude - lon) * scale, start.latitude - lat
                    bx, by = (end.longitude - lon) * scale, end.latitude - lat
                    dx, dy = bx - ax, by - ay
                    fraction = (
                        max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
                        if dx * dx + dy * dy
                        else 0
                    )
                    km = math.hypot(ax + fraction * dx, ay + fraction * dy) * 111.195
                    distances.append(
                        {
                            "link_id": link.id,
                            "distance_km": round(km, 3),
                            "basis": "Straight line between documented endpoints; actual route, spare fibers and connection rights unverified",
                        }
                    )
            snapshot["nearby_documented_fiber"] = sorted(
                distances, key=lambda v: v["distance_km"]
            )[:10]
        areas = db.scalars(select(Area).order_by(Area.id).limit(1001)).all()
        if len(areas) > 1000:
            raise ValueError("Coverage scope exceeds1000")
        matched = [r for r in areas if contains(r.polygon, lat, lon)]
        active = [
            r
            for r in matched
            if r.status == "active" and r.technology != "service_boundary"
        ]
        planned = [r for r in matched if r.status == "planned"]
        snapshot["coverage"] = [
            {
                "id": r.id,
                "technology": r.technology,
                "status": r.status,
                "source": r.evidence,
                "updated_at": r.updated_at.isoformat(),
            }
            for r in matched
        ]
        snapshot["fiber_construction"] = (
            "review_required"
            if any(r.technology in {"fiber", "hybrid"} for r in planned)
            else "unknown"
        )
        if active:
            snapshot["status"] = "potentially_serviceable"
        elif planned or candidates:
            snapshot["status"] = "requires_site_survey"
        elif any(
            r.technology == "service_boundary" and r.status == "active" for r in areas
        ) and not any(r.technology == "service_boundary" for r in matched):
            snapshot["status"] = "outside_current_service_area"
        else:
            snapshot["status"] = "no_confirmed_coverage"
        snapshot["technology_options"] = sorted(
            {r.technology for r in active + planned}
        )
        snapshot["coordinate_source"] = coords["source"]
        snapshot["evidence"].append(
            {
                "reason": "Nearby/planned fiber does not establish a customer connection, port availability or construction commitment"
            }
        )
    q = Qualification(application_id=a.id, actor_id=u.id, snapshot=snapshot)
    db.add(q)
    db.flush()
    event(
        db,
        a,
        u,
        "qualification_recorded",
        b.reason,
        {"qualification_id": q.id, "advisory_only": True},
    )
    return inventory.view(q)


def update(db, u, a, b):
    staff(u)
    check(a, b.expected_revision)
    if b.state not in STATES:
        raise ValueError("Invalid state")
    if b.state != a.state and b.state not in GRAPH[a.state]:
        if not (u.role == "admin" and a.state in CLOSED and b.state == "under_review"):
            raise ValueError("Invalid lifecycle transition")
    if b.state in {
        "qualified",
        "installation_pending",
        "installed",
        "activation_pending",
        "completed",
    }:
        field.admin(u)
    if b.state == "qualified" and (
        not b.approved_qualification or not b.proposed_site_id
    ):
        raise ValueError("Explicit approved qualification and serving site required")
    if b.state == "installed":
        link = db.scalar(
            select(AppWork).where(
                AppWork.application_id == a.id, AppWork.kind == "installation"
            )
        )
        if (
            not link
            or field.get(db, WorkOrder, link.work_order_id).status != "completed"
        ):
            raise ValueError("Completed installation documentation required")
    if (
        b.state in {"activation_pending", "completed"}
        and not b.acknowledged_no_provisioning
    ):
        raise ValueError(
            "Acknowledge documentation-only activation status; no service activation performed"
        )
    if b.assigned_to:
        person = field.get(db, User, b.assigned_to)
        if not person.is_active or person.role not in {"admin", "operator"}:
            raise ValueError("Active application operator required")
    if b.proposed_site_id:
        field.get(db, Site, b.proposed_site_id)
        a.proposed_site_id = b.proposed_site_id
    a.assigned_to = b.assigned_to
    old = a.state
    a.state = b.state
    a.closed_at = now() if a.state in CLOSED else None
    event(
        db,
        a,
        u,
        "state_changed",
        b.reason,
        {"before": old, "after": a.state, "provisioning_changes": 0},
    )
    return {"id": a.id, "state": a.state, "revision": a.revision}


def make_work(db, u, a, b):
    staff(u)
    check(a, b.expected_revision)
    existing = db.scalar(
        select(AppWork).where(AppWork.application_id == a.id, AppWork.kind == b.kind)
    )
    if existing:
        return {
            "id": existing.id,
            "work_order_id": existing.work_order_id,
            "duplicate": True,
        }
    if b.kind == "survey" and a.state != "site_survey_required":
        raise ValueError("Application must require site survey")
    if b.kind == "installation" and (
        a.state != "installation_pending" or a.proposed_site_id != b.site_id
    ):
        raise ValueError(
            "Administrator-approved installation and serving site required"
        )
    if b.sector_id and field.get(db, Sector, b.sector_id).site_id != b.site_id:
        raise ValueError("Sector/site mismatch")
    if b.fiber_link_id:
        l = field.get(db, NetworkLink, b.fiber_link_id)
        if l.link_type != "fiber" or b.site_id not in {l.a_site_id, l.z_site_id}:
            raise ValueError("Documented fiber link/site required")
    w = field.create_work(
        db,
        u,
        FieldWorkInput(
            operation_key=b.operation_key,
            reason="Authorized customer application work; contact details remain in protected application context",
            site_id=b.site_id,
            technician_id=b.technician_id,
            description=f"Customer {b.kind} for {a.reference}",
            scheduled_at=b.scheduled_at,
            required_equipment=b.equipment,
        ),
        customer_application_authorized=True,
    )
    m = AppWork(application_id=a.id, kind=b.kind, work_order_id=w["id"])
    db.add(m)
    db.flush()
    a.proposed_site_id = b.site_id
    a.state = "survey_scheduled" if b.kind == "survey" else "installation_scheduled"
    event(
        db,
        a,
        u,
        "work_order_created",
        b.reason,
        {
            "work_order_id": w["id"],
            "kind": b.kind,
            "sector_id": b.sector_id,
            "fiber_link_id": b.fiber_link_id,
            "dispatches": 0,
        },
    )
    db.flush()
    return {
        "id": m.id,
        "work_order_id": w["id"],
        "duplicate": False,
        "revision": a.revision,
    }


def survey(db, u, a, b):
    check(a, b.expected_revision)
    m = field.get(db, AppWork, b.app_work_id)
    w = work_access(db, u, m)
    if (
        m.application_id != a.id
        or m.kind != "survey"
        or w.status not in {"assigned", "scheduled", "in_progress"}
    ):
        raise ValueError("Assigned active site survey required")
    if (
        b.proposed_sector_id
        and field.get(db, Sector, b.proposed_sector_id).site_id != b.proposed_site_id
    ):
        raise ValueError("Sector/site mismatch")
    if b.proposed_site_id != w.site_id:
        raise ValueError("Survey site differs from authorized work site")
    if (b.latitude is None) != (b.longitude is None):
        raise ValueError("Paired coordinates required")
    for id in b.photo_ids:
        p = field.get(db, Photo, id)
        if p.app_work_id != m.id or not p.approved or not trusted_clean(p):
            raise ValueError("Approved scoped survey photo required")
    row = Survey(
        application_id=a.id,
        app_work_id=m.id,
        technician_id=u.id,
        surveyed_at=b.surveyed_at,
        outcome=b.outcome,
        details_ciphertext=crypto.seal(
            a,
            b.model_dump(
                mode="json", exclude={"operation_key", "reason", "expected_revision"}
            ),
        ),
    )
    db.add(row)
    db.flush()
    event(
        db,
        a,
        u,
        "survey_recorded",
        b.reason,
        {"survey_id": row.id, "advisory_only": True},
    )
    field.event(
        db,
        w,
        u,
        "application_survey_recorded",
        "Customer survey documented in protected application",
        {"application_id": a.id, "survey_id": row.id},
    )
    return {"id": row.id, "outcome": row.outcome, "revision": a.revision}


def quote_draft(db, u, a, b):
    staff(u)
    check(a, b.expected_revision)
    if a.state not in {"qualified", "quote_pending", "quote_sent"}:
        raise ValueError("Qualified application required for quote draft")
    p = field.get(db, Plan, b.plan_id)
    if (
        not p.active
        or p.technology != a.technology
        or p.audience not in {"both", a.audience}
    ):
        raise ValueError("Eligible active plan required")
    n = (
        db.scalar(select(func.max(Quote.number)).where(Quote.application_id == a.id))
        or 0
    ) + 1
    q = Quote(
        application_id=a.id,
        number=n,
        plan_id=p.id,
        snapshot=b.model_dump(
            mode="json",
            exclude={"reason", "operation_key", "expected_revision", "expires_at"},
        )
        | {
            "plan_name": p.name,
            "plan_catalog_revision": p.revision,
            "commitment": "Manual quote only; no payment or contract execution",
        },
        expires_at=b.expires_at,
    )
    db.add(q)
    db.flush()
    a.state = "quote_pending"
    event(db, a, u, "quote_drafted", b.reason, {"quote_id": q.id, "number": n})
    return inventory.view(q)


def control(db, u, a, b):
    field.admin(u)
    check(a, b.expected_revision)
    if b.action in {"approve_quote", "issue_quote", "record_acceptance"}:
        q = field.get(db, Quote, b.quote_id)
        latest = db.scalar(
            select(func.max(Quote.number)).where(Quote.application_id == a.id)
        )
        if q.application_id != a.id or q.number != latest or q.expires_at <= now():
            raise ValueError("Current nonexpired quote required")
        if b.action == "approve_quote":
            if q.status != "draft" or any(
                q.snapshot.get(k) is None
                for k in ["monthly", "installation", "equipment", "construction"]
            ):
                raise ValueError(
                    "All charges must be explicitly known, including zero, before approval"
                )
            q.status = "approved"
            q.approved_by = u.id
        elif b.action == "issue_quote":
            if q.status != "approved" or a.state != "quote_pending":
                raise ValueError("Approved pending quote required")
            q.status = "issued"
            q.issued_at = now()
            a.state = "quote_sent"
        else:
            if q.status != "issued" or a.state != "quote_sent":
                raise ValueError(
                    "Issued quote and explicit customer acceptance evidence required"
                )
            q.status = "accepted"
            q.acceptance_ciphertext = crypto.seal(a, b.evidence)
            a.state = "customer_accepted"
    elif b.action == "approve_match":
        customer = field.get(db, CustomerAccount, b.customer_id)
        if customer.source_deleted_at:
            raise ValueError("Active documented customer required")
        if customer.id not in {r["id"] for r in matches(db, a)["customers"]}:
            raise ValueError(
                "Exact contact evidence required before documentation match"
            )
        a.matched_customer_id = customer.id
    elif b.action == "prepare_provisioning":
        if a.state not in {
            "customer_accepted",
            "installation_pending",
            "installation_scheduled",
            "installed",
            "activation_pending",
            "completed",
        }:
            raise ValueError("Approved customer acceptance required")
        row = db.scalar(
            select(ProvisionDraft).where(ProvisionDraft.application_id == a.id)
        )
        if not row:
            db.add(ProvisionDraft(application_id=a.id, approved_by=u.id))
    elif b.action == "hold":
        a.legal_hold = True
    elif b.action == "release_hold":
        a.legal_hold = False
    elif b.action == "redact":
        if (
            a.legal_hold
            or a.state not in CLOSED
            or not a.closed_at
            or a.closed_at > now() - timedelta(days=settings.signup_retention_days)
        ):
            raise ValueError(
                "Closed retention-eligible application without legal hold required"
            )
        event(db, a, u, "privacy_redacted", b.reason, {"wrapped_key_destroyed": True})
        a.wrapped_key = None
        a.pii_ciphertext = None
        a.lookup_hash = None
        a.email_hash = None
        a.phone_hash = None
        a.address_hash = None
        a.redacted_at = now()
        return {"id": a.id, "redacted": True, "revision": a.revision}
    event(
        db,
        a,
        u,
        b.action,
        b.reason,
        {"state": a.state, "powercode_writes": 0, "external_delivery": False},
    )
    return {
        "id": a.id,
        "state": a.state,
        "revision": a.revision,
        "production_changes": 0,
    }


def notification(db, u, a, b):
    staff(u)
    check(a, b.expected_revision)
    data = pii(a)
    allowed = (
        bool(data.get("consent_" + b.channel))
        and data.get("preferred_contact") == b.channel
    )
    matching = matches(db, a)
    if b.channel == "sms" and any(c["sms_opt_out"] for c in matching["customers"]):
        allowed = False
    r = NotificationDraft(
        application_id=a.id,
        template=b.template,
        channel=b.channel,
        eligible=allowed,
        created_by=u.id,
        policy={
            "foundation": "NOC96 consent-aware draft only; no incident fabricated",
            "text": TEMPLATES[b.template],
            "recipient": "Protected application reference only",
            "reason": (
                "Explicit current preference/consent and opt-out review"
                if allowed
                else "Consent/preference or imported opt-out suppresses contact"
            ),
            "external_delivery": False,
        },
    )
    db.add(r)
    db.flush()
    event(
        db,
        a,
        u,
        "notification_drafted",
        b.reason,
        {"draft_id": r.id, "eligible": allowed, "external_delivery": False},
    )
    return inventory.view(r)


def upload_photo(db, u, a, b):
    m = field.get(db, AppWork, b.app_work_id)
    work_access(db, u, m)
    if m.application_id != a.id or m.kind != "survey" or not a.wrapped_key:
        raise ValueError("Active scoped survey required")
    if b.restricted_financial or b.media_type not in {"image/png", "image/jpeg"}:
        raise ValueError("Survey photographs only")
    if (
        db.scalar(
            select(func.count()).select_from(Photo).where(Photo.app_work_id == m.id)
        )
        >= 20
    ):
        raise ValueError("Photo limit20")
    raw = inventory.verify_file(b)
    state, source = storage.scan(raw)
    encrypted = crypto.data_cipher(a).encrypt(raw)
    key = storage.put(encrypted)
    p = Photo(
        application_id=a.id,
        app_work_id=m.id,
        actor_id=u.id,
        filename=b.filename,
        media_type=b.media_type,
        storage_key=key,
        sha256=hashlib.sha256(raw).hexdigest(),
        size=len(raw),
        scan_state=state,
        scan_source=source,
    )
    db.add(p)
    db.flush()
    event(db, a, u, "survey_photo_uploaded", b.reason, {"photo_id": p.id})
    return inventory.view(p, exclude={"storage_key"})


def photo_bytes(a, p):
    if not a.wrapped_key or not p.approved or not trusted_clean(p):
        raise PermissionError("Approved clean active photo required")
    try:
        raw = crypto.data_cipher(a).decrypt(storage.read(p.storage_key))
    except InvalidToken:
        raise ValueError("Photo integrity unavailable") from None
    if len(raw) != p.size or hashlib.sha256(raw).hexdigest() != p.sha256:
        raise ValueError("Photo integrity mismatch")
    return raw


def detail(db, u, a, offset=0):
    staff(u)
    data = pii(a)
    audit(db, u, "viewed", a.id)
    return inventory.view(
        a,
        exclude={
            "wrapped_key",
            "pii_ciphertext",
            "digest",
            "lookup_hash",
            "email_hash",
            "phone_hash",
            "address_hash",
            "submission_key",
        },
    ) | {
        "applicant": data,
        "matches": matches(db, a),
        "qualifications": [
            inventory.view(r)
            for r in db.scalars(
                select(Qualification)
                .where(Qualification.application_id == a.id)
                .order_by(Qualification.id.desc())
                .limit(20)
            )
        ],
        "quotes": [
            inventory.view(q, exclude={"acceptance_ciphertext"})
            for q in db.scalars(
                select(Quote)
                .where(Quote.application_id == a.id)
                .order_by(Quote.number.desc())
                .limit(50)
            )
        ],
        "work_orders": [
            inventory.view(w)
            for w in db.scalars(select(AppWork).where(AppWork.application_id == a.id))
        ],
        "surveys": [
            inventory.view(r, exclude={"details_ciphertext"})
            | {"details": crypto.unseal(a, r.details_ciphertext)}
            for r in db.scalars(
                select(Survey)
                .where(Survey.application_id == a.id)
                .order_by(Survey.id.desc())
                .limit(50)
            )
        ],
        "events": [
            inventory.view(e, exclude={"reason_ciphertext"})
            | {"reason": crypto.unseal(a, e.reason_ciphertext)}
            for e in db.scalars(
                select(Event)
                .where(Event.application_id == a.id)
                .order_by(Event.id.desc())
                .offset(offset)
                .limit(50)
            )
        ],
        "notification_drafts": [
            inventory.view(r)
            for r in db.scalars(
                select(NotificationDraft)
                .where(NotificationDraft.application_id == a.id)
                .order_by(NotificationDraft.id.desc())
                .limit(50)
            )
        ],
    }


def work_context(db, u, work_id):
    m = db.scalar(select(AppWork).where(AppWork.work_order_id == work_id))
    if not m:
        raise LookupError("No customer application context")
    w = work_access(db, u, m)
    a = get(db, m.application_id)
    if not a.wrapped_key:
        raise PermissionError("Applicant retention redacted")
    data = pii(a)
    audit(db, u, "work_context_viewed", a.id)
    proposal = db.scalar(
        select(Event)
        .where(
            Event.application_id == a.id,
            Event.action == "work_order_created",
            Event.data["work_order_id"].as_integer() == w.id,
        )
        .order_by(Event.id.desc())
        .limit(1)
    )
    return {
        "application_id": a.id,
        "reference": a.reference,
        "revision": a.revision,
        "app_work_id": m.id,
        "kind": m.kind,
        "work_order_id": w.id,
        "service_address": data["service_address"],
        "customer_name": data["customer_name"],
        "business_name": data.get("business_name"),
        "contact": {
            "preferred": data["preferred_contact"],
            "email": data["email"] if data.get("consent_email") else None,
            "phone": (
                data["phone"]
                if data.get("consent_phone") or data.get("consent_sms")
                else None
            ),
        },
        "technology": a.technology,
        "requested_plan": a.plan_snapshot["name"],
        "installation_preferences": data["installation_preferences"],
        "site_id": w.site_id,
        "network_proposal": {
            "site_id": w.site_id,
            "sector_id": proposal.data.get("sector_id") if proposal else None,
            "fiber_link_id": proposal.data.get("fiber_link_id") if proposal else None,
            "status": "Documentation only; connection and capacity require approved verification",
        },
        "required_equipment": w.required_equipment,
        "surveys": [
            crypto.unseal(a, r.details_ciphertext)
            for r in db.scalars(
                select(Survey)
                .where(Survey.application_id == a.id)
                .order_by(Survey.id.desc())
                .limit(20)
            )
        ],
        "photos": [
            inventory.view(r, exclude={"storage_key"})
            for r in db.scalars(select(Photo).where(Photo.app_work_id == m.id))
        ],
        "configuration_changes": 0,
    }
