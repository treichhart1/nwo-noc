"""Private administrator provider controls. Never mounted on callback gateway."""

from datetime import datetime, timedelta
from typing import Literal
import json
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func
from app.api.deps import require_admin
from app.core.database import get_db
from app.core.config import settings
from app.notifications.routes import tx
from app.notifications.models import (
    NotificationAttempt as Attempt,
    NotificationDelivery as Delivery,
    NotificationCredential as Credential,
)
from app.notifications.sms_models import (
    SMSProviderConfig as Provider,
    SMSRouting as Routing,
    SMSWebhookEvent as Event,
)
from app.notifications.sms_contracts import (
    ProviderInput,
    RoutingInput,
    CredentialsInput,
    AuthorizationInput,
    ReconcileInput,
    ProviderName,
)
from app.notifications.service import audit, clock, utc
from app.notifications.sms_identity import contact_hash
from app.notifications.secrets import unseal
from app.notifications import sms_service as service
from app.notifications.sms_transport import TERMINAL, PROVIDER_CAPABILITIES, segments

router = APIRouter(
    prefix="/api/notifications/sms",
    tags=["SMS provider administration"],
    dependencies=[Depends(require_admin)],
)


@router.get("")
def status(db=Depends(get_db), user=Depends(require_admin)):
    def load():
        rows = list(db.scalars(select(Provider).order_by(Provider.name)))
        now = clock()
        return {
            "provider_capabilities": PROVIDER_CAPABILITIES,
            "host_live_enabled": settings.sms_native_transport_enabled,
            "host_worker_enabled": settings.sms_native_worker_enabled,
            "gateway_enabled_in_this_process": settings.sms_callback_gateway_enabled,
            "routing": service.route(db),
            "routing_revision": (
                db.get(Routing, 1).revision if db.get(Routing, 1) else 0
            ),
            "providers": [
                {
                    "name": r.name,
                    "enabled": r.enabled,
                    "live_authorized": r.live_authorized,
                    "revision": r.revision,
                    "health": r.health,
                    "last_submission_at": r.last_submission_at,
                    "last_callback_at": r.last_callback_at,
                    "credentials_configured": db.get(
                        Credential, service.credential_key(r.name)
                    )
                    is not None,
                    "callback_key_configured": bool(r.webhook_ciphertext),
                    "approved_sender_count": len(r.config.get("approved_senders", [])),
                    "test_recipient_count": len(r.config.get("recipient_hashes", [])),
                    "daily_limit_microunits": r.config["daily_limit_microunits"],
                    "monthly_limit_microunits": r.config["monthly_limit_microunits"],
                    "price_per_segment_microunits": r.config[
                        "price_per_segment_microunits"
                    ],
                    "currency": r.config["currency"],
                }
                for r in rows
            ],
            "daily_usage": service.summary(
                db, now.replace(hour=0, minute=0, second=0, microsecond=0), now
            ),
            "monthly_usage": service.summary(
                db, now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now
            ),
            "limitations": [
                "Live provider/sender registration and costs require separate acceptance",
                "START never automatically restores consent",
                "Signup and installation communication remain existing consent-aware drafts",
            ],
        }

    return tx(db, load)


@router.put("/providers/{name}")
def configure(
    name: ProviderName,
    body: ProviderInput,
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def save():
        body.provider_valid(name)
        row = db.get(Provider, name)
        config = body.model_dump(exclude={"enabled", "recipient_allowlist"})
        config["recipient_hashes"] = sorted(
            {contact_hash(p) for p in body.recipient_allowlist}
        )
        if row:
            pending = db.scalar(
                select(Attempt.id)
                .join(Delivery)
                .where(
                    Attempt.provider_name == name,
                    Attempt.acceptance != "not_accepted",
                    Delivery.state.not_in(TERMINAL),
                )
                .limit(1)
            )
            if pending and any(
                row.config.get(k) != config.get(k)
                for k in {"account_sid", "service_id", "sender", "approved_senders"}
            ):
                raise ValueError(
                    "Reconcile outstanding attempts before changing provider ownership"
                )
            row.config, row.enabled, row.live_authorized = config, body.enabled, False
            row.revision += 1
        else:
            row = Provider(
                name=name, config=config, enabled=body.enabled, live_authorized=False
            )
            db.add(row)
        audit(
            db, user.id, "sms_provider_configured", provider=name, live_authorized=False
        )
        return {
            "provider": name,
            "live_authorized": False,
            "credentials_returned": False,
        }

    return tx(db, save)


@router.put("/providers/{name}/credentials")
def set_credentials(
    name: ProviderName,
    body: CredentialsInput,
    db=Depends(get_db),
    user=Depends(require_admin),
):
    return tx(
        db,
        lambda: (
            service.store_credentials(db, name, body.value.get_secret_value(), user.id),
            {"stored": True, "credentials_returned": False, "live_authorized": False},
        )[1],
    )


@router.put("/routing")
def routing(body: RoutingInput, db=Depends(get_db), user=Depends(require_admin)):
    def save():
        row = db.get(Routing, 1)
        if row:
            row.config = body.model_dump()
            row.revision += 1
        else:
            db.add(Routing(id=1, config=body.model_dump()))
        audit(
            db,
            user.id,
            "sms_routing_changed",
            primary=body.primary,
            secondary=body.secondary,
            failover=body.failover,
        )
        return {"saved": True, "existing_releases_invalidated": True}

    return tx(db, save)


@router.post("/providers/{name}/authorize")
def activate(
    name: ProviderName,
    body: AuthorizationInput,
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def save():
        if not body.confirm:
            raise ValueError("Explicit provider activation confirmation required")
        service.host_authorized()
        row = db.get(Provider, name)
        if not row:
            raise LookupError()
        row.live_authorized, row.authorization_reference = True, body.reference
        service.provider_ready(db, name)
        row.revision += 1
        audit(
            db,
            user.id,
            "sms_provider_live_authorized",
            provider=name,
            reference=body.reference,
        )
        return {"provider": name, "live_authorized": True, "messages_sent": 0}

    return tx(db, save)


@router.post("/providers/{name}/disable")
def disable(name: ProviderName, db=Depends(get_db), user=Depends(require_admin)):
    def save():
        row = db.get(Provider, name)
        if not row:
            raise LookupError()
        row.live_authorized, row.enabled = False, False
        row.revision += 1
        audit(db, user.id, "sms_provider_disabled", provider=name)
        return {
            "disabled": True,
            "verified_opt_out_callbacks_retained": row.config.get(
                "callbacks_enabled", False
            ),
        }

    return tx(db, save)


@router.post("/deliveries/{identifier}/authorize")
def authorize(
    identifier: int,
    body: AuthorizationInput,
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def save():
        if not body.confirm or not body.fingerprint:
            raise ValueError(
                "Explicit live release and approved content fingerprint required"
            )
        d = service.authorize_delivery(
            db, identifier, user.id, body.reference, body.fingerprint
        )
        return {"id": d.id, "state": d.state, "released": True, "messages_sent": 0}

    return tx(db, save)


@router.post("/deliveries/{identifier}/failover")
def failover(
    identifier: int,
    body: AuthorizationInput,
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def save():
        if not body.confirm:
            raise ValueError("Explicit manual failover approval required")
        d = service.manual_failover(db, identifier, user.id, body.reference)
        return {
            "id": d.id,
            "state": d.state,
            "provider": d.sms_provider,
            "messages_sent": 0,
        }

    return tx(db, save)


@router.post("/attempts/{identifier}/reconcile")
def reconcile(
    identifier: int,
    body: ReconcileInput,
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def save():
        if not body.confirm:
            raise ValueError("Explicit provider reconciliation required")
        from app.notifications.sms_transport import SMSFailure

        try:
            d = service.reconcile_attempt(
                db, identifier, user.id, body.reference, body.provider_message_id
            )
        except SMSFailure:
            raise ValueError(
                "Provider reconciliation unavailable; retain quarantine"
            ) from None
        return {"delivery_id": d.id, "state": d.state, "messages_sent": 0}

    # Reconciliation performs GET-only provider I/O before taking the short
    # notification update lock; verified STOP callbacks must not wait on HTTP.
    try:
        value = save()
        db.commit()
        return value
    except LookupError:
        db.rollback()
        raise HTTPException(404, "SMS attempt not found") from None
    except (ValueError, OSError):
        db.rollback()
        raise HTTPException(
            409, "Reconciliation unavailable; retain quarantine"
        ) from None


@router.get("/attempts")
def attempts(
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=100),
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def load():
        rows = list(
            db.scalars(
                select(Attempt)
                .where(Attempt.provider_name.is_not(None))
                .order_by(Attempt.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        return {
            "items": [
                {
                    "id": r.id,
                    "delivery_id": r.delivery_id,
                    "provider": r.provider_name,
                    "acceptance": r.acceptance,
                    "state": r.outcome,
                    "raw_status": r.raw_status,
                    "provider_error_code": r.provider_error_code,
                    "provider_message_id": r.provider_message_id,
                    "attempt": r.attempt,
                    "estimated_segments": r.estimated_segments,
                    "estimated_cost_microunits": r.estimated_cost_microunits,
                    "provider_reported_cost_microunits": r.reported_cost_microunits,
                    "currency": r.currency,
                }
                for r in rows
            ],
            "offset": offset,
            "limit": limit,
        }

    return tx(db, load)


@router.get("/inbound")
def inbound(
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=100),
    db=Depends(get_db),
    user=Depends(require_admin),
):
    return tx(
        db,
        lambda: {
            "items": [
                {
                    "key": r.key,
                    "provider": r.provider,
                    "kind": r.kind,
                    "at": r.received_at,
                    "reviewed": r.reviewed_by is not None,
                    "content_available": r.ciphertext is not None,
                }
                for r in db.scalars(
                    select(Event)
                    .where(Event.kind != "status")
                    .order_by(Event.received_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ]
        },
    )


@router.post("/inbound/{key}/review")
def review(key: str, db=Depends(get_db), user=Depends(require_admin)):
    def load():
        row = db.get(Event, key)
        if not row or row.kind == "status":
            raise LookupError()
        content = (
            json.loads(unseal(settings.sms_callback_key_file, row.ciphertext))
            if row.ciphertext
            else {"body": None}
        )
        row.reviewed_by = user.id
        audit(
            db,
            user.id,
            "sms_inbound_reviewed",
            provider=row.provider,
            event_key=row.key,
        )
        return {
            "key": row.key,
            "kind": row.kind,
            "content": content,
            "consent_restored": False,
            "messages_sent": 0,
        }

    return tx(db, load)


@router.post("/redact-expired")
def redact(db=Depends(get_db), user=Depends(require_admin)):
    def run():
        from app.notifications.service import policy

        rows = list(
            db.scalars(
                select(Event)
                .where(
                    Event.received_at
                    < clock() - timedelta(days=policy(db).retention_days),
                    Event.ciphertext.is_not(None),
                )
                .limit(1000)
            )
        )
        for row in rows:
            row.ciphertext = None
        audit(db, user.id, "sms_inbound_retention_redacted", count=len(rows))
        return {"redacted": len(rows), "replay_and_opt_out_tombstones_retained": True}

    return tx(db, run)


@router.post("/signup-drafts/{identifier}/preview")
def signup_preview(identifier: int, db=Depends(get_db), user=Depends(require_admin)):
    return tx(db, lambda: service.signup_preview(db, identifier, user.id))


@router.get("/deliveries/{identifier}/review")
def delivery_review(identifier: int, db=Depends(get_db), user=Depends(require_admin)):
    def load():
        from app.notifications.models import NotificationPlan, NotificationRecipient
        from app.customers.models import CustomerAccount

        row = db.get(Delivery, identifier)
        if not row or row.channel != "sms":
            raise LookupError()
        plan = db.get(NotificationPlan, row.plan_id)
        recipient = db.get(NotificationRecipient, row.recipient_id)
        customer = db.get(CustomerAccount, recipient.customer_id) if recipient else None
        audit(
            db,
            user.id,
            "sms_live_release_reviewed",
            plan_id=plan.id,
            delivery_id=row.id,
        )
        return {
            "delivery_id": row.id,
            "plan_id": plan.id,
            "state": row.state,
            "kind": plan.kind,
            "provider_estimates": [
                {
                    "provider": p.name,
                    "estimated_segments": segments(plan.message),
                    "estimated_cost_microunits": segments(plan.message)
                    * p.config["price_per_segment_microunits"]
                    if p.config.get("price_per_segment_microunits")
                    else None,
                    "currency": p.config["currency"],
                }
                for p in db.scalars(select(Provider).order_by(Provider.name))
            ],
            "approved_message": plan.message,
            "fingerprint": plan.fingerprint,
            "recipient_mask": "***" + customer.phone[-4:]
            if customer and customer.phone
            else "unavailable",
            "consent": customer.sms_consent if customer else "unknown",
            "opt_out": customer.sms_opt_out if customer else True,
            "host_live_enabled": settings.sms_native_transport_enabled,
            "messages_sent": 0,
        }

    return tx(db, load)


@router.get("/usage")
def usage(
    since: datetime, until: datetime, db=Depends(get_db), user=Depends(require_admin)
):
    def load():
        if (
            since.tzinfo is None
            or until.tzinfo is None
            or until <= since
            or until - since > timedelta(days=93)
        ):
            raise ValueError("Aware ordered reporting range up to 93 days required")
        return service.summary(db, utc(since), utc(until))

    return tx(db, load)
