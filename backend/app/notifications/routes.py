from datetime import datetime, timedelta, timezone
from typing import Literal
import json
import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import Field, SecretStr
from sqlalchemy import select, func
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
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=100),
    db=Depends(get_db),
    user=Depends(customer_operator),
):
    def load():
        p = policy(db)
        plans = list(
            db.scalars(
                select(NotificationPlan)
                .order_by(NotificationPlan.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        audit(db, user.id, "accessed", operation="dashboard", returned=len(plans))
        return {
            "items": [view(x) for x in plans],
            "total": db.scalar(select(func.count(NotificationPlan.id))),
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
                for k in KINDS
            ],
            "maintenance": [
                {
                    "id": m.id,
                    "title": m.title,
                    "target": m.target,
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
        target_for(db, target=Target(**target))
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
