"""Explicit prospect subjects on the canonical notification plan/recipient/outbox."""

from dataclasses import dataclass
from sqlalchemy import select
from app.models.entities import User
from app.customers.models import CustomerAccount
from app.customers.contracts import e164
from app.notifications import service as notifications
from app.notifications.models import (
    NotificationPlan,
    NotificationRecipient,
    NotificationContactSuppression,
)
from app.notifications.sms_models import SMSWebhookEvent
from app.notifications.sms_identity import contact_hash as provider_hash
from .models import Application, NotificationDraft
from . import service as signup


@dataclass(frozen=True)
class ProspectContact:
    application_id: int
    phone: str
    subject_kind: str = "signup_application"


def contact(db, recipient, *, consent=True):
    if (
        recipient is None
        or recipient.application_id is None
        or recipient.customer_id is not None
    ):
        raise ValueError("Explicit prospect subject required")
    a = db.get(Application, recipient.application_id)
    if not a or a.redacted_at or not a.wrapped_key:
        raise ValueError("Prospect unavailable")
    data = signup.pii(a)
    phone = e164(data.get("phone"))
    if not phone or notifications.contact_hash(phone) != recipient.contact_hash:
        raise ValueError("Prospect contact changed")
    if consent:
        if (
            not data.get("consent_sms")
            or data.get("preferred_contact") != "sms"
            or data.get("consent_withdrawn_at")
        ):
            raise ValueError("Current prospect SMS consent required")
        if db.get(NotificationContactSuppression, notifications.contact_hash(phone)):
            raise ValueError("Shared contact opt-out")
        if db.scalar(
            select(CustomerAccount.id)
            .where(
                CustomerAccount.phone == phone, CustomerAccount.sms_opt_out.is_(True)
            )
            .limit(1)
        ):
            raise ValueError("Imported customer opt-out")
        identity = provider_hash(phone)
        if db.scalar(
            select(SMSWebhookEvent.key)
            .where(
                SMSWebhookEvent.kind == "stop", SMSWebhookEvent.contact_hash == identity
            )
            .limit(1)
        ):
            raise ValueError("Verified provider opt-out")
    return ProspectContact(a.id, phone)


def snapshot(db, plan):
    marker = plan.snapshot.get("prospect", {})
    if set(marker) != {"draft_id", "application_id"}:
        raise ValueError("Invalid prospect source contract")
    draft = db.get(NotificationDraft, marker["draft_id"])
    a = db.get(Application, marker["application_id"])
    if (
        not draft
        or not a
        or draft.application_id != a.id
        or draft.channel != "sms"
        or not draft.eligible
    ):
        raise ValueError("Approved-source SMS draft required")
    recipient = NotificationRecipient(
        application_id=a.id,
        customer_id=None,
        contact_hash=notifications.contact_hash(signup.pii(a).get("phone")),
    )
    destination = contact(db, recipient)
    actor = db.get(User, draft.created_by)
    if not actor or not actor.is_active or actor.role not in {"admin", "operator"}:
        raise ValueError("Draft operator unavailable")
    message = (
        "Northwest Ohio Broadband: "
        + draft.policy["text"].removeprefix("Northwest Ohio Broadband: ")
        + " Reply STOP to opt out."
    )
    if plan.message is not None and plan.message != message:
        raise ValueError("Approved communication content changed")
    return {
        "prospect": marker,
        "application_revision": a.revision,
        "state": a.state,
        "draft": draft.id,
        "template": draft.template,
        "message": message,
        "recipient_hash": notifications.contact_hash(destination.phone),
        "policy": notifications.policy(db).model_dump(mode="json"),
    }


def prepare(db, u, draft_id):
    signup.staff(u)
    notifications.lock(db)
    draft = db.get(NotificationDraft, draft_id)
    if not draft or draft.channel != "sms":
        raise ValueError("Prospect SMS draft required")
    marker = {"draft_id": draft.id, "application_id": draft.application_id}
    provisional = NotificationPlan(snapshot={"prospect": marker})
    current = snapshot(db, provisional)
    fingerprint = notifications.digest(current)
    key = notifications.digest(["signup_sms", draft.id])
    old = db.scalar(select(NotificationPlan).where(NotificationPlan.dedupe_key == key))
    if old:
        if old.fingerprint != fingerprint:
            raise ValueError(
                "Source changed; prepare a new application communication draft"
            )
        return {
            "id": old.id,
            "fingerprint": old.fingerprint,
            "status": old.status,
            "external_delivery": False,
        }
    p = notifications.policy(db)
    from app.notifications.sms_transport import segments

    estimate = (
        segments(current["message"]) * p.sms_price_microunits
        if p.sms_price_microunits is not None
        else None
    )
    plan = NotificationPlan(
        dedupe_key=key,
        kind="installation_scheduling"
        if draft.template.startswith(("installation", "appointment"))
        else "signup_application",
        severity="info",
        confidence="operator_review",
        status="draft",
        target={"application_id": draft.application_id},
        evidence={"source": "signup_draft", "draft_id": draft.id},
        snapshot={"prospect": marker, "request": {"critical_quiet_bypass": False}},
        fingerprint=fingerprint,
        created_by=u.id,
        due_at=notifications.clock(),
        message=current["message"],
        slack_payload={},
        estimated_cost_microunits=estimate,
    )
    db.add(plan)
    db.flush()
    db.add(
        NotificationRecipient(
            plan_id=plan.id,
            customer_id=None,
            application_id=draft.application_id,
            service_ids=[],
            contact_hash=current["recipient_hash"],
            state="eligible",
            reason="explicit_prospect_consent",
        )
    )
    signup.event(
        db,
        db.get(Application, draft.application_id),
        u,
        "sms_plan_prepared",
        "Protected prospect communication prepared",
        {"plan_id": plan.id, "delivery_authorized": False},
    )
    plan.fingerprint = notifications.digest(snapshot(db, plan))
    return {
        "id": plan.id,
        "fingerprint": plan.fingerprint,
        "status": "draft",
        "external_delivery": False,
    }
