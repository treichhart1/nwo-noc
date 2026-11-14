from datetime import datetime, timedelta, timezone
from typing import Literal
import json
import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import Field, SecretStr
from sqlalchemy import select, func, or_, cast
from sqlalchemy.exc import SQLAlchemyError
from app.models.entities import AuditLog, MaintenanceWindow
from app.api.deps import require_admin
from app.customers.routes import customer_operator
from app.customers.contracts import SafeModel
from app.core.database import get_db
from app.core.config import settings
from app.customers.models import CustomerAccount
from app.customers.impact import Target, target_for
from app.notifications.models import (
    NotificationPolicy,
    NotificationTemplate,
    NotificationMaintenance,
    NotificationPlan,
    NotificationRecipient,
    NotificationDelivery,
    NotificationAttempt,
    NotificationSuppression,
    NotificationCredential,
)
from app.notifications.contracts import Policy, PlanRequest, MaintenanceRequest, KINDS
from app.notifications.service import (
    create_plan,
    approve,
    audit,
    policy,
    template,
    TEMPLATE_KEYS,
    validate_template,
    lock,
    snapshot,
    digest,
    clock,
    utc,
)
from app.notifications.worker import run_batch
from app.notifications.providers import MockSender, MockSlack
from app.notifications.secrets import seal

router = APIRouter(
    prefix="/api/notifications",
    tags=["Operational notifications"],
    dependencies=[Depends(customer_operator)],
)


def tx(db, fn):
    try:
        lock(db)
        result = fn()
        db.commit()
        return result
    except LookupError:
        db.rollback()
        raise HTTPException(404, "Notification record not found") from None
    except ValueError as e:
        db.rollback()
        raise HTTPException(409, str(e)) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            503, "Notification storage unavailable; durable queues retained"
        ) from None


def view(p):
    return {
        "id": p.id,
        "fingerprint": p.fingerprint,
        "incident_id": p.incident_id,
        "maintenance_id": p.maintenance_id,
        "kind": p.kind,
        "severity": p.severity,
        "confidence": p.confidence,
        "status": p.status,
        "message": p.message,
        "slack_payload": p.slack_payload,
        "due_at": p.due_at,
        "created_at": p.created_at,
        "approved_by": p.approved_by,
        "approved_at": p.approved_at,
        "estimated_cost_microunits": p.estimated_cost_microunits,
        "simulation_only": True,
        "external_delivery_enabled": False,
    }


@router.get("")
def dashboard(
    tower_id: int | None = Query(None, ge=1),
    customer_id: int | None = Query(None, ge=1),
    incident_id: int | None = Query(None, ge=1),
    event_type: str | None = Query(None, max_length=40),
    delivery_status: (
        Literal["queued", "retry", "simulated", "dead_letter", "suppressed"] | None
    ) = None,
    since: datetime | None = None,
    until: datetime | None = None,
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=100),
    db=Depends(get_db),
    user=Depends(customer_operator),
):
    def load():
        p = policy(db)
        statement = select(NotificationPlan)
        if tower_id:
            from sqlalchemy.dialects.postgresql import JSONB

            statement = statement.where(
                or_(
                    NotificationPlan.target["site_id"].as_integer() == tower_id,
                    cast(
                        NotificationPlan.snapshot["context"]["targets"], JSONB
                    ).contains([{"site_id": tower_id}]),
                )
            )
        if customer_id:
            statement = statement.where(
                NotificationPlan.id.in_(
                    select(NotificationRecipient.plan_id).where(
                        NotificationRecipient.customer_id == customer_id
                    )
                )
            )
        if incident_id:
            statement = statement.where(NotificationPlan.incident_id == incident_id)
        if event_type:
            statement = statement.where(
                NotificationPlan.evidence["event_type"].as_string() == event_type
            )
        if delivery_status:
            statement = statement.where(
                NotificationPlan.id.in_(
                    select(NotificationDelivery.plan_id).where(
                        NotificationDelivery.state == delivery_status
                    )
                )
            )
        for value in (since, until):
            if value and value.tzinfo is None:
                raise ValueError("Date filters require an explicit time zone")
        if since and until and since > until:
            raise ValueError("Date filter range is reversed")
        if since:
            statement = statement.where(NotificationPlan.created_at >= since)
        if until:
            statement = statement.where(NotificationPlan.created_at <= until)
        total = db.scalar(select(func.count()).select_from(statement.subquery()))
        plans = list(
            db.scalars(
                statement.order_by(NotificationPlan.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        from app.notifications.controls import (
            control_status,
            NotificationProviderConfig,
        )

        audit(db, user.id, "accessed", operation="dashboard", returned=len(plans))
        return {
            "items": [view(x) for x in plans],
            "total": total,
            "controls": control_status(db),
            "provider_configuration": [
                {
                    "channel": c.channel,
                    "adapter": c.adapter,
                    "sender_configured": bool(c.sender),
                    "last_health": c.last_health,
                    "last_health_at": c.last_health_at,
                }
                for c in db.scalars(select(NotificationProviderConfig))
            ],
            "counts": dict(
                db.execute(
                    select(NotificationDelivery.state, func.count()).group_by(
                        NotificationDelivery.state
                    )
                ).all()
            ),
            "policy": p.model_dump(),
            "templates": [
                {"key": k, "body": template(db, k)[0], "revision": template(db, k)[1]}
                for k in TEMPLATE_KEYS
            ],
            "maintenance": [
                {
                    "id": m.id,
                    "title": m.title,
                    "target": m.target,
                    "targets": m.targets or [m.target],
                    "description": m.description,
                    "advance_notice_minutes": m.advance_notice_minutes,
                    "starts_at": m.starts_at,
                    "ends_at": m.ends_at,
                    "status": m.status,
                    "revision": m.revision,
                }
                for m in db.scalars(
                    select(NotificationMaintenance)
                    .order_by(NotificationMaintenance.id.desc())
                    .limit(100)
                )
            ],
            "provider_status": {
                "sms": "mock_only",
                "slack": "mock_only",
                "external_delivery_enabled": False,
                "worker_enabled": settings.notifications_worker_enabled,
                "vault_configured": list(
                    db.scalars(select(NotificationCredential.provider))
                ),
                "currency": "USD",
                "cost_estimate": "conservative UTF-16 segments; provider taxes/fees unverified",
            },
            "unredacted_customer_contacts": False,
        }

    return tx(db, load)


@router.post("/plans")
def prepare(body: PlanRequest, db=Depends(get_db), user=Depends(customer_operator)):
    return tx(db, lambda: view(create_plan(db, body, user.id)))


@router.get("/plans/{ident}")
def detail(
    ident: int = Path(ge=1),
    recipient_offset: int = Query(0, ge=0, le=10000),
    audit_before: int = Query(0, ge=0),
    db=Depends(get_db),
    user=Depends(customer_operator),
):
    def load():
        p = db.get(NotificationPlan, ident)
        if not p:
            raise LookupError()
        audit(db, user.id, "accessed", operation="plan", plan_id=ident)
        total_recipients = db.scalar(
            select(func.count(NotificationRecipient.id)).where(
                NotificationRecipient.plan_id == ident
            )
        )
        audits = list(
            db.scalars(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == "notification",
                    AuditLog.entity_id == str(ident),
                    AuditLog.id < audit_before if audit_before else AuditLog.id > 0,
                )
                .order_by(AuditLog.id.desc())
                .limit(101)
            )
        )
        return view(p) | {
            "audit_has_more": len(audits) > 100,
            "audit_next": audits[99].id if len(audits) > 100 else None,
            "recipient_total": total_recipients,
            "recipient_offset": recipient_offset,
            "recipient_has_more": recipient_offset + 500 < total_recipients,
            "recipient_counts": dict(
                db.execute(
                    select(NotificationRecipient.state, func.count())
                    .where(NotificationRecipient.plan_id == ident)
                    .group_by(NotificationRecipient.state)
                ).all()
            ),
            "recipients": [
                {
                    "customer_id": r.customer_id,
                    "service_ids": r.service_ids,
                    "state": r.state,
                    "reason": r.reason,
                }
                for r in db.scalars(
                    select(NotificationRecipient)
                    .where(NotificationRecipient.plan_id == ident)
                    .order_by(NotificationRecipient.id)
                    .offset(recipient_offset)
                    .limit(500)
                )
            ],
            "deliveries": [
                {
                    "id": d.id,
                    "channel": d.channel,
                    "state": d.state,
                    "attempts": d.attempts,
                    "next_attempt_at": d.next_attempt_at,
                    "last_error": d.last_error,
                    "provider_status": d.provider_status,
                    "provider_status_at": d.provider_status_at,
                    "completed_at": d.completed_at,
                }
                for d in db.scalars(
                    select(NotificationDelivery)
                    .where(NotificationDelivery.plan_id == ident)
                    .limit(1200)
                )
            ],
            "audit": [
                {
                    "id": a.id,
                    "action": a.action,
                    "at": a.created_at,
                    "actor": a.user_id,
                    "details": a.details,
                }
                for a in audits[:100]
            ],
        }

    return tx(db, load)


class Confirmation(SafeModel):
    confirm_simulation: bool = Field(strict=True)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/plans/{ident}/approve")
def approve_plan(
    body: Confirmation,
    ident: int = Path(ge=1),
    db=Depends(get_db),
    user=Depends(customer_operator),
):
    if body.confirm_simulation is not True:
        raise HTTPException(
            409,
            "Explicit operator confirmation required; no live delivery is available",
        )
    return tx(
        db,
        lambda: view(
            approve(db, ident, user.id, expected_fingerprint=body.fingerprint)
        ),
    )


@router.post("/plans/{ident}/cancel")
def cancel_plan(
    ident: int = Path(ge=1), db=Depends(get_db), user=Depends(customer_operator)
):
    def cancel():
        p = db.get(NotificationPlan, ident)
        if not p:
            raise LookupError()
        p.status = "cancelled"
        for d in db.scalars(
            select(NotificationDelivery).where(
                NotificationDelivery.plan_id == ident,
                NotificationDelivery.state.in_(["queued", "retry"]),
            )
        ):
            d.state = "suppressed"
            d.last_error = "operator_cancelled"
            d.completed_at = clock()
        audit(db, user.id, "cancelled", plan_id=ident)
        return view(p)

    return tx(db, cancel)


@router.post("/maintenance")
def maintenance(
    body: MaintenanceRequest, db=Depends(get_db), user=Depends(customer_operator)
):
    def save():
        target = {
            k: getattr(body, k)
            for k in ("site_id", "device_id", "sector_id", "link_id")
            if getattr(body, k)
        }
        targets = (
            [t.model_dump(exclude_none=True) for t in body.targets]
            if body.targets
            else [target]
        )
        for scope in targets:
            target_for(db, target=Target(**scope))
        target = targets[0]
        window = None
        if body.site_id or body.link_id:
            window = MaintenanceWindow(
                title=body.title,
                site_id=body.site_id,
                link_id=body.link_id,
                starts_at=body.starts_at,
                ends_at=body.ends_at,
                status="scheduled",
                created_by=user.id,
                description="NOC-96 approved notification schedule; equipment unchanged",
            )
            db.add(window)
            db.flush()
        row = NotificationMaintenance(
            operations_window_id=window.id if window else None,
            title=body.title,
            targets=targets,
            description=body.description,
            advance_notice_minutes=body.advance_notice_minutes,
            target=target,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            status="scheduled",
            created_by=user.id,
        )
        db.add(row)
        db.flush()
        audit(db, user.id, "maintenance_created", maintenance_id=row.id)
        return {"id": row.id, "revision": row.revision, "status": row.status}

    return tx(db, save)


class Change(SafeModel):
    revision: int = Field(ge=1)
    action: Literal["reschedule", "cancel", "complete"]
    starts_at: datetime | None = None
    ends_at: datetime | None = None


@router.patch("/maintenance/{ident}")
def change_maintenance(
    body: Change,
    ident: int = Path(ge=1),
    db=Depends(get_db),
    user=Depends(customer_operator),
):
    def change():
        row = db.get(NotificationMaintenance, ident)
        if not row:
            raise LookupError()
        if row.revision != body.revision:
            raise ValueError("Maintenance changed; refresh before editing")
        if row.status != "scheduled":
            raise ValueError("Maintenance lifecycle is terminal")
        if body.action == "reschedule":
            check = MaintenanceRequest(
                title=row.title,
                **row.target,
                starts_at=body.starts_at,
                ends_at=body.ends_at
            )
            row.starts_at = check.starts_at
            row.ends_at = check.ends_at
        else:
            row.status = "cancelled" if body.action == "cancel" else "completed"
            if body.action == "complete":
                if utc(row.starts_at) > clock():
                    raise ValueError(
                        "Maintenance has not started; cancel instead of completing early"
                    )
                row.ends_at = clock()
        row.revision += 1
        if row.operations_window_id:
            window = db.get(MaintenanceWindow, row.operations_window_id)
            if window:
                window.starts_at = row.starts_at
                window.ends_at = row.ends_at
                window.status = row.status
        for plan in db.scalars(
            select(NotificationPlan).where(
                NotificationPlan.maintenance_id == ident,
                NotificationPlan.status.in_(["draft", "approved"]),
            )
        ):
            plan.status = "superseded"
            for d in db.scalars(
                select(NotificationDelivery).where(
                    NotificationDelivery.plan_id == plan.id,
                    NotificationDelivery.state.in_(["queued", "retry"]),
                )
            ):
                d.state = "suppressed"
                d.last_error = "maintenance_changed"
                d.completed_at = clock()
        audit(
            db,
            user.id,
            "maintenance_" + body.action,
            maintenance_id=ident,
            revision=row.revision,
        )
        return {"id": ident, "revision": row.revision, "status": row.status}

    return tx(db, change)


@router.put("/policy")
def update_policy(body: Policy, db=Depends(get_db), user=Depends(require_admin)):
    def save():
        row = db.get(NotificationPolicy, 1)
        if not row:
            row = NotificationPolicy(id=1, config=body.model_dump())
            db.add(row)
        else:
            row.config = body.model_dump()
            row.revision += 1
        audit(db, user.id, "policy_updated")
        return body.model_dump()

    return tx(db, save)


class TemplateBody(SafeModel):
    body: str = Field(min_length=20, max_length=480)


@router.put("/templates/{key}")
def update_template(
    key: Literal[
        "outage",
        "update",
        "extended",
        "maintenance",
        "rescheduled",
        "cancelled",
        "restoration",
        "backhaul",
        "power",
        "reminder",
        "delay",
    ],
    body: TemplateBody,
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def save():
        validate_template(body.body)
        row = db.get(NotificationTemplate, key)
        if not row:
            row = NotificationTemplate(key=key, body=body.body)
            db.add(row)
        else:
            row.body = body.body
            row.revision += 1
        audit(db, user.id, "template_updated", template_key=key)
        return {"key": key}

    return tx(db, save)


class Suppress(SafeModel):
    enabled: bool
    reason: Literal["operator_hold", "opt_out", "compliance"] = "operator_hold"


@router.put("/suppressions/{ident}")
def suppression(
    body: Suppress,
    ident: int = Path(ge=1),
    db=Depends(get_db),
    user=Depends(customer_operator),
):
    def save():
        a = db.get(CustomerAccount, ident)
        if not a:
            raise LookupError()
        row = db.get(NotificationSuppression, ident)
        if body.enabled:
            if not row:
                db.add(NotificationSuppression(customer_id=ident, reason=body.reason))
            else:
                row.reason = body.reason
            if body.reason == "opt_out":
                a.sms_opt_out = True
                a.opt_out_at = clock()
        elif row:
            db.delete(row)  # Clearing a hold never re-grants consent or opt-in.
        audit(
            db, user.id, "suppression_changed", customer_id=ident, enabled=body.enabled
        )
        return {"customer_id": ident, "suppressed": body.enabled}

    return tx(db, save)


@router.post("/mock-drain")
def drain(db=Depends(get_db), user=Depends(require_admin)):
    def process():
        result = run_batch(db)
        audit(db, user.id, "manual_mock_worker")
        return result

    return tx(db, process)


@router.post("/mock-test")
def mock_test(db=Depends(get_db), user=Depends(require_admin)):
    def test():
        from app.notifications.controls import require_processing

        require_processing(db)
        if not settings.offline_mode or settings.app_env != "staging":
            raise ValueError("Mock tests require offline staging")
        sms = MockSender().send(
            key="safe-test",
            destination="+15555550996",
            payload={"body": "Synthetic service test only"},
        )
        slack = MockSlack(
            httpx.MockTransport(lambda r: httpx.Response(200, text="ok"))
        ).send(
            key="safe-test",
            destination="noc-simulation",
            payload={"text": "Synthetic technician test only"},
        )
        audit(db, user.id, "mock_test", simulation_only=True)
        return {
            "simulation_only": True,
            "sms": sms,
            "slack": slack,
            "external_delivery_enabled": False,
        }

    return tx(db, test)


class Credential(SafeModel):
    value: SecretStr = Field(max_length=4096)


@router.put("/credentials/{provider}")
def credential(
    provider: Literal["sms", "slack"],
    body: Credential,
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def save():
        try:
            value = seal(
                settings.notifications_vault_key_file, body.value.get_secret_value()
            )
        except (OSError, ValueError):
            raise ValueError(
                "Private provider encryption key unavailable; configure the approved secret mount"
            ) from None
        row = db.get(NotificationCredential, provider)
        if not row:
            db.add(NotificationCredential(provider=provider, ciphertext=value))
        else:
            row.ciphertext = value
            row.updated_at = clock()
        audit(db, user.id, "encrypted_credential_stored", provider=provider)
        return {"provider": provider, "encrypted": True, "live_enabled": False}

    return tx(db, save)


@router.post("/redact-expired")
def redact(db=Depends(get_db), user=Depends(require_admin)):
    def run():
        cutoff = clock() - timedelta(days=policy(db).retention_days)
        n = 0
        for p in db.scalars(
            select(NotificationPlan)
            .where(
                NotificationPlan.created_at < cutoff,
                NotificationPlan.status.in_(["complete", "cancelled", "superseded"]),
            )
            .limit(1000)
        ):
            p.message = "[redacted]"
            p.slack_payload = {}
            p.snapshot = {}
            p.evidence = {}
            for r in db.scalars(
                select(NotificationRecipient).where(
                    NotificationRecipient.plan_id == p.id
                )
            ):
                r.contact_hash = None
                r.service_ids = []
            n += 1
        audit(db, user.id, "retention_redacted", plans=n)
        return {"redacted": n, "dedupe_receipts_retained": True}

    return tx(db, run)


@router.post("/deliveries/{ident}/retry")
def retry_delivery(
    body: Confirmation,
    ident: int = Path(ge=1),
    db=Depends(get_db),
    user=Depends(customer_operator),
):
    def retry():
        d = db.get(NotificationDelivery, ident)
        if not d:
            raise LookupError()
        if body.fingerprint != db.get(NotificationPlan, d.plan_id).fingerprint:
            raise ValueError("Plan changed; review before retry")
        if not body.confirm_simulation or d.state != "dead_letter":
            raise ValueError(
                "Only explicitly confirmed dead-letter simulations may be retried"
            )
        p = db.get(NotificationPlan, d.plan_id)
        current, _ = snapshot(db, PlanRequest.model_validate(p.snapshot["request"]))
        if digest(current) != p.fingerprint:
            raise ValueError("Approved context changed; fresh operator review required")
        p.status = "approved"
        p.approved_by = user.id
        p.approved_at = clock()
        d.state = "retry"
        d.next_attempt_at = clock()
        audit(db, user.id, "dead_letter_retry_approved", plan_id=p.id, delivery_id=d.id)
        return {
            "id": ident,
            "state": d.state,
            "attempts_retained": d.attempts,
            "simulation_only": True,
        }

    return tx(db, retry)


class LocalTime(SafeModel):
    value: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$")
    timezone: str = Field(max_length=80)


@router.post("/local-time")
def resolve_local_time(
    body: LocalTime, db=Depends(get_db), user=Depends(customer_operator)
):
    def resolve():
        from zoneinfo import ZoneInfo

        p = policy(db)
        if body.timezone != p.timezone:
            raise ValueError(
                "Configured time zone changed; refresh the notification dashboard"
            )
        wall = datetime.fromisoformat(body.value)
        zone = ZoneInfo(p.timezone)
        candidates = {
            wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
            for fold in (0, 1)
            if wall.replace(tzinfo=zone, fold=fold)
            .astimezone(timezone.utc)
            .astimezone(zone)
            .replace(tzinfo=None)
            == wall
        }
        if len(candidates) != 1:
            raise ValueError(
                "Ambiguous or nonexistent local time; choose an unambiguous time or supply an explicitly offset UTC API timestamp"
            )
        audit(db, user.id, "local_time_resolved", timezone=p.timezone)
        return {"utc": next(iter(candidates)).isoformat(), "timezone": p.timezone}

    return tx(db, resolve)


class Pause(SafeModel):
    paused: bool = Field(strict=True)
    confirm: bool = Field(strict=True)


@router.put("/pause")
def pause(body: Pause, db=Depends(get_db), user=Depends(require_admin)):
    def save():
        from app.notifications.controls import NotificationControl, control_status

        if not body.confirm:
            raise ValueError("Explicit operator confirmation required")
        row = db.get(NotificationControl, 1)
        if not row:
            row = NotificationControl(id=1)
            db.add(row)
        row.paused = body.paused
        row.updated_by = user.id
        row.updated_at = clock()
        db.flush()
        audit(db, user.id, "pause_changed", paused=body.paused)
        return control_status(db)

    return tx(db, save)


@router.post("/emergency-disable")
def emergency(db=Depends(get_db), user=Depends(require_admin)):
    from app.notifications.controls import emergency_disable, control_status

    try:
        emergency_disable()
    except (OSError, ValueError):
        raise HTTPException(
            503,
            "Unable to persist notification disable; stop the worker through the approved procedure",
        ) from None
    try:
        audit(db, user.id, "emergency_disabled")
        db.commit()
        return control_status(db)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            503,
            "Notification disable is latched; storage audit unavailable. Do not remove the latch.",
        ) from None


class ProviderConfiguration(SafeModel):
    account_reference: str | None = Field(None, pattern=r"^AC[a-fA-F0-9]{32}$")
    adapter: Literal["mock", "twilio_mock", "slack_webhook_mock", "slack_api_mock"] = (
        "mock"
    )
    sender: str | None = Field(None, max_length=80)
    registration_reference: str | None = Field(
        None, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$"
    )
    callback_secret: SecretStr | None = Field(None, max_length=512)


@router.put("/providers/{channel}")
def provider_config(
    channel: Literal["sms", "slack"],
    body: ProviderConfiguration,
    db=Depends(get_db),
    user=Depends(require_admin),
):
    def save():
        from app.notifications.controls import NotificationProviderConfig
        from app.customers.contracts import e164

        if body.adapter == "twilio_mock" and (
            not body.account_reference or not body.sender
        ):
            raise ValueError("SMS contract requires account reference and sender")
        if body.adapter not in (
            {"mock", "twilio_mock"}
            if channel == "sms"
            else {"mock", "slack_webhook_mock", "slack_api_mock"}
        ):
            raise ValueError("Adapter does not match channel")
        if channel == "sms" and body.sender and not e164(body.sender):
            raise ValueError(
                "SMS sender requires validated E.164 syntax; native registration remains unverified"
            )
        if channel == "slack" and body.sender:
            import re

            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", body.sender):
                raise ValueError("Bounded Slack channel identifier required")
        row = db.get(NotificationProviderConfig, channel)
        if not row:
            row = NotificationProviderConfig(channel=channel)
            db.add(row)
        row.adapter = body.adapter
        row.sender = body.sender
        row.registration_reference = body.registration_reference
        row.account_reference = body.account_reference
        if body.callback_secret:
            try:
                row.callback_secret_ciphertext = seal(
                    settings.notifications_vault_key_file,
                    body.callback_secret.get_secret_value(),
                )
            except (ValueError, OSError):
                raise ValueError(
                    "Private callback encryption key unavailable"
                ) from None
        row.last_health = "not_tested"
        audit(
            db,
            user.id,
            "provider_configuration_changed",
            channel=channel,
            adapter=body.adapter,
        )
        return {
            "channel": channel,
            "adapter": row.adapter,
            "live_enabled": False,
            "sender_configured": bool(row.sender),
        }

    return tx(db, save)


@router.post("/providers/{channel}/mock-health")
def provider_health(
    channel: Literal["sms", "slack"], db=Depends(get_db), user=Depends(require_admin)
):
    def check():
        from app.notifications.controls import (
            NotificationProviderConfig,
            require_processing,
        )

        require_processing(db)
        if not settings.offline_mode or settings.app_env != "staging":
            raise ValueError("Mock health requires offline staging")
        row = db.get(NotificationProviderConfig, channel)
        if not row:
            raise ValueError("Configure the provider contract first")
        # No connection is established; this is explicitly mock contract health.
        from app.notifications.providers import configured_mock_senders

        sms, slack = configured_mock_senders(db)
        (sms if channel == "sms" else slack).send(
            key="health-" + channel,
            destination="+15555550996" if channel == "sms" else "noc-simulation",
            payload={
                "simulation_only": True,
                "body": "Synthetic contract health check",
            },
        )
        row.last_health = "mock_healthy"
        row.last_health_at = clock()
        audit(db, user.id, "mock_provider_health", channel=channel)
        return {
            "channel": channel,
            "status": row.last_health,
            "at": row.last_health_at,
            "external_calls": 0,
        }

    return tx(db, check)


class MockCallback(SafeModel):
    event_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    channel: Literal["sms", "slack"]
    delivery_id: int = Field(ge=1)
    status: Literal["accepted", "delivered", "failed", "opt_out"]
    timestamp: int = Field(ge=0)
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/mock-callback")
def mock_callback(body: MockCallback, db=Depends(get_db), user=Depends(require_admin)):
    def apply():
        from app.notifications.controls import (
            NotificationProviderConfig,
            NotificationCallback,
        )
        from app.notifications.providers import verified_callback
        from app.notifications.secrets import unseal
        import hashlib, json

        if not settings.offline_mode or settings.app_env != "staging":
            raise ValueError("Only authenticated offline mock callbacks are available")
        config = db.get(NotificationProviderConfig, body.channel)
        if not config or not config.callback_secret_ciphertext:
            raise ValueError("Private mock callback signing secret not configured")
        payload = json.dumps(
            body.model_dump(exclude={"signature"}),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        try:
            secret = unseal(
                settings.notifications_vault_key_file, config.callback_secret_ciphertext
            ).encode()
        except (ValueError, OSError):
            raise ValueError("Private callback verification key unavailable") from None
        if not verified_callback(
            secret, payload, body.signature, body.timestamp, int(clock().timestamp())
        ):
            raise ValueError("Mock callback verification failed")
        digest = hashlib.sha256(payload).hexdigest()
        prior = db.get(NotificationCallback, body.event_id)
        if prior:
            if prior.body_hash != digest:
                raise ValueError("Conflicting callback event identifier")
            return {"duplicate": True, "simulation_only": True}
        delivery = db.get(NotificationDelivery, body.delivery_id)
        if (
            not delivery
            or delivery.channel != body.channel
            or delivery.state != "simulated"
        ):
            raise ValueError(
                "Callback must refer to a completed mock delivery on its channel"
            )
        if body.status != "opt_out":
            if (
                delivery.provider_status in {"delivered", "failed"}
                and delivery.provider_status != body.status
            ):
                raise ValueError("Terminal provider status cannot regress")
            if delivery.provider_status_at and body.timestamp < int(
                delivery.provider_status_at.timestamp()
            ):
                raise ValueError("Stale provider status callback")
            delivery.provider_status = body.status
            delivery.provider_status_at = datetime.fromtimestamp(
                body.timestamp, timezone.utc
            )
        if body.status == "opt_out":
            if body.channel != "sms" or not delivery.recipient_id:
                raise ValueError("Opt-out requires a matching SMS recipient")
            recipient = db.get(NotificationRecipient, delivery.recipient_id)
            account = db.get(CustomerAccount, recipient.customer_id)
            account.sms_opt_out = True
            account.opt_out_at = clock()
            if not db.get(NotificationSuppression, account.id):
                db.add(
                    NotificationSuppression(customer_id=account.id, reason="opt_out")
                )
        db.add(
            NotificationCallback(
                event_id=body.event_id,
                delivery_id=delivery.id,
                status=body.status,
                body_hash=digest,
            )
        )
        audit(
            db,
            user.id,
            "mock_callback_applied",
            plan_id=delivery.plan_id,
            delivery_id=delivery.id,
            status=body.status,
        )
        return {"duplicate": False, "simulation_only": True, "status": body.status}

    return tx(db, apply)
