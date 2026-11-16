"""Synthetic40-POP communication benchmark, rollback-only, zero provider/network calls."""

import json, time, resource
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, text
from app.core.database import SessionLocal
from app.models.entities import Site, Device, NetworkLink, Incident, User
from app.customers.models import CustomerAccount, CustomerService
from . import service as s
from .contracts import *


def b(cls, **kw):
    return cls(
        operation_key=uuid4(),
        reason="Synthetic rollback-only customer communication benchmark",
        **kw
    )


def main():
    from app.core.config import settings

    if not (
        settings.app_env == "staging"
        and settings.offline_mode
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError(
            "Explicit offline synthetic staging required for customer benchmark"
        )
    for size in [250, 500, 1000]:
        with SessionLocal() as db:
            user = User(
                username="status-bench-" + uuid4().hex,
                email=uuid4().hex + "@example.invalid",
                role="admin",
                password_hash="unusable",
                is_active=True,
            )
            core = Site(name="Synthetic status core", site_type="core-pop")
            db.add_all([user, core])
            db.flush()
            sites = [Site(name="Synthetic service POP " + str(i)) for i in range(40)]
            db.add_all(sites)
            db.flush()
            devices = [
                Device(
                    source="synthetic",
                    external_id=uuid4().hex,
                    name="Synthetic access point",
                    wisp_site_id=site.id,
                    role="access-point",
                )
                for site in sites
            ]
            db.add_all(devices)
            db.flush()
            for site in sites:
                db.add(
                    NetworkLink(
                        name="Synthetic backhaul " + str(site.id),
                        a_site_id=core.id,
                        z_site_id=site.id,
                        status="active",
                    )
                )
            now = s.clock()
            accounts = [
                CustomerAccount(
                    external_id=uuid4().hex,
                    name="Synthetic customer",
                    phone="+1500555" + str(i).zfill(4),
                    sms_consent="granted",
                    sms_opt_out=False,
                    consent_record="Synthetic fixture consent",
                    consent_recorded_at=now,
                    communication_preference="sms",
                    source_updated_at=now,
                    last_synced_at=now,
                )
                for i in range(size)
            ]
            db.add_all(accounts)
            db.flush()
            for i, a in enumerate(accounts):
                db.add(
                    CustomerService(
                        external_id=uuid4().hex,
                        customer_id=a.id,
                        status="active",
                        service_type="wireless",
                        site_id=sites[i % 40].id,
                        device_id=devices[i % 40].id,
                        mapping_state="verified",
                        source_updated_at=now,
                        last_synced_at=now,
                    )
                )
            incident = Incident(
                title="Synthetic benchmark incident",
                site_id=sites[0].id,
                severity="critical",
                started_at=now - timedelta(hours=1),
            )
            db.add(incident)
            db.flush()
            p = s.get(
                db,
                s.Publication,
                s.create(db, user, b(SourceInput, incident_id=incident.id))["id"],
            )
            elapsed = []
            for _ in range(3):
                start = time.perf_counter()
                result = s.impact_preview(db, p)
                elapsed.append(round((time.perf_counter() - start) * 1000, 2))
            start = time.perf_counter()
            msg = s.prepare_message(db, user, p, b(MessageInput, impact_confirmed=True))
            draft_ms = round((time.perf_counter() - start) * 1000, 2)
            assert len(result["impacts"]) == size
            assert result["eligible_count"] == (size + 39) // 40
            assert not db.scalar(select(s.NotificationDelivery.id))
            print(
                json.dumps(
                    {
                        "pops": 40,
                        "customer_services": size,
                        "eligible_in_selected_tower": result["eligible_count"],
                        "impact_preview_ms": elapsed,
                        "sms_draft_ms": draft_ms,
                        "max_rss_kib": resource.getrusage(
                            resource.RUSAGE_SELF
                        ).ru_maxrss,
                        "transaction": "rolled_back",
                        "provider_calls": 0,
                    }
                ),
                flush=True,
            )
            db.rollback()


if __name__ == "__main__":
    main()
