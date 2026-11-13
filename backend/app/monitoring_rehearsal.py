"""Dedicated offline synthetic job used by isolated service-recovery acceptance."""

import json, sys
from datetime import datetime, timezone
from sqlalchemy import select, func
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import (
    Device,
    MonitoringJob,
    MonitoringReceipt,
    MonitoringWorker,
    AuditLog,
    MonitoringAlert,
    TelemetrySample,
)
from app.services.monitoring_scheduler import enroll


def main(mode):
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
        and settings.rf_scheduler_enabled
    ):
        raise ValueError("Explicit offline synthetic scheduler required")
    with SessionLocal() as db:
        d = db.scalar(
            select(Device).where(
                Device.source == "synthetic",
                Device.external_id == "noc92-service-recovery",
            )
        )
        if mode == "seed":
            if d:
                raise ValueError("Rehearsal already seeded; refusing to overwrite")
            d = Device(
                source="synthetic",
                external_id="noc92-service-recovery",
                name="NOC92 Synthetic recovery radio",
                vendor="Cambium",
                enabled=True,
            )
            db.add(d)
            db.flush()
            enroll(
                db,
                {
                    "device_id": d.id,
                    "interval_seconds": 15,
                    "timeout_seconds": 1,
                    "lease_seconds": 15,
                },
                now=datetime.now(timezone.utc),
            )
            db.commit()
        if mode not in {"seed", "status", "weak", "normal"} or not d:
            raise ValueError("Seed dedicated synthetic job first")
        j = db.get(MonitoringJob, f"synthetic:{d.id}")
        if mode in {"weak", "normal"}:
            j.fixture = {"mode": mode, "latency_ms": 0}
            db.add(
                AuditLog(
                    action="monitoring.synthetic_scenario_changed",
                    entity_type="monitoring_job",
                    entity_id=j.key,
                    details={"mode": mode, "synthetic": True},
                )
            )
            db.commit()
        signal = db.scalar(
            select(MonitoringAlert).where(
                MonitoringAlert.device_id == d.id, MonitoringAlert.rule == "low_signal"
            )
        )
        workers = list(
            db.scalars(
                select(MonitoringWorker)
                .order_by(MonitoringWorker.heartbeat_at.desc())
                .limit(5)
            )
        )
        events = {
            action: count
            for action, count in db.execute(
                select(AuditLog.action, func.count(AuditLog.id))
                .where(AuditLog.action.like("monitoring.%"))
                .group_by(AuditLog.action)
            )
        }
        print(
            json.dumps(
                {
                    "job_key": j.key,
                    "rf_signal_alert": signal.state if signal else None,
                    "rf_signal_episodes": signal.episode if signal else 0,
                    "sample_count": db.scalar(
                        select(func.count(TelemetrySample.id)).where(
                            TelemetrySample.device_id == d.id
                        )
                    ),
                    "state": j.state,
                    "last_error": j.last_error,
                    "receipts": db.scalar(
                        select(func.count(MonitoringReceipt.id)).where(
                            MonitoringReceipt.job_key == j.key
                        )
                    ),
                    "workers": [
                        {
                            "id": w.id,
                            "redis_state": w.redis_state,
                            "phase": w.phase,
                            "heartbeat_at": w.heartbeat_at.isoformat(),
                        }
                        for w in workers
                    ],
                    "events": events,
                }
            )
        )


if __name__ == "__main__":
    main(sys.argv[1])
