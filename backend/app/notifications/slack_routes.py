"""Private Slack administrator APIs; never mounted on the public receiver."""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func
from app.api.deps import require_admin
from app.core.database import get_db
from app.models.entities import User
from app.notifications.routes import tx
from app.notifications.slack_models import (
    SlackIntegration as Integration,
    SlackThread as Thread,
    SlackUserMap as UserMap,
    SlackSource as Source,
    SlackInteraction as Interaction,
)
from app.notifications.models import (
    NotificationPlan as Plan,
    NotificationDelivery as Delivery,
    NotificationAttempt as Attempt,
    NotificationCredential as Credential,
)
from app.notifications.slack_contracts import (
    SlackConfig,
    SlackCredentials,
    SlackSourceRequest,
    SlackApproval,
    SlackReconcile,
    SlackMapping,
    EVENTS,
)
from app.notifications.service import audit, clock, lock, utc
from app.notifications import slack_service as service, slack_sources as sources
from app.core.config import settings
from app.notifications.slack_transport import SlackFailure

router = APIRouter(
    prefix="/api/notifications/slack",
    tags=["Native Slack"],
    dependencies=[Depends(require_admin)],
)


@router.get("")
def status(db=Depends(get_db), user=Depends(require_admin)):
    def load():
        r = db.get(Integration, 1)
        return {
            "host_transport_enabled": settings.slack_native_transport_enabled,
            "host_worker_enabled": settings.slack_native_worker_enabled,
            "configuration": r.config if r else None,
            "revision": r.revision if r else None,
            "live_authorized": r.live_authorized if r else False,
            "health": r.health if r else "not_configured",
            "last_submission_at": r.last_submission_at if r else None,
            "last_callback_at": r.last_callback_at if r else None,
            "credentials_configured": db.get(Credential, "native_slack") is not None,
            "capabilities": {
                "webhook": {
                    "timestamp_returned": False,
                    "automatic_root_thread": False,
                    "manual_api_reconciliation": False,
                },
                "bot": {
                    "timestamp_returned": True,
                    "thread_replies": True,
                    "history_requires_extra_scopes_and_provider_permission": True,
                },
            },
            "events": sorted(EVENTS),
            "counts": dict(
                db.execute(
                    select(Delivery.state, func.count())
                    .where(Delivery.slack_authorization.is_not(None))
                    .group_by(Delivery.state)
                ).all()
            ),
        }

    return tx(db, load)


@router.put("/configuration")
def configure(body: SlackConfig, db=Depends(get_db), user=Depends(require_admin)):
    def save():
        r = db.get(Integration, 1)
        if db.scalar(
            select(Attempt.id)
            .where(
                Attempt.slack_mode.is_not(None),
                Attempt.acceptance.in_(["pending", "unknown"]),
            )
            .limit(1)
        ):
            raise ValueError(
                "Drain/reconcile Slack claims before changing configuration"
            )
        if not r:
            r = Integration(id=1, config={})
            db.add(r)
            db.flush()
        r.config = body.model_dump()
        r.revision += 1
        r.live_authorized = False
        r.health = "configuration_changed"
        audit(db, user.id, "slack_configuration_saved", revision=r.revision)
        return {"revision": r.revision, "live_authorized": False, "messages_sent": 0}

    return tx(db, save)


@router.put("/credentials")
def store(body: SlackCredentials, db=Depends(get_db), user=Depends(require_admin)):
    def save():
        service.store_credentials(db, body.value.get_secret_value(), user.id)
        return {
            "credentials_configured": True,
            "live_authorized": False,
            "messages_sent": 0,
        }

    return tx(db, save)


@router.post("/authorize")
def activate(body: SlackApproval, db=Depends(get_db), user=Depends(require_admin)):
    def save():
        if not body.confirm:
            raise ValueError(
                "Explicit workspace, sender and channel authorization required"
            )
        service.host_authorized()
        r = db.get(Integration, 1)
        if not r:
            raise LookupError()
        r.live_authorized = True
        r.authorization_reference = body.reference
        service.ready(db)
        audit(
            db,
            user.id,
            "slack_provider_authorized",
            revision=r.revision,
            reference=body.reference,
        )
        return {"live_authorized": True, "messages_sent": 0}

    return tx(db, save)


@router.post("/disable")
def disable(db=Depends(get_db), user=Depends(require_admin)):
    def save():
        r = db.get(Integration, 1)
        if r:
            r.live_authorized = False
        audit(db, user.id, "slack_sending_disabled")
        return {"live_authorized": False, "callbacks_unchanged": True}

    return tx(db, save)


@router.post("/preview")
def preview(body: SlackSourceRequest, db=Depends(get_db), user=Depends(require_admin)):
    def load():
        p = service.prepare(db, body.kind, body.identifier, user.id)
        fields = p.snapshot["native_slack"]
        r = db.get(Integration, 1)
        return {
            "plan_id": p.id,
            "fingerprint": p.fingerprint,
            "fields": fields,
            "channel": service.route(r.config, fields) if r else None,
            "payload": sources.payload(fields, r.config, delivery_id=0) if r else None,
            "status": p.status,
            "messages_sent": 0,
        }

    return tx(db, load)


@router.post("/plans/{identifier}/authorize")
def approve(
    identifier: int,
    body: SlackApproval,
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def save():
        if not body.confirm or not body.fingerprint:
            raise ValueError(
                "Review the exact internal event fingerprint before live release"
            )
        d = service.approve(db, identifier, user.id, body.reference, body.fingerprint)
        return {
            "delivery_id": d.id,
            "state": d.state,
            "channel": d.recipient_key,
            "messages_sent": 0,
        }

    return tx(db, save)


@router.get("/deliveries")
def deliveries(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    state: str | None = Query(None, max_length=40),
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def load():
        q = select(Delivery).where(Delivery.slack_authorization.is_not(None))
        if state:
            q = q.where(Delivery.state == state)
        rows = list(
            db.scalars(q.order_by(Delivery.id.desc()).offset(offset).limit(limit))
        )
        return {
            "items": [
                {
                    "id": d.id,
                    "plan_id": d.plan_id,
                    "channel": d.recipient_key,
                    "state": d.state,
                    "error": d.last_error,
                    "fingerprint": (d.slack_authorization or {}).get("fingerprint"),
                    "timestamp": d.provider_receipt,
                    "attempts": d.attempts,
                    "event": (d.slack_context or {}).get("fields", {}).get("event"),
                    "simulated": False,
                }
                for d in rows
            ]
        }

    return tx(db, load)


@router.get("/attempts")
def attempts(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    user=Depends(require_admin),
):
    return tx(
        db,
        lambda: {
            "items": [
                {
                    "id": a.id,
                    "delivery_id": a.delivery_id,
                    "mode": a.slack_mode,
                    "workspace": a.slack_workspace_id,
                    "channel": a.slack_channel_id,
                    "timestamp": a.slack_ts,
                    "thread_ts": a.slack_thread_ts,
                    "acceptance": a.acceptance,
                    "outcome": a.outcome,
                    "created_at": a.created_at,
                    "simulated": False,
                }
                for a in db.scalars(
                    select(Attempt)
                    .where(Attempt.slack_mode.is_not(None))
                    .order_by(Attempt.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ]
        },
    )


@router.get("/threads")
def threads(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    user=Depends(require_admin),
):
    return tx(
        db,
        lambda: {
            "items": [
                {
                    "workspace": t.workspace,
                    "channel": t.channel,
                    "topic": t.topic,
                    "timestamp": t.timestamp,
                    "pending_delivery_id": t.pending_delivery_id,
                }
                for t in db.scalars(
                    select(Thread).order_by(Thread.key).offset(offset).limit(limit)
                )
            ]
        },
    )


@router.post("/attempts/{identifier}/reconcile")
def reconcile(
    identifier: int,
    body: SlackReconcile,
    db=Depends(get_db),
    user=Depends(require_admin),
):
    if not body.confirm:
        raise HTTPException(409, "Explicit read-only provider lookup approval required")
    try:
        a, d, r, provider, payload = service.reconcile(
            db, identifier, body.timestamp, user.id, body.reference
        )
        channel, thread_ts = d.recipient_key, a.slack_thread_ts
        db.commit()  # no global transaction lock during remote read
        result = provider.reconcile(
            channel=channel,
            timestamp=body.timestamp,
            thread_ts=thread_ts,
            text=payload["text"],
        )
        service.accept_reconciliation(db, identifier, result, user.id, body.reference)
        db.commit()
        return {"verified": True, "timestamp": result.timestamp, "messages_sent": 0}
    except LookupError:
        db.rollback()
        raise HTTPException(404, "Slack record not found") from None
    except (SlackFailure, ValueError, OSError):
        db.rollback()
        raise HTTPException(
            409,
            "Slack lookup did not establish acceptance; no resend or nonacceptance inferred",
        ) from None


@router.put("/users/{slack_id}")
def mapping(
    slack_id: str, body: SlackMapping, db=Depends(get_db), user=Depends(require_admin)
):
    import re

    def save():
        r = db.get(Integration, 1)
        u = db.get(User, body.user_id)
        if (
            not re.fullmatch(r"[UW][A-Z0-9]{8,31}", slack_id)
            or not r
            or not u
            or not u.is_active
            or u.role not in {"admin", "operator", "technician"}
            or not set(body.channels) <= set(r.config["approved_channels"])
        ):
            raise ValueError(
                "Approved workspace/channel and active permitted NOC user required"
            )
        m = db.get(UserMap, slack_id)
        if not m:
            m = UserMap(
                slack_id=slack_id,
                workspace=r.config["workspace_id"],
                user_id=u.id,
                channels=body.channels,
                active=body.active,
            )
            db.add(m)
        else:
            m.workspace = r.config["workspace_id"]
            m.user_id = u.id
            m.channels = body.channels
            m.active = body.active
        audit(
            db,
            user.id,
            "slack_user_mapping_updated",
            mapped_user_id=u.id,
            active=body.active,
        )
        return {"slack_id": slack_id, "user_id": u.id, "active": body.active}

    return tx(db, save)


@router.get("/users")
def mappings(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    user=Depends(require_admin),
):
    return tx(
        db,
        lambda: {
            "items": [
                {
                    "slack_id": m.slack_id,
                    "user_id": m.user_id,
                    "workspace": m.workspace,
                    "channels": m.channels,
                    "active": m.active,
                }
                for m in db.scalars(
                    select(UserMap)
                    .order_by(UserMap.slack_id)
                    .offset(offset)
                    .limit(limit)
                )
            ]
        },
    )


@router.get("/interactions")
def interactions(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    db=Depends(get_db),
    user=Depends(require_admin),
):
    return tx(
        db,
        lambda: {
            "items": [
                {
                    "key": x.key,
                    "delivery_id": x.delivery_id,
                    "action": x.action,
                    "state": x.state,
                    "received_at": x.received_at,
                }
                for x in db.scalars(
                    select(Interaction)
                    .order_by(Interaction.received_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ]
        },
    )


@router.post("/deliveries/{identifier}/retry")
def retry(
    identifier: int,
    body: SlackApproval,
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def release():
        if not body.confirm:
            raise ValueError("Explicit retry approval required")
        d = db.get(Delivery, identifier)
        if not d or not d.slack_authorization:
            raise LookupError()
        if d.state != "dead_letter" or d.attempts >= service.policy(db).max_attempts:
            raise ValueError("Retry requires a dead letter below the attempt limit")
        latest = db.scalar(
            select(Attempt)
            .where(Attempt.delivery_id == d.id)
            .order_by(Attempt.id.desc())
            .limit(1)
        )
        if not latest or latest.acceptance != "not_accepted":
            raise ValueError(
                "Proven nonacceptance required; ambiguous submissions cannot be retried"
            )
        service.validate(db, d, clock())
        if body.fingerprint != d.slack_authorization.get("fingerprint"):
            raise ValueError("Review exact approved source fingerprint")
        d.state = "retry"
        d.next_attempt_at = clock()
        d.last_error = None
        audit(
            db,
            user.id,
            "slack_known_rejection_retry_released",
            delivery_id=d.id,
            reference=body.reference,
        )
        return {"state": d.state, "messages_sent": 0}

    return tx(db, release)


@router.post("/deliveries/{identifier}/cancel")
def cancel(
    identifier: int,
    body: SlackApproval,
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def stop():
        if not body.confirm:
            raise ValueError("Explicit cancellation required")
        d = db.get(Delivery, identifier)
        if not d or not d.slack_authorization:
            raise LookupError()
        if d.state not in {"queued", "retry", "dead_letter"}:
            raise ValueError(
                "An in-flight, accepted or ambiguous submission cannot be cancelled as unsent"
            )
        d.state = "suppressed"
        d.last_error = "operator_cancelled"
        audit(
            db,
            user.id,
            "slack_delivery_cancelled",
            delivery_id=d.id,
            reference=body.reference,
        )
        return {"state": d.state, "messages_sent": 0}

    return tx(db, stop)
