"""Human-reviewed cancellation evidence. Never changes canonical service/billing state."""

import csv
import io
import json
import secrets
import hashlib
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select, func, text
from app.models.entities import AuditLog, Incident
from app.customers.models import CustomerAccount, CustomerService, IncidentCustomer
from app.customers.powercode_models import PowercodeServiceHistory
from app.signup import crypto
from app.field.models import now
from app.field import service as field
from .models import (
    Case,
    CaseService,
    CaseEvent,
    Reason,
    Policy,
    Competitor,
    Observation,
    Offer,
    Cohort,
    ImportRun,
)
from .contracts import REASONS, STATES, RETENTION


def digest(v):
    return hashlib.sha256(
        json.dumps(v, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def lock(db):
    db.execute(text("SET LOCAL statement_timeout='5000ms'"))
    db.execute(text("SET LOCAL lock_timeout='3000ms'"))
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(1310001)")):
        raise ValueError("Retention operation in progress; refresh safely")


def get(db, id, revision=None):
    c = db.scalar(select(Case).where(Case.id == id).with_for_update())
    if not c:
        raise LookupError()
    if c.redacted_at:
        raise ValueError("Request personal data has been redacted")
    if revision is not None and c.revision != revision:
        raise ValueError("Request changed; refresh before writing")
    return c


def audit(db, u, action, id="private", details=None):
    db.add(
        AuditLog(
            user_id=u.id if u else None,
            action="retention." + action,
            entity_type="retention",
            entity_id=str(id),
            details=details or {},
        )
    )


def event(db, c, u, action, notes="", **facts):
    c.revision += 1
    c.updated_at = now()
    db.add(
        CaseEvent(
            case_id=c.id,
            actor_id=u.id if u else None,
            action=action,
            status=c.status,
            evidence={**facts, "retention_state": c.retention_state},
            notes_ciphertext=crypto.seal(c, {"notes": notes}) if notes else None,
        )
    )
    audit(db, u, action, c.id)
    db.flush()


def configure(db, u, b):
    field.admin(u)
    lock(db)
    version = (db.scalar(select(func.max(Policy.version))) or 0) + 1
    db.query(Policy).filter(Policy.active.is_(True)).update(
        {"active": False}, synchronize_session="fetch"
    )
    p = Policy(version=version, created_by=u.id, **b.model_dump())
    db.add(p)
    db.flush()
    for code, label in REASONS.items():
        if not db.get(Reason, code):
            db.add(
                Reason(
                    code=code,
                    label=label,
                    voluntary="involuntary"
                    if code == "nonpayment"
                    else "unknown"
                    if code in ("unknown", "other")
                    else "voluntary",
                )
            )
    for name in [
        "Starlink",
        "Verizon",
        "T-Mobile",
        "AT&T",
        "Brightspeed",
        "Frontier",
        "Spectrum",
        "MetaLINK",
        "Wabash",
        "NKTelco",
        "Watch Communications",
        "Other local providers",
    ]:
        if not db.scalar(
            select(Competitor.id).where(func.lower(Competitor.name) == name.lower())
        ):
            db.add(Competitor(name=name, technology="unknown", service_areas=[]))
    audit(db, u, "policy_version_created", p.id)
    db.flush()
    return {"id": p.id, "version": p.version, "text": p.text}


def case_view(db, c, details=False):
    v = {
        k: getattr(c, k)
        for k in (
            "id",
            "reference",
            "customer_id",
            "requested_date",
            "reported_reason",
            "operator_reason",
            "status",
            "retention_state",
            "competitor_id",
            "billing_disposition",
            "revision",
            "source",
            "created_at",
            "updated_at",
            "redacted_at",
            "legal_hold",
            "policy_id",
            "policy_ack_at",
        )
    }
    links = list(
        db.scalars(
            select(CaseService)
            .where(CaseService.case_id == c.id)
            .order_by(CaseService.id)
        )
    )
    v["services"] = [
        {
            k: getattr(r, k)
            for k in (
                "id",
                "service_id",
                "snapshot",
                "actual_disconnected_at",
                "source_history_id",
                "equipment_return_required",
                "equipment_due",
                "equipment_returned_at",
            )
        }
        for r in links
    ]
    if details:
        v["customer_submission"] = crypto.unseal(c, c.pii_ciphertext)
        v["events"] = [
            {
                "id": r.id,
                "action": r.action,
                "status": r.status,
                "created_at": r.created_at,
                "actor_id": r.actor_id,
                "evidence": r.evidence,
                "notes": crypto.unseal(c, r.notes_ciphertext),
            }
            for r in db.scalars(
                select(CaseEvent)
                .where(CaseEvent.case_id == c.id)
                .order_by(CaseEvent.id.desc())
                .limit(100)
            )
        ]
        v["offers"] = [
            offer_view(db, c, r)
            for r in db.scalars(
                select(Offer)
                .where(Offer.case_id == c.id)
                .order_by(Offer.id.desc())
                .limit(100)
            )
        ]
        p = db.get(Policy, c.policy_id)
        v["acknowledged_policy"] = {
            "version": p.version,
            "text": p.text,
            "acknowledged_at": c.policy_ack_at,
            "debt_established": False,
        }
    return v


def verify(db, u, id, b):
    field.admin(u)
    lock(db)
    c = get(db, id, b.expected_revision)
    if c.status != "pending_verification" or not (
        b.identity_verified and b.address_verified
    ):
        raise ValueError(
            "Unverified request and documented identity/address review required"
        )
    # Serialize with authoritative import/mapping, never alter the customer records.
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(950001)")):
        raise ValueError("Customer synchronization in progress")
    account = db.get(CustomerAccount, b.customer_id)
    if not account or account.source_deleted_at:
        raise ValueError("Current canonical customer required")
    rows = list(
        db.scalars(
            select(CustomerService)
            .where(CustomerService.id.in_(b.service_ids))
            .with_for_update()
        )
    )
    if len(rows) != len(b.service_ids) or any(
        s.customer_id != account.id or s.source_deleted_at for s in rows
    ):
        raise ValueError("Explicit services must belong to the verified account")
    for s in rows:
        previous = db.scalar(
            select(CaseService.id)
            .join(Case)
            .where(
                CaseService.service_id == s.id,
                Case.status.not_in(["withdrawn", "closed", "disconnected"]),
            )
        )
        if previous:
            raise ValueError("An unresolved request already covers this service")
    data = crypto.unseal(c, c.pii_ciphertext)
    for s in rows:
        snap = {
            k: getattr(s, k)
            for k in (
                "external_id",
                "plan",
                "package_id",
                "site_id",
                "sector_id",
                "service_type",
                "mapping_state",
                "monthly_recurring",
                "currency",
                "revenue_basis",
                "last_synced_at",
            )
        }
        snap = json.loads(json.dumps(snap, default=str))
        if s.monthly_recurring is not None:
            snap["monthly_recurring"] = str(
                s.monthly_recurring.quantize(Decimal(".01"))
            )
        snap.update(
            town=data.get("town") or None,
            county=data.get("county") or None,
            reported_town=True,
        )
        db.add(CaseService(case_id=c.id, service_id=s.id, snapshot=snap))
    replacement = data.get("replacement") or {}
    competitor = db.scalar(
        select(Competitor).where(
            func.lower(Competitor.name)
            == str(replacement.get("provider") or "").lower()
        )
    )
    if competitor:
        c.competitor_id = competitor.id
        db.add(
            Observation(
                competitor_id=competitor.id,
                case_id=c.id,
                offer=replacement,
                source_kind="customer_reported",
                source_reference=c.reference,
                observed_on=c.created_at.date(),
                verified=False,
            )
        )
    c.customer_id = account.id
    c.verified_by = u.id
    c.status = "verified"
    event(
        db,
        c,
        u,
        "identity_verified",
        b.notes,
        service_ids=b.service_ids,
        evidence_reference=b.evidence_reference,
        external_changes=0,
    )
    return case_view(db, c)


def change(db, u, id, b):
    field.admin(u)
    lock(db)
    c = get(db, id, b.expected_revision)
    if b.status not in STATES.get(c.status, set()):
        raise ValueError("Invalid cancellation transition")
    if b.operator_reason and not db.get(Reason, b.operator_reason):
        raise ValueError("Known operator classification required")
    if b.status == "disconnected":
        links = list(db.scalars(select(CaseService).where(CaseService.case_id == c.id)))
        if not links:
            raise ValueError("Verified service associations required")
        for link in links:
            source = db.scalar(
                select(PowercodeServiceHistory)
                .where(PowercodeServiceHistory.service_id == link.service_id)
                .order_by(
                    PowercodeServiceHistory.source_updated_at.desc(),
                    PowercodeServiceHistory.id.desc(),
                )
                .limit(1)
            )
            service = db.get(CustomerService, link.service_id)
            if (
                not source
                or source.status != "disconnected"
                or source.previous_status in (None, "disconnected")
                or service.status != "disconnected"
            ):
                raise ValueError(
                    "Observed source disconnection required; this action cannot disconnect service"
                )
            link.actual_disconnected_at = source.source_updated_at
            link.source_history_id = source.id
    if b.billing_disposition not in (None, "unreviewed") and not c.customer_id:
        raise ValueError("Verify identity before recording billing review")
    c.status = b.status
    c.operator_reason = b.operator_reason or c.operator_reason
    if b.billing_disposition is not None:
        c.billing_disposition = b.billing_disposition
    event(db, c, u, "status_changed", b.notes, external_changes=0)
    return case_view(db, c)


def retention(db, u, id, b):
    field.admin(u)
    lock(db)
    c = get(db, id, b.expected_revision)
    if not c.customer_id or c.status in (
        "pending_verification",
        "disconnected",
        "closed",
    ):
        raise ValueError("Verified unresolved request required")
    if b.state not in RETENTION:
        raise ValueError("Known retention state required")
    if b.state in ("customer_accepted", "customer_declined"):
        raise ValueError("Record a decision against an approved offer")
    if b.state == "customer_retained":
        offers = list(
            db.scalars(
                select(Offer).where(Offer.case_id == c.id, Offer.status == "accepted")
            )
        )
        links = list(db.scalars(select(CaseService).where(CaseService.case_id == c.id)))
        if (
            not offers
            or not links
            or any(
                db.get(CustomerService, r.service_id).status != "active" for r in links
            )
        ):
            raise ValueError(
                "Accepted offer and observed active selected services required"
            )
        c.status = "withdrawn"
    elif b.state == "cancellation_confirmed":
        if c.status not in ("verified", "retention_review", "cancellation_confirmed"):
            raise ValueError("Cancellation review required")
        c.status = "cancellation_confirmed"
    else:
        c.status = "retention_review"
    c.retention_state = b.state
    event(
        db,
        c,
        u,
        "retention_review",
        b.notes,
        evidence_reference=b.evidence_reference,
        service_changes=0,
    )
    return case_view(db, c)


def returns(db, u, id, b):
    field.admin(u)
    lock(db)
    c = get(db, id, b.expected_revision)
    link = db.scalar(
        select(CaseService).where(
            CaseService.case_id == id, CaseService.service_id == b.service_id
        )
    )
    if not link or (b.required and not b.due):
        raise ValueError("Selected service and explicit return due date required")
    link.equipment_return_required = b.required
    link.equipment_due = b.due
    link.equipment_returned_at = now() if b.returned else None
    event(db, c, u, "equipment_return_documented", b.notes, service_id=b.service_id)
    return case_view(db, c)


def offer_view(db, c, o):
    return {
        **{
            k: getattr(o, k)
            for k in (
                "id",
                "kind",
                "status",
                "expires_at",
                "monthly_cost",
                "one_time_cost",
                "currency",
                "fingerprint",
                "approved_by",
            )
        },
        "terms": crypto.unseal(c, o.terms_ciphertext),
        "decision_evidence": crypto.unseal(c, o.acceptance_ciphertext),
    }


def offer(db, u, id, b):
    field.admin(u)
    lock(db)
    c = get(db, id, b.expected_revision)
    if (
        not c.customer_id
        or c.status not in ("verified", "retention_review")
        or not b.eligible
    ):
        raise ValueError("Verified eligible retention review required")
    terms = {
        "text": b.terms,
        "case_revision": c.revision,
        "source_customer_id": c.customer_id,
        "evidence_reference": b.evidence_reference,
        "expires_at": b.expires_at.isoformat(),
        "kind": b.kind,
        "monthly_cost": str(b.monthly_cost) if b.monthly_cost is not None else None,
        "one_time_cost": str(b.one_time_cost) if b.one_time_cost is not None else None,
        "currency": b.currency,
    }
    terms["service_evidence"] = offer_context(db, c)
    o = Offer(
        case_id=c.id,
        kind=b.kind,
        terms_ciphertext=crypto.seal(c, terms),
        monthly_cost=b.monthly_cost,
        one_time_cost=b.one_time_cost,
        currency=b.currency,
        expires_at=b.expires_at,
        eligibility={"reviewed": True, "reference": b.evidence_reference},
        fingerprint=digest(terms),
    )
    db.add(o)
    event(db, c, u, "offer_drafted", b.notes)
    db.flush()
    return offer_view(db, c, o)


def offer_decide(db, u, id, offer_id, b):
    field.admin(u)
    lock(db)
    c = get(db, id, b.expected_revision)
    o = db.get(Offer, offer_id)
    if (
        not o
        or o.case_id != c.id
        or o.fingerprint != b.fingerprint
        or o.expires_at <= now()
        or c.status not in ("verified", "retention_review")
    ):
        raise ValueError("Current unexpired offer and verified request required")
    if crypto.unseal(c, o.terms_ciphertext).get("service_evidence") != offer_context(
        db, c
    ):
        raise ValueError(
            "Offer service, package or pricing evidence changed; create a new offer"
        )
    if b.action == "approve":
        if db.scalar(
            select(Offer.id)
            .where(
                Offer.case_id == c.id,
                Offer.id != o.id,
                Offer.status.in_(["approved", "accepted"]),
            )
            .limit(1)
        ):
            raise ValueError(
                "Resolve the existing approved offer before another approval; accepted evidence cannot be replaced"
            )
        if o.status != "draft":
            raise ValueError("Draft approval required")
        o.status = "approved"
        o.approved_by = u.id
    else:
        if o.status != "approved" or not o.approved_by:
            raise ValueError(
                "Independent offer approval required before customer decision"
            )
        o.status = b.action
        o.acceptance_ciphertext = crypto.seal(
            c,
            {
                "decision": b.action,
                "evidence_reference": b.evidence_reference,
                "recorded_by": u.id,
                "recorded_at": now().isoformat(),
                "fingerprint": o.fingerprint,
            },
        )
        c.retention_state = (
            "customer_accepted" if b.action == "accepted" else "customer_declined"
        )
    event(db, c, u, "offer_" + b.action, b.notes, offer_id=o.id, automatic_changes=0)
    return offer_view(db, c, o)


def cohort(db, u, b):
    field.admin(u)
    lock(db)
    rows = list(
        db.scalars(select(CustomerService).where(CustomerService.id.in_(b.service_ids)))
    )
    if len(rows) != len(b.service_ids):
        raise ValueError(
            "Canonical service IDs required; no fabricated historical membership"
        )
    if db.scalar(select(Cohort.id).where(Cohort.as_of == b.as_of)):
        raise ValueError(
            "Historical opening snapshot is immutable; review corrections independently"
        )
    members = [
        {
            "service_id": r.id,
            "customer_id": r.customer_id,
            "site_id": r.site_id,
            "sector_id": r.sector_id,
            "package": r.package_id or r.plan,
            "source": "operator_attested_historical_membership",
        }
        for r in rows
    ]
    for m in members:
        if m["service_id"] in b.evidence:
            m.update(b.evidence[m["service_id"]].model_dump(mode="json"))
    r = Cohort(
        as_of=b.as_of,
        complete=b.complete,
        members=members,
        source_reference=b.evidence_reference,
        approved_by=u.id,
    )
    db.add(r)
    audit(db, u, "cohort_recorded")
    db.flush()
    return {
        "id": r.id,
        "as_of": r.as_of,
        "complete": r.complete,
        "services": len(members),
    }


def redact(db, u, id, b):
    field.admin(u)
    lock(db)
    c = get(db, id, b.expected_revision)
    p = db.get(Policy, c.policy_id)
    if (
        c.legal_hold
        or c.status not in ("closed", "withdrawn")
        or c.created_at > now() - timedelta(days=p.retention_days)
    ):
        raise ValueError("Closed expired retention period and no legal hold required")
    event(db, c, u, "personal_data_redacted", b.notes)
    c.pii_ciphertext = None
    c.wrapped_key = None
    c.redacted_at = now()
    db.flush()
    return {"id": c.id, "redacted": True, "historical_references_retained": True}


def risk(db, c):
    links = list(db.scalars(select(CaseService).where(CaseService.case_id == c.id)))
    ids = [r.service_id for r in links]
    evidence = []
    if c.reported_reason in {
        "reliability",
        "outages",
        "speed",
        "customer_service",
        "price",
    }:
        evidence.append(
            {
                "kind": "customer_reported_dissatisfaction",
                "reason": c.reported_reason,
                "observed_at": c.created_at.isoformat(),
                "uncertainty": "Self-reported; not inferred sentiment",
            }
        )
    incidents = (
        list(
            db.execute(
                select(Incident, IncidentCustomer)
                .join(IncidentCustomer, IncidentCustomer.incident_id == Incident.id)
                .where(
                    IncidentCustomer.service_id.in_(ids),
                    IncidentCustomer.evaluated_at >= now() - timedelta(days=90),
                )
                .order_by(Incident.started_at.desc())
                .limit(101)
            )
        )
        if ids
        else []
    )
    seen = {}
    for i, impact in incidents[:100]:
        if i.id not in seen:
            seen[i.id] = {
                "kind": "documented_incident_impact",
                "incident_id": i.id,
                "classification": impact.classification,
                "observed_at": impact.evaluated_at.isoformat(),
                "status": i.status,
                "uncertainty": "Documented impact mapping, not proof of subscriber downtime",
            }
    evidence.extend(seen.values())
    from app.models.entities import MonitoringAlert

    devices = (
        list(
            db.scalars(
                select(CustomerService.device_id).where(
                    CustomerService.id.in_(ids), CustomerService.device_id.is_not(None)
                )
            )
        )
        if ids
        else []
    )
    alerts = (
        list(
            db.scalars(
                select(MonitoringAlert)
                .where(
                    MonitoringAlert.device_id.in_(devices),
                    MonitoringAlert.updated_at >= now() - timedelta(days=30),
                )
                .order_by(MonitoringAlert.updated_at.desc())
                .limit(100)
            )
        )
        if devices
        else []
    )
    for alert in alerts:
        evidence.append(
            {
                "kind": "documented_monitoring_alert",
                "alert_id": alert.id,
                "rule": alert.rule,
                "severity": alert.severity,
                "state": alert.state,
                "observed_at": alert.updated_at.isoformat(),
                "uncertainty": "Existing collector alert; shared access infrastructure is not proof of subscriber-specific experience",
            }
        )
    if c.competitor_id:
        observations = list(
            db.scalars(
                select(Observation)
                .where(
                    Observation.competitor_id == c.competitor_id,
                    Observation.verified.is_(True),
                    Observation.observed_on >= date.today() - timedelta(days=30),
                )
                .order_by(Observation.observed_on.desc(), Observation.id.desc())
                .limit(10)
            )
        )
        for observation in observations:
            evidence.append(
                {
                    "kind": "independently_reviewed_competitor_offer",
                    "observation_id": observation.id,
                    "observed_on": observation.observed_on.isoformat(),
                    "uncertainty": "Offer observation is not proof of availability at this customer location or an equivalent package",
                }
            )

    service_fresh = (
        all(
            db.get(CustomerService, r.service_id).last_synced_at
            >= now() - timedelta(days=1)
            for r in links
        )
        if links
        else False
    )
    return {
        "case_id": c.id,
        "assessment": "review_recommended" if evidence else "insufficient_evidence",
        "evidence": evidence,
        "service_mapping_fresh": service_fresh,
        "truncated": len(incidents) > 100,
        "confidence": "not_statistically_validated",
        "recommendation": "Human review of documented service issues and customer-reported concerns",
        "uncertainty": [
            "No validated churn probability",
            "No invented competitor availability, tenure, sentiment or RF measurements",
            "Incident mapping may be estimated or stale",
        ],
        "automatic_actions": False,
    }


def historical(db, u, b):
    field.admin(u)
    lock(db)
    allowed = {
        "external_id",
        "customer_external_id",
        "service_external_id",
        "requested_date",
        "disconnected_date",
        "reason",
        "competitor",
        "town",
        "county",
        "comments",
        "monthly",
        "currency",
    }
    if not set(b.columns) <= allowed or "external_id" not in b.columns:
        raise ValueError(
            "Explicit supported column mapping including stable import identifier required"
        )
    try:
        reader = csv.DictReader(io.StringIO(b.csv_text, newline=""))
        if (
            not reader.fieldnames
            or len(reader.fieldnames) != len(set(reader.fieldnames))
            or any(v not in reader.fieldnames for v in b.columns.values())
        ):
            raise ValueError()
        rows = []
        for n, r in enumerate(reader, 2):
            if len(rows) >= 1000 or None in r:
                raise ValueError()
            x = {k: r[v].strip() for k, v in b.columns.items()}
            if (
                not x["external_id"]
                or len(x["external_id"]) > 120
                or any(len(v) > 2000 for v in x.values())
            ):
                raise ValueError()
            d = date.fromisoformat(
                x.get("requested_date") or x.get("disconnected_date") or ""
            )
            end = (
                date.fromisoformat(x["disconnected_date"])
                if x.get("disconnected_date")
                else None
            )
            if d > date.today() or end and (end < d or end > date.today()):
                raise ValueError()
            reason = x.get("reason", "unknown").lower().replace(" ", "_")
            if x.get("monthly"):
                money = Decimal(x["monthly"])
                if (
                    not money.is_finite()
                    or money < 0
                    or money > 1000000
                    or money.as_tuple().exponent < -2
                    or len(x.get("currency", "")) != 3
                    or not x["currency"].isupper()
                ):
                    raise ValueError()
            x["normalized_reason"] = reason if db.get(Reason, reason) else "unknown"
            x["requested"] = d.isoformat()
            x["reported_disconnected"] = end.isoformat() if end else None
            comp = db.scalar(
                select(Competitor).where(
                    func.lower(Competitor.name) == x.get("competitor", "").lower()
                )
            )
            x["competitor_id"] = comp.id if comp else None
            key = crypto.digest("retention_import", x["external_id"])
            x["import_key"] = key
            if any(v["import_key"] == key for v in rows):
                raise ValueError()
            customer = db.scalar(
                select(CustomerAccount).where(
                    CustomerAccount.external_id == x.get("customer_external_id", "")
                )
            )
            service = db.scalar(
                select(CustomerService).where(
                    CustomerService.external_id == x.get("service_external_id", "")
                )
            )
            if service and (not customer or service.customer_id != customer.id):
                raise ValueError()
            x["customer_id"] = customer.id if customer else None
            x["service_id"] = service.id if service else None
            rows.append(x)
    except (ValueError, TypeError, KeyError, csv.Error, ArithmeticError):
        raise ValueError(
            "CSV validation failed; check mapped columns, unique IDs, dates and associations. No rows imported"
        ) from None
    signature = digest({"columns": b.columns, "rows": rows})
    old = db.scalar(select(ImportRun).where(ImportRun.digest == signature))
    preview = {
        "digest": signature,
        "rows": len(rows),
        "matched_services": sum(r["service_id"] is not None for r in rows),
        "unmatched": sum(r["service_id"] is None for r in rows),
        "already_imported": bool(old),
        "dry_run": b.dry_run,
    }
    if b.dry_run or old:
        return preview
    if b.expected_digest != signature:
        raise ValueError("Exact reviewed CSV preview digest required")
    policy = db.scalar(
        select(Policy)
        .where(Policy.active.is_(True))
        .order_by(Policy.version.desc())
        .limit(1)
    )
    if not policy:
        raise ValueError("Configure reviewed policy before historical import")
    for x in rows:
        existing = db.scalar(select(Case).where(Case.import_key == x["import_key"]))
        if existing:
            if existing.digest != digest(x):
                raise ValueError("Historical import identity conflict; no overwrite")
            continue
        c = Case(
            reference="HIST-" + str(uuid4()),
            submission_key=str(uuid4()),
            digest=digest(x),
            receipt_hash=secrets.token_hex(32),
            requested_date=date.fromisoformat(x["requested"]),
            reported_reason=x["normalized_reason"],
            policy_id=policy.id,
            policy_ack_at=None,
            equipment_ack=False,
            status="pending_verification",
            source="historical_report",
            import_key=x["import_key"],
            competitor_id=x["competitor_id"],
        )
        # Imported reports are not customer policy acknowledgement or verified disconnection.
        crypto.initialize(
            c,
            {
                "historical": x,
                "policy_acknowledged": False,
                "reported_disconnected_date": x["reported_disconnected"],
                "town": x.get("town"),
                "county": x.get("county"),
            },
        )
        db.add(c)
        db.flush()
        event(
            db,
            c,
            u,
            "historical_report_imported",
            source_association="unverified_report",
            matched_service_id=x["service_id"],
        )
    db.add(ImportRun(digest=signature, rows=len(rows), actor_id=u.id))
    audit(db, u, "csv_imported", details={"rows": len(rows)})
    db.flush()
    return preview


def offer_context(db, c):
    ids = list(
        db.scalars(select(CaseService.service_id).where(CaseService.case_id == c.id))
    )
    rows = list(
        db.scalars(
            select(CustomerService)
            .where(CustomerService.id.in_(ids))
            .order_by(CustomerService.id)
        )
    )
    if not rows or any(
        r.customer_id != c.customer_id or r.source_deleted_at for r in rows
    ):
        raise ValueError("Current selected services required")
    return [
        {
            "id": r.id,
            "status": r.status,
            "package": r.package_id,
            "plan": r.plan,
            "monthly": str(r.monthly_recurring.quantize(Decimal(".01")))
            if r.monthly_recurring is not None
            else None,
            "currency": r.currency,
            "basis": r.revenue_basis,
        }
        for r in rows
    ]


def review(db, u, id, b):
    field.admin(u)
    lock(db)
    c = get(db, id, b.expected_revision)
    if b.competitor_id is not None and not db.get(Competitor, b.competitor_id):
        raise ValueError("Known competitor required")
    c.competitor_id = b.competitor_id
    c.legal_hold = b.legal_hold
    event(
        db,
        c,
        u,
        "operator_review",
        b.notes,
        competitor_id=c.competitor_id,
        legal_hold=c.legal_hold,
    )
    return case_view(db, c)
