"""Guarded synthetic financial acceptance reusing canonical upstream sites/services."""

import json
from datetime import date
from uuid import uuid4
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.config import settings
from app.core.security import hash_password
from app.models.entities import User, Site
from app.customers.models import CustomerService
from app.field.models import now
from . import service as s
from .models import Period, Scope
from .contracts import *

R = "Synthetic financial acceptance fixture; no billing writes or external calls"


def body(cls, **kw):
    return cls(operation_key=uuid4(), reason=R, **kw)


def seed():
    if not (
        settings.app_env == "staging"
        and settings.offline_mode
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Offline synthetic staging required")
    with SessionLocal() as db:
        if db.scalar(select(Period.id)):
            raise ValueError("Empty isolated financial fixture required")
        if not db.scalar(select(User.id).where(User.username == "acceptance")):
            raise ValueError("Existing upstream acceptance fixture required")
        actor = User(
            username="finance-acceptance",
            email="finance-acceptance@example.invalid",
            password_hash=hash_password(settings.admin_password),
            role="admin",
            is_active=True,
        )
        db.add(actor)
        db.flush()
        service = db.scalar(
            select(CustomerService)
            .where(
                CustomerService.status == "active",
                CustomerService.mapping_state == "verified",
                CustomerService.site_id.is_not(None),
                CustomerService.source_deleted_at.is_(None),
            )
            .order_by(CustomerService.id)
            .limit(1)
        )
        if not service:
            raise ValueError("Existing reviewed synthetic Powercode service required")
        p = s.create_period(
            db,
            actor,
            body(
                PeriodInput,
                code="SYN-2026-01",
                starts_on=date(2026, 1, 1),
                ends_on=date(2026, 2, 1),
                currency="USD",
            ),
        )
        scopes = {}
        for code, kind, id in [
            ("network", "network", None),
            ("tower", "tower", service.site_id),
            ("service", "service", service.id),
        ]:
            scopes[code] = s.create_scope(
                db,
                actor,
                body(
                    ScopeInput,
                    code="SYN-" + code,
                    name="Synthetic financial " + code,
                    kind=kind,
                    reference_id=id,
                ),
            )["id"]
        for key, kind, cat, basis, amount, scope in [
            (
                "monthly-service",
                "revenue",
                "subscription",
                "contracted",
                "1000.00",
                "service",
            ),
            ("power", "opex", "power", "actual", "100.00", "tower"),
            ("lease", "opex", "lease", "actual", "200.00", "tower"),
            ("investment", "capex", "equipment", "actual", "5000.00", "tower"),
        ]:
            s.add_entry(
                db,
                actor,
                p["id"],
                body(
                    EntryInput,
                    scope_id=scopes[scope],
                    kind=kind,
                    category=cat,
                    basis=basis,
                    amount=amount,
                    external_key=key,
                    evidence=R,
                ),
            )
        scenario = s.scenario(
            db,
            actor,
            p["id"],
            body(
                ScenarioInput,
                scope_id=scopes["tower"],
                name="Synthetic upgrade comparison",
                monthly_revenue_delta="100",
                monthly_opex_delta="20",
                capex="800",
                additional_customers=2,
                evidence=R,
            ),
        )
        db.commit()
        return {
            "username": actor.username,
            "period_id": p["id"],
            "tower_scope_id": scopes["tower"],
            "service_scope_id": scopes["service"],
            "network_scope_id": scopes["network"],
            "service_external_id": service.external_id,
            "scenario_id": scenario["id"],
            "external_calls": 0,
            "billing_writes": False,
            "initial_complete": False,
        }


if __name__ == "__main__":
    print(json.dumps(seed()))
