"""Durable mock queue worker. Claims/receipts are atomic PostgreSQL transactions.

Actual providers remain unavailable: real external send ambiguity would require
provider idempotency/status reconciliation, not an exactly-once assertion.
"""

from datetime import timedelta
from sqlalchemy import select, func, or_, case
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import User
from app.customers.models import CustomerAccount
from app.customers.contracts import e164
from app.notifications.models import (
    NotificationPlan,
    NotificationRecipient,
    NotificationDelivery,
    NotificationAttempt,
    NotificationSuppression,
)
from app.notifications.providers import MockSender, MockSlack, DeliveryFailure
from app.notifications.service import (
    lock,
    clock,
    utc,
    policy,
    snapshot,
    digest,
    contact_hash,
    quiet_due,
    audit,
)
from app.notifications.contracts import PlanRequest


def run_batch(db, *, sms=None, slack=None, now=None, limit=20):
    if not settings.offline_mode or settings.app_env != "staging":
        raise ValueError("Mock notification processing requires offline staging")
    sms = sms or MockSender()
    slack = slack or MockSender()
    if not isinstance(sms, MockSender) or not isinstance(
        slack, (MockSender, MockSlack)
    ):
        raise ValueError("Only mock senders are permitted")
    if not 1 <= limit <= 100:
        raise ValueError("Bounded batch required")
    lock(db)
    now = now or clock()
    p = policy(db)
    processed = 0
    rows = list(
        db.scalars(
            select(NotificationDelivery)
            .where(
                NotificationDelivery.state.in_(["queued", "retry"]),
                NotificationDelivery.next_attempt_at <= now,
            )
            .order_by(
                case(
                    (NotificationDelivery.channel == "slack", 0),
                    (NotificationDelivery.channel == "internal", 1),
                    else_=2,
                ),
                NotificationDelivery.next_attempt_at,
                NotificationDelivery.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for d in rows:
        plan = db.get(NotificationPlan, d.plan_id)
        actor = db.get(User, plan.approved_by) if plan.approved_by else None
        try:
            if (
                plan.status != "approved"
                or not actor
                or not actor.is_active
                or actor.role not in {"operator", "admin"}
            ):
                raise ValueError("approval_invalid")
            current, _ = snapshot(
                db, PlanRequest.model_validate(plan.snapshot["request"]), now
            )
            if digest(current) != plan.fingerprint:
                raise ValueError("source_or_consent_changed")
        except (ValueError, LookupError):
            d.state = "suppressed"
            d.last_error = "approval_or_source_changed"
            d.completed_at = now
            audit(
                db,
                None,
                "delivery_suppressed",
                plan_id=plan.id,
                delivery_id=d.id,
                reason=d.last_error,
            )
            continue
        if d.channel == "sms":
            r = db.get(NotificationRecipient, d.recipient_id)
            account = db.scalar(
                select(CustomerAccount)
                .where(CustomerAccount.id == r.customer_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                not account
                or account.source_deleted_at
                or account.sms_consent != "granted"
                or not account.consent_record
                or account.sms_opt_out
                or account.communication_preference not in {"sms", "both"}
                or not e164(account.phone)
                or contact_hash(account.phone) != r.contact_hash
                or db.get(NotificationSuppression, r.customer_id)
            ):
                d.state = "suppressed"
                d.last_error = "contact_or_suppression_changed"
                d.completed_at = now
                continue
            due = quiet_due(
                now, p, plan.snapshot["request"].get("critical_quiet_bypass", False)
            )
            if due > now:
                d.next_attempt_at = due
                continue
            spent = db.scalar(
                select(
                    func.coalesce(
                        func.sum(NotificationDelivery.budget_charge_microunits), 0
                    )
                ).where(
                    NotificationDelivery.channel == "sms",
                    NotificationDelivery.state == "simulated",
                    NotificationDelivery.completed_at >= now - timedelta(days=1),
                )
            )
            if (
                d.budget_charge_microunits is not None
                and spent + d.budget_charge_microunits > p.daily_budget_microunits
            ):
                d.state = "suppressed"
                d.last_error = "daily_cost_cap"
                d.completed_at = now
                continue
            minute = db.scalar(
                select(func.count(NotificationDelivery.id)).where(
                    NotificationDelivery.channel == "sms",
                    NotificationDelivery.completed_at >= now - timedelta(minutes=1),
                    NotificationDelivery.state == "simulated",
                )
            )
            if minute >= p.max_sms_per_minute:
                d.next_attempt_at = now + timedelta(minutes=1)
                continue
            hour = db.scalar(
                select(func.count(NotificationDelivery.id)).where(
                    NotificationDelivery.channel == "sms",
                    NotificationDelivery.completed_at >= now - timedelta(hours=1),
                    NotificationDelivery.state == "simulated",
                )
            )
            personal = db.scalar(
                select(func.count(NotificationDelivery.id)).where(
                    NotificationDelivery.channel == "sms",
                    NotificationDelivery.recipient_key == d.recipient_key,
                    NotificationDelivery.completed_at >= now - timedelta(days=1),
                    NotificationDelivery.state == "simulated",
                )
            )
            if hour >= p.max_sms_per_hour:
                d.next_attempt_at = now + timedelta(minutes=15)
                continue
            if personal >= p.max_per_customer_day:
                d.state = "suppressed"
                d.last_error = "customer_frequency_cap"
                d.completed_at = now
                continue
            # Brief repeated outage messages across separate correlated incidents
            # are suppressed per customer; restoration/lifecycle notices differ.
            recent = db.scalar(
                select(NotificationDelivery.id)
                .join(NotificationPlan)
                .where(
                    NotificationDelivery.channel == "sms",
                    NotificationDelivery.recipient_key == d.recipient_key,
                    NotificationDelivery.state == "simulated",
                    NotificationDelivery.completed_at >= now - timedelta(minutes=15),
                    NotificationPlan.kind.in_(["outage", "update", "extended"]),
                )
                .limit(1)
            )
            if recent and plan.kind in {"outage", "update", "extended"}:
                d.state = "suppressed"
                d.last_error = "incident_cooldown"
                d.completed_at = now
                continue
            destination = account.phone
            payload = {
                "body": plan.message,
                "category": "operational_service_notice",
                "simulation_only": True,
            }
            provider = sms
        elif d.channel == "slack":
            destination = d.recipient_key
            payload = plan.slack_payload
            provider = slack
        else:
            destination = "internal-dashboard"
            payload = {"plan_id": plan.id, "simulation_only": True}
            provider = MockSender()
        d.attempts += 1
        try:
            receipt = provider.send(
                key=f"noc96:{d.id}", destination=destination, payload=payload
            )
            d.provider_receipt = receipt
            d.state = "simulated"
            d.completed_at = now
            d.last_error = None
            db.add(
                NotificationAttempt(
                    delivery_id=d.id, attempt=d.attempts, outcome="simulated"
                )
            )
            audit(
                db,
                None,
                "simulated_delivery",
                plan_id=plan.id,
                delivery_id=d.id,
                channel=d.channel,
                attempt=d.attempts,
            )
        except DeliveryFailure as error:
            d.last_error = error.code
            d.state = (
                "dead_letter"
                if error.permanent or d.attempts >= p.max_attempts
                else "retry"
            )
            d.next_attempt_at = now + timedelta(
                seconds=min(300, 5 * 2 ** (d.attempts - 1))
            )
            if d.state == "dead_letter":
                d.completed_at = now
            db.add(
                NotificationAttempt(
                    delivery_id=d.id,
                    attempt=d.attempts,
                    outcome=d.state + ":" + error.code,
                )
            )
            audit(
                db,
                None,
                "delivery_failed",
                plan_id=plan.id,
                delivery_id=d.id,
                error_code=error.code,
                state=d.state,
            )
        except Exception:
            d.last_error = "provider_unavailable"
            d.state = "dead_letter" if d.attempts >= p.max_attempts else "retry"
            d.next_attempt_at = now + timedelta(
                seconds=min(300, 5 * 2 ** (d.attempts - 1))
            )
            if d.state == "dead_letter":
                d.completed_at = now
            db.add(
                NotificationAttempt(
                    delivery_id=d.id,
                    attempt=d.attempts,
                    outcome=d.state + ":provider_unavailable",
                )
            )
            audit(
                db,
                None,
                "delivery_failed",
                plan_id=plan.id,
                delivery_id=d.id,
                error_code="provider_unavailable",
            )
        processed += 1
        db.flush()
    for ident in {r.plan_id for r in rows}:
        if not db.scalar(
            select(NotificationDelivery.id)
            .where(
                NotificationDelivery.plan_id == ident,
                NotificationDelivery.state.in_(["queued", "retry"]),
            )
            .limit(1)
        ):
            plan = db.get(NotificationPlan, ident)
            if plan.status == "approved":
                plan.status = "complete"
    db.flush()
    return {
        "processed": processed,
        "simulation_only": True,
        "external_delivery_enabled": False,
    }


def discover(db, now=None):
    from app.models.entities import Incident
    from app.notifications.service import create_plan, digest
    from app.notifications.contracts import Evidence

    now = now or clock()
    actor = db.scalar(
        select(User)
        .where(User.role == "admin", User.is_active.is_(True))
        .order_by(User.id)
        .limit(1)
    )
    if not actor:
        return 0
    created = 0
    for i in db.scalars(
        select(Incident)
        .where(
            Incident.status.not_in(["resolved", "closed"]),
            Incident.id.not_in(
                select(NotificationPlan.incident_id).where(
                    NotificationPlan.incident_id.is_not(None),
                    NotificationPlan.kind == "outage",
                )
            ),
        )
        .order_by(Incident.id)
        .limit(20)
    ):
        key = digest([f"i:{i.id}", "outage", 0])
        if db.scalar(
            select(NotificationPlan.id).where(NotificationPlan.dedupe_key == key)
        ):
            continue
        try:
            create_plan(
                db,
                PlanRequest(
                    incident_id=i.id,
                    evidence=Evidence(
                        reference=f"AUTO-INCIDENT-{i.id}",
                        event_type=(
                            "backhaul_outage" if i.link_id else "equipment_failure"
                        ),
                    ),
                ),
                actor.id,
                now,
            )
            created += 1
        except ValueError:
            break
    return created


def tick():
    with SessionLocal() as db:
        if settings.notifications_auto_drafts_enabled:
            discover(db)
        result = run_batch(db)
        db.commit()
        return result


if __name__ == "__main__":
    if not settings.notifications_worker_enabled:
        raise SystemExit(
            "Mock notification worker disabled; enable only in isolated offline staging"
        )
    import json

    print(json.dumps(tick()))
