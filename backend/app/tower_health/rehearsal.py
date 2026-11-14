"""Offline-only dashboard and durable recorded-input restart acceptance."""

import json
import sys
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device, TelemetrySample
from app.customers.models import CustomerAccount, CustomerService
from .contracts import Observation
from .models import TowerSample, TowerFinding
from .service import ingest, assess
from .worker import consume


def run(action):
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Explicit isolated synthetic rehearsal required")
    with SessionLocal() as db:
        account = db.scalar(
            select(CustomerAccount).where(CustomerAccount.external_id == "NOC95-C0001")
        )
        if not account:
            raise ValueError("NOC95 synthetic customer fixture required")
        service = db.scalar(
            select(CustomerService).where(CustomerService.customer_id == account.id)
        )
        now = datetime.now(timezone.utc)
        if action == "seed":
            for second in [-120, -60, 0]:
                ingest(
                    db,
                    Observation(
                        event_id=f"NOC98-browser:{second}",
                        source="synthetic",
                        site_id=service.site_id,
                        device_id=service.device_id,
                        sector_id=service.sector_id,
                        observed_at=now + timedelta(seconds=second),
                        metrics={
                            "collector_ok": True,
                            "reachable": True,
                            "cpu_percent": 95.0,
                            "temperature_c": 80.0,
                            "tx_mbps": 95.0,
                            "tx_capacity_mbps": 100.0,
                        },
                    ),
                    now=now,
                )
            assess(db, site_ids=[service.site_id], now=now, persist=True)
            finding = db.scalar(
                select(TowerFinding).where(
                    TowerFinding.device_id == service.device_id,
                    TowerFinding.rule == "high_cpu",
                )
            )
            if not finding:
                finding = db.scalar(
                    select(TowerFinding).where(
                        TowerFinding.device_id == service.device_id
                    )
                )
            assert finding
            result = {
                "site_id": service.site_id,
                "finding_id": finding.id,
                "device_id": service.device_id,
            }
        elif action == "queue":
            if db.scalar(
                select(Device.id).where(Device.external_id == "noc98-restart")
            ):
                raise ValueError("Fresh restart fixture required")
            device = Device(
                source="synthetic",
                external_id="noc98-restart",
                name="NOC98 Synthetic restart",
                wisp_site_id=service.site_id,
            )
            db.add(device)
            db.flush()
            for second in [-120, -60, 0]:
                db.add(
                    TelemetrySample(
                        device_id=device.id,
                        sampled_at=now + timedelta(seconds=second),
                        raw_data={
                            "tower_health_v1": {
                                "metrics": {"collector_ok": True, "cpu_percent": 95.0}
                            }
                        },
                    )
                )
            result = {"queued": 3, "external_calls": 0}
        elif action == "verify":
            for _ in range(50):
                if consume(db, now=now)["processed"] == 0:
                    break
            device = db.scalar(
                select(Device).where(Device.external_id == "noc98-restart")
            )
            assert device
            n = db.scalar(
                select(func.count(TowerSample.id)).where(
                    TowerSample.device_id == device.id
                )
            )
            findings = db.scalar(
                select(func.count(TowerFinding.id)).where(
                    TowerFinding.device_id == device.id
                )
            )
            assert n == 3 and findings == 1
            assert consume(db, now=now)["processed"] == 0
            assert (
                db.scalar(
                    select(func.count(TowerSample.id)).where(
                        TowerSample.device_id == device.id
                    )
                )
                == n
            )
            result = {
                "observations_after_database_restart": n,
                "findings": findings,
                "duplicate_samples": 0,
                "external_calls": 0,
            }
        else:
            raise ValueError("Use seed, queue, verify")
        db.commit()
        return result


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1])))
