"""Cancellation drafts on the existing consent/approval/budget/outbox system."""

from sqlalchemy import select
from app.customers.models import CustomerAccount, CustomerService
from app.customers.contracts import e164
from app.notifications import service as ns
from app.notifications.models import (
    NotificationPlan,
    NotificationRecipient,
    NotificationContactSuppression,
    NotificationSuppression,
)
from app.notifications.sms_models import SMSWebhookEvent
from app.notifications.sms_identity import contact_hash as provider_hash
from .models import Case, CaseService
from . import service as s

TEMPLATES = {
    "request_confirmation": "Your cancellation request has been received for identity verification and operator review. No automatic service or billing change has been made.",
    "retention_contact": "Our team would like to discuss your service concerns. Please contact support using your usual support channel.",
    "equipment_return": "Please contact support for your operator-reviewed equipment return instructions.",
    "processing_update": "An operator has updated your cancellation request. Please contact support for details.",
    "completion": "Your operator-reviewed cancellation request has been processed. Powercode remains authoritative for service and billing details.",
}


def snapshot(db, plan):
    marker = plan.snapshot.get("retention", {})
    if (
        set(marker) != {"case_id", "revision", "template"}
        or plan.incident_id
        or plan.maintenance_id
        or plan.kind != "customer_cancellation"
    ):
        raise ValueError("Explicit cancellation communication contract required")
    c = db.get(Case, marker["case_id"])
    if (
        not c
        or c.redacted_at
        or not c.customer_id
        or c.revision != marker["revision"]
        or c.status == "pending_verification"
        or marker["template"] not in TEMPLATES
    ):
        raise ValueError("Current verified cancellation source required")
    if marker["template"] == "completion" and c.status not in (
        "disconnected",
        "closed",
    ):
        raise ValueError("Observed disconnection or completed review required")
    account = db.get(CustomerAccount, c.customer_id)
    phone = e164(account.phone) if account else None
    if (
        not account
        or account.source_deleted_at
        or not phone
        or account.sms_consent != "granted"
        or account.sms_opt_out
        or account.communication_preference not in ("sms", "both")
        or not account.consent_record
    ):
        raise ValueError(
            "Canonical current SMS consent required; cancellation processing remains available without SMS"
        )
    h = ns.contact_hash(phone)
    if db.get(NotificationContactSuppression, h) or db.get(
        NotificationSuppression, account.id
    ):
        raise ValueError("Shared contact hold or opt-out")
    if db.scalar(
        select(CustomerAccount.id)
        .where(CustomerAccount.phone == phone, CustomerAccount.sms_opt_out.is_(True))
        .limit(1)
    ):
        raise ValueError("Shared customer opt-out")
    if db.scalar(
        select(SMSWebhookEvent.key)
        .where(
            SMSWebhookEvent.contact_hash == provider_hash(phone),
            SMSWebhookEvent.kind == "stop",
        )
        .limit(1)
    ):
        raise ValueError("Verified provider opt-out")
    links = list(
        db.scalars(
            select(CaseService)
            .where(CaseService.case_id == c.id)
            .order_by(CaseService.service_id)
        )
    )
    if not links or any(
        db.get(CustomerService, r.service_id).customer_id != c.customer_id
        for r in links
    ):
        raise ValueError("Selected canonical services required")
    message = (
        "Northwest Ohio Broadband: "
        + TEMPLATES[marker["template"]]
        + " Reply STOP to opt out."
    )
    if plan.message is not None and plan.message != message:
        raise ValueError("Approved message changed")
    return {
        "retention": marker,
        "status": c.status,
        "retention_state": c.retention_state,
        "message": message,
        "customer_id": c.customer_id,
        "service_ids": [r.service_id for r in links],
        "recipient_hash": h,
        "policy": ns.policy(db).model_dump(mode="json"),
    }


def prepare(db, u, id, b):
    s.field.admin(u)
    s.lock(db)
    ns.lock(db)
    c = s.get(db, id, b.expected_revision)
    # An audit event changes the source revision before fingerprinting, invalidating older drafts.
    s.event(
        db,
        c,
        u,
        "sms_draft_requested",
        b.notes,
        template=b.template,
        delivery_authorized=False,
    )
    marker = {"case_id": c.id, "revision": c.revision, "template": b.template}
    current = snapshot(
        db,
        NotificationPlan(snapshot={"retention": marker}, kind="customer_cancellation"),
    )
    key = ns.digest(["customer_cancellation", marker])
    old = db.scalar(select(NotificationPlan).where(NotificationPlan.dedupe_key == key))
    if old:
        return {
            "id": old.id,
            "fingerprint": old.fingerprint,
            "status": old.status,
            "external_delivery": False,
        }
    p = ns.policy(db)
    from app.notifications.sms_transport import segments

    plan = NotificationPlan(
        dedupe_key=key,
        kind="customer_cancellation",
        severity="info",
        confidence="operator_review",
        status="draft",
        target={"case_id": c.id},
        evidence={"source": "verified_cancellation"},
        snapshot={"retention": marker, "request": {"critical_quiet_bypass": False}},
        fingerprint=ns.digest(current),
        created_by=u.id,
        due_at=ns.clock(),
        message=current["message"],
        slack_payload={},
        estimated_cost_microunits=segments(current["message"]) * p.sms_price_microunits
        if p.sms_price_microunits is not None
        else None,
    )
    db.add(plan)
    db.flush()
    db.add(
        NotificationRecipient(
            plan_id=plan.id,
            customer_id=c.customer_id,
            service_ids=current["service_ids"],
            contact_hash=current["recipient_hash"],
            state="eligible",
            reason="verified_customer_cancellation",
        )
    )
    return {
        "id": plan.id,
        "fingerprint": plan.fingerprint,
        "status": plan.status,
        "external_delivery": False,
    }
