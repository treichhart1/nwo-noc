"""Private administrator email and unified evidence APIs; no callback router mounts."""

import base64, json
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from app.api.deps import require_admin
from app.core.database import get_db
from app.core.config import settings
from app.notifications.routes import tx
from app.notifications.email_transport import CAPABILITIES
from app.notifications.email_contracts import (
    EmailConfig,
    EmailRouting,
    EmailSecrets,
    EmailApproval,
    EmailPermission,
    EmailTemplateRequest,
    EmailDraftRequest,
)
from app.notifications.email_models import (
    EmailProvider as Provider,
    EmailRoutingRow as Routing,
    EmailPreference as Preference,
    EmailTemplate as Template,
    EmailEnvelope as Envelope,
)
from app.notifications.models import (
    NotificationDelivery as Delivery,
    NotificationPlan as Plan,
    NotificationAttempt as Attempt,
    NotificationCredential as Credential,
)
from app.notifications import email_service as s
from app.notifications.secrets import seal
from app.notifications.service import audit, clock
from app.customers.contracts import SafeModel
from pydantic import Field

router = APIRouter(
    prefix="/api/communications",
    tags=["Unified communications"],
    dependencies=[Depends(require_admin)],
)


@router.get("")
def dashboard(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
    channel: str | None = Query(None, max_length=20),
    db=Depends(get_db),
):
    def load():
        from app.customer_status.models import Publication

        publications = [
            {
                "id": p.id,
                "incident_id": p.incident_id,
                "maintenance_id": p.maintenance_id,
                "published_revision_id": p.published_revision_id,
                "channel": "wordpress",
                "delivery_evidence": "publication only, not recipient delivery",
            }
            for p in db.scalars(
                select(Publication).order_by(Publication.id.desc()).limit(50)
            )
        ]
        q = select(Delivery)
        if channel:
            q = q.where(Delivery.channel == channel)
        rows = list(
            db.scalars(q.order_by(Delivery.id.desc()).offset(offset).limit(limit))
        )
        return {
            "publications": publications,
            "items": [
                {
                    "id": d.id,
                    "plan_id": d.plan_id,
                    "channel": d.channel,
                    "category": db.get(Plan, d.plan_id).kind,
                    "source": db.get(Plan, d.plan_id).target,
                    "status": d.state,
                    "approval": db.get(Plan, d.plan_id).status,
                    "provider": (
                        db.get(Envelope, d.id).provider
                        if d.channel == "email" and db.get(Envelope, d.id)
                        else d.sms_provider
                        or ("slack" if d.channel == "slack" else None)
                    ),
                    "provider_status": d.provider_status,
                    "attempts": d.attempts,
                    "estimated_cost_microunits": d.budget_charge_microunits,
                    "delivered": d.state == "delivered",
                    "simulated": d.state == "simulated",
                }
                for d in rows
            ],
            "counts": [
                {"channel": c, "status": state, "count": n}
                for c, state, n in db.execute(
                    select(Delivery.channel, Delivery.state, func.count()).group_by(
                        Delivery.channel, Delivery.state
                    )
                )
            ],
            "public_status": "Independent publication approval; /api/customer-status remains authoritative",
            "native_enabled": settings.email_native_transport_enabled,
            "email_capabilities": CAPABILITIES,
            "email_providers": [
                {
                    "name": p.name,
                    "config": p.config,
                    "revision": p.revision,
                    "live_authorized": p.live_authorized,
                    "health": p.health,
                    "last_submission_at": p.last_submission_at,
                    "last_callback_at": p.last_callback_at,
                    "credentials_configured": db.get(
                        Credential, "email.native." + p.name
                    )
                    is not None,
                }
                for p in db.scalars(select(Provider).order_by(Provider.name))
            ],
            "routing": db.get(Routing, 1).config
            if db.get(Routing, 1)
            else {"primary": "mock", "failover": False},
            "templates": [
                {"id": t.id, "key": t.key, "revision": t.revision, "config": t.config}
                for t in db.scalars(
                    select(Template).order_by(Template.id.desc()).limit(100)
                )
            ],
        }

    return tx(db, load)


@router.put("/email/providers/{name}")
def configure(
    name: str, body: EmailConfig, db=Depends(get_db), u=Depends(require_admin)
):
    def save():
        if name not in {"mock", "sendgrid", "smtp"}:
            raise ValueError("Supported email provider required")
        if db.scalar(
            select(Attempt.id)
            .where(
                Attempt.email_provider == name,
                Attempt.acceptance.in_(["pending", "unknown"]),
            )
            .limit(1)
        ):
            raise ValueError("Reconcile outstanding intents before reconfiguration")
        if name == "smtp" and body.smtp_host not in settings.email_smtp_allowed_hosts:
            raise ValueError("Explicit host SMTP allowlist required")
        p = db.get(Provider, name)
        if not p:
            p = Provider(name=name, config={}, revision=0)
            db.add(p)
        p.config = body.model_dump()
        p.revision += 1
        p.live_authorized = False
        audit(db, u.id, "email_provider_configured", provider=name, revision=p.revision)
        return {"revision": p.revision, "live_authorized": False}

    return tx(db, save)


@router.put("/email/routing")
def routing(body: EmailRouting, db=Depends(get_db), u=Depends(require_admin)):
    def save():
        r = db.get(Routing, 1)
        if not r:
            r = Routing(id=1, config={}, revision=0)
            db.add(r)
        r.config = body.model_dump()
        r.revision += 1
        audit(db, u.id, "email_routing_reviewed", revision=r.revision)
        return r.config

    return tx(db, save)


@router.put("/email/providers/{name}/credentials")
def credentials(
    name: str, body: EmailSecrets, db=Depends(get_db), u=Depends(require_admin)
):
    def save():
        p = db.get(Provider, name)
        if not p or name not in {"sendgrid", "smtp"}:
            raise ValueError("Configure native provider first")
        if db.scalar(
            select(Attempt.id)
            .where(
                Attempt.email_provider == name,
                Attempt.acceptance.in_(["pending", "unknown"]),
            )
            .limit(1)
        ):
            raise ValueError("Reconcile before credential/key rotation")
        try:
            value = json.loads(body.value.get_secret_value())
            required = (
                {"api_key", "webhook_public_key"}
                if name == "sendgrid"
                else {"username", "password"}
            )
            if (
                not isinstance(value, dict)
                or set(value) != required
                or any(
                    not isinstance(v, str)
                    or not 1 <= len(v) <= 2048
                    or any(ord(c) < 32 for c in v)
                    for v in value.values()
                )
            ):
                raise ValueError()
            if name == "sendgrid":
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.asymmetric import ec

                key = serialization.load_der_public_key(
                    base64.b64decode(value["webhook_public_key"], validate=True)
                )
                if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
                    key.curve, ec.SECP256R1
                ):
                    raise ValueError()
                newkey = value.pop("webhook_public_key")
                if (
                    p.webhook_public_key
                    and p.webhook_public_key != newkey
                    and db.scalar(
                        select(Attempt.id)
                        .where(
                            Attempt.email_provider == "sendgrid",
                            Attempt.acceptance != "not_accepted",
                        )
                        .limit(1)
                    )
                ):
                    raise ValueError()
                p.webhook_public_key = newkey
        except (ValueError, TypeError):
            raise ValueError("Invalid private email credential contract") from None
        key = "email.native." + name
        row = db.get(Credential, key)
        encrypted = seal(settings.notifications_vault_key_file, json.dumps(value))
        if row:
            row.ciphertext = encrypted
        else:
            db.add(Credential(provider=key, ciphertext=encrypted))
        p.revision += 1
        p.live_authorized = False
        audit(db, u.id, "email_credentials_rotated", provider=name)
        return {"credentials_configured": True, "live_authorized": False}

    return tx(db, save)


@router.post("/email/providers/{name}/authorize")
def authorize(
    name: str, body: EmailApproval, db=Depends(get_db), u=Depends(require_admin)
):
    def save():
        if not body.confirm:
            raise ValueError("Explicit live provider authorization required")
        s.host_authorized()
        p = db.get(Provider, name)
        if not p:
            raise LookupError()
        p.live_authorized = True
        p.authorization_reference = body.reference
        s.ready(db, name)
        audit(
            db,
            u.id,
            "email_provider_authorized",
            provider=name,
            reference=body.reference,
        )
        return {"live_authorized": True, "messages_sent": 0}

    return tx(db, save)


@router.post("/email/disable")
def disable(db=Depends(get_db), u=Depends(require_admin)):
    def save():
        for p in db.scalars(select(Provider)):
            p.live_authorized = False
        audit(db, u.id, "email_emergency_disabled")
        return {"live_authorized": False}

    return tx(db, save)


@router.put("/email/preferences")
def preferences(body: EmailPermission, db=Depends(get_db), u=Depends(require_admin)):
    def save():
        p = s.permission(db, body, u.id)
        return {
            "id": p.id,
            "revision": p.revision,
            "granted": p.granted,
            "contact_hash": p.contact_hash,
            "other_channels_changed": False,
        }

    return tx(db, save)


@router.post("/email/templates")
def template(body: EmailTemplateRequest, db=Depends(get_db), u=Depends(require_admin)):
    def save():
        version = (
            db.scalar(
                select(func.max(Template.revision)).where(Template.key == body.key)
            )
            or 0
        ) + 1
        t = Template(
            key=body.key, revision=version, config=body.model_dump(), created_by=u.id
        )
        db.add(t)
        db.flush()
        audit(db, u.id, "email_template_revision_created", template_id=t.id)
        return {
            "id": t.id,
            "revision": version,
            "preview": s.render(
                t.config, {"reference": "synthetic-reference", "status": "under_review"}
            ),
        }

    return tx(db, save)


@router.post("/email/drafts")
def draft(body: EmailDraftRequest, db=Depends(get_db), u=Depends(require_admin)):
    def save():
        p = s.prepare(db, body, u.id)
        d = db.scalar(select(Delivery).where(Delivery.plan_id == p.id))
        e = db.get(Envelope, d.id)
        return {
            "plan_id": p.id,
            "delivery_id": d.id,
            "fingerprint": p.fingerprint,
            "preview": s.decrypt(e.ciphertext)["payload"],
            "status": p.status,
            "messages_sent": 0,
        }

    return tx(db, save)


class EmailRelease(EmailApproval):
    simulation: bool = False
    provider: str | None = Field(None, pattern=r"^(mock|smtp|sendgrid)$")


@router.post("/email/deliveries/{identifier}/approve")
def release(
    identifier: int, body: EmailRelease, db=Depends(get_db), u=Depends(require_admin)
):
    def save():
        d = s.approve(
            db, identifier, body, u.id, mock=body.simulation, provider=body.provider
        )
        return {"id": d.id, "state": d.state, "messages_sent": 0}

    return tx(db, save)


@router.get("/attempts/{identifier}")
def attempts(identifier: int, db=Depends(get_db)):
    return tx(
        db,
        lambda: {
            "items": [
                {
                    "id": a.id,
                    "provider": a.email_provider or a.provider_name or a.slack_mode,
                    "acceptance": a.acceptance,
                    "outcome": a.outcome,
                    "provider_message_id": a.provider_message_id,
                    "raw_status": a.raw_status,
                    "estimated_cost_microunits": a.estimated_cost_microunits,
                    "reported_cost_microunits": a.reported_cost_microunits,
                    "currency": a.currency,
                    "created_at": a.created_at,
                }
                for a in db.scalars(
                    select(Attempt)
                    .where(Attempt.delivery_id == identifier)
                    .order_by(Attempt.id)
                )
            ]
        },
    )


@router.post("/email/attempts/{identifier}/reconcile-accepted")
def reconcile(
    identifier: int, body: EmailApproval, db=Depends(get_db), u=Depends(require_admin)
):
    def save():
        a = db.get(Attempt, identifier)
        if (
            not body.confirm
            or not a
            or not a.email_provider
            or a.acceptance not in {"unknown", "pending"}
        ):
            raise ValueError("Explicit reviewed uncertain email attempt required")
        d = db.get(Delivery, a.delivery_id)
        if body.fingerprint != db.get(Plan, d.plan_id).fingerprint:
            raise ValueError("Review exact approved communication")
        a.acceptance = "accepted"
        a.outcome = "operator_attested_acceptance"
        a.updated_at = clock()
        if d.state in {"submitting", "reconciliation_required"}:
            d.state = "accepted"
        audit(
            db,
            u.id,
            "email_manual_acceptance_evidence",
            attempt_id=a.id,
            reference=body.reference,
        )
        return {
            "state": d.state,
            "evidence": "operator_attestation_only",
            "confirmed_delivery": False,
            "resend_authorized": False,
        }

    return tx(db, save)


@router.post("/email/deliveries/{identifier}/cancel")
def cancel(
    identifier: int, body: EmailApproval, db=Depends(get_db), u=Depends(require_admin)
):
    def save():
        d = db.get(Delivery, identifier)
        if (
            not body.confirm
            or not d
            or d.channel != "email"
            or d.state not in {"draft", "queued", "retry", "dead_letter", "suppressed"}
        ):
            raise ValueError(
                "Only unsubmitted or proven rejection can be cancelled as unsent"
            )
        if db.scalar(
            select(Attempt.id)
            .where(Attempt.delivery_id == d.id, Attempt.acceptance != "not_accepted")
            .limit(1)
        ):
            raise ValueError(
                "Accepted or ambiguous provider intent cannot be cancelled as unsent"
            )
        d.state = "suppressed"
        d.last_error = "operator_cancelled"
        audit(
            db,
            u.id,
            "email_pending_cancelled",
            delivery_id=d.id,
            reference=body.reference,
        )
        return {"state": d.state, "messages_sent": 0}

    return tx(db, save)


@router.get("/email/analytics")
def analytics(db=Depends(get_db)):
    def load():
        rows = db.execute(
            select(
                Attempt.email_provider,
                Attempt.acceptance,
                Attempt.outcome,
                func.count(),
                func.sum(Attempt.estimated_cost_microunits),
                func.sum(Attempt.reported_cost_microunits),
                Attempt.currency,
            )
            .where(Attempt.email_provider.is_not(None))
            .group_by(
                Attempt.email_provider,
                Attempt.acceptance,
                Attempt.outcome,
                Attempt.currency,
            )
        ).all()
        return {
            "items": [
                {
                    "provider": p,
                    "acceptance": a,
                    "outcome": o,
                    "count": n,
                    "estimated_cost_microunits": estimate,
                    "reported_cost_microunits": actual,
                    "currency": currency,
                }
                for p, a, o, n, estimate, actual, currency in rows
            ],
            "delivery_denominator": "Unique canonical delivery records; SMTP acceptance is not final delivery",
            "open_tracking": False,
            "unknown_costs": "unavailable, never zero",
            "cross_currency_totals": None,
        }

    return tx(db, load)


@router.post("/email/redact-expired")
def redact(db=Depends(get_db), u=Depends(require_admin)):
    def save():
        from datetime import timedelta
        from app.notifications.service import policy

        cutoff = clock() - timedelta(days=policy(db).retention_days)
        rows = list(
            db.scalars(
                select(Envelope)
                .join(Delivery, Delivery.id == Envelope.delivery_id)
                .join(Plan, Plan.id == Delivery.plan_id)
                .where(
                    Plan.created_at < cutoff,
                    Delivery.state.in_(
                        [
                            "delivered",
                            "failed",
                            "undelivered",
                            "suppressed",
                            "dead_letter",
                            "simulated",
                            "complaint",
                            "unsubscribed",
                        ]
                    ),
                )
                .limit(100)
            )
        )
        n = 0
        for e in rows:
            if db.scalar(
                select(Attempt.id)
                .where(
                    Attempt.delivery_id == e.delivery_id,
                    Attempt.acceptance.in_(["pending", "unknown"]),
                )
                .limit(1)
            ):
                continue
            # Retain deduplication and provider identity, erase encrypted private body.
            e.ciphertext = s.encrypt({"redacted": True})
            n += 1
        audit(db, u.id, "email_expired_content_redacted", count=n)
        return {"redacted": n, "receipts_preserved": True}

    return tx(db, save)


class EmailHold(SafeModel):
    verified_address: str = Field(min_length=3, max_length=254)
    reason: str = Field(pattern="^(unsubscribe|bounce|complaint|operator_hold)$")
    reference: str = Field(
        min_length=10, max_length=120, pattern=r"^[A-Za-z0-9_.: -]+$"
    )
    confirm: bool = False


@router.post("/email/suppress")
def hold(body: EmailHold, db=Depends(get_db), u=Depends(require_admin)):
    def save():
        from app.notifications.email_models import EmailSuppression

        if not body.confirm:
            raise ValueError("Verified identity/evidence required")
        h = s.identity(body.verified_address)
        if not db.get(EmailSuppression, h):
            db.add(
                EmailSuppression(
                    contact_hash=h, reason=body.reason, evidence=body.reference
                )
            )
        audit(
            db,
            u.id,
            "email_shared_suppression_recorded",
            reason=body.reason,
            reference=body.reference,
        )
        return {
            "suppressed": True,
            "other_channels_changed": False,
            "automatic_reenable": False,
        }

    return tx(db, save)


@router.post("/email/mock-drain")
def mock_drain(body: EmailApproval, u=Depends(require_admin)):
    from app.core.database import SessionLocal
    from fastapi import HTTPException

    if not body.confirm or not settings.offline_mode or settings.app_env != "staging":
        raise HTTPException(
            409, "Explicit offline-staging simulation approval required"
        )
    return s.run_batch(SessionLocal, mock=True)
