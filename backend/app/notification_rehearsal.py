"""Explicit synthetic customer-backed notification and durable restart acceptance."""

import json, sys
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Incident, User
from app.customers.models import CustomerAccount, CustomerService
from app.notifications.models import (
    NotificationPlan,
    NotificationPolicy,
    NotificationMaintenance,
    NotificationDelivery,
)
from app.notifications.contracts import Policy, PlanRequest, Evidence
from app.notifications.service import create_plan, approve


def run(action):
    if (
        not settings.offline_mode
        or settings.app_env != "staging"
        or not settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Explicit synthetic offline staging required")
    with SessionLocal() as db:
        accounts = list(db.scalars(select(CustomerAccount)))
        if len(accounts) != 200 or any(
            not a.external_id.startswith("NOC95-C") for a in accounts
        ):
            raise ValueError("Expected isolated NOC-95 synthetic customer fixture")
        a = next(a for a in accounts if a.external_id == "NOC95-C0001")
        s = db.scalar(
            select(CustomerService).where(CustomerService.customer_id == a.id)
        )
        user = db.scalar(
            select(User).where(User.username == "acceptance", User.role == "admin")
        )
        if not user or not s.site_id or s.mapping_state != "verified":
            raise ValueError("Reviewed synthetic service and administrator required")
        now = datetime.now(timezone.utc)
        if action == "seed":
            if db.scalar(select(NotificationPlan.id)):
                raise ValueError("Fresh notification fixture required")
            a.sms_consent = "granted"
            a.sms_opt_out = False
            a.communication_preference = "sms"
            a.consent_record = "SYN-NOC96-CONSENT"
            db.add(
                NotificationPolicy(
                    id=1,
                    config=Policy(
                        timezone="UTC",
                        quiet_start=(now.hour + 12) % 24,
                        quiet_end=(now.hour + 13) % 24,
                        sms_price_microunits=12500,
                    ).model_dump(),
                )
            )
            incident = Incident(
                title="NOC96 Synthetic tower power interruption",
                severity="warning",
                site_id=s.site_id,
                started_at=now - timedelta(hours=1),
            )
            db.add(incident)
            db.flush()
            plan = create_plan(
                db,
                PlanRequest(
                    incident_id=incident.id,
                    evidence=Evidence(
                        reference="SYN-NOC96", event_type="tower_power", confirmed=True
                    ),
                ),
                user.id,
            )
            result = {
                "incident_id": incident.id,
                "plan_id": plan.id,
                "site_id": s.site_id,
                "simulation_only": True,
                "external_calls": 0,
            }
            db.commit()
            return result
        if action == "queue":
            m = NotificationMaintenance(
                title="NOC96 Synthetic queue restart maintenance",
                target={"site_id": s.site_id},
                starts_at=now + timedelta(hours=1),
                ends_at=now + timedelta(hours=2),
                created_by=user.id,
            )
            db.add(m)
            db.flush()
            p = create_plan(
                db,
                PlanRequest(
                    maintenance_id=m.id,
                    kind="maintenance",
                    evidence=Evidence(
                        reference="SYN-RESTART", event_type="planned_maintenance"
                    ),
                ),
                user.id,
            )
            approve(db, p.id, user.id)
            db.commit()
            return {
                "queued_plan_id": p.id,
                "pending": db.scalar(
                    select(func.count(NotificationDelivery.id)).where(
                        NotificationDelivery.state == "queued"
                    )
                ),
                "simulation_only": True,
            }
        if action == "verify":
            pending = db.scalar(
                select(func.count(NotificationDelivery.id)).where(
                    NotificationDelivery.state.in_(["queued", "retry"])
                )
            )
            sms = list(
                db.scalars(
                    select(NotificationDelivery).where(
                        NotificationDelivery.channel == "sms"
                    )
                )
            )
            assert (
                pending == 0
                and len(sms) >= 2
                and all(d.state == "simulated" and d.attempts == 1 for d in sms)
            )
            assert len({d.provider_receipt for d in sms}) == len(sms)
            return {
                "pending": pending,
                "sms_simulated": len(sms),
                "unique_receipts": len(sms),
                "duplicate_attempts": 0,
                "simulation_only": True,
                "external_calls": 0,
            }
        raise ValueError("Use seed, queue or verify")


if __name__ == "__main__":
    try:
        print(json.dumps(run(sys.argv[1])))
    except Exception:
        raise SystemExit(
            "Synthetic notification rehearsal failed; preserve isolated queues/state and inspect sanitized acceptance results"
        ) from None
