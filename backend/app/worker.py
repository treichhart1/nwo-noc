"""Independent bounded legacy tasks plus the durable RF scheduler."""

import logging
import time
from sqlalchemy import text
from app.core.config import settings
from app.core.database import SessionLocal
from app.integrations.uisp import sync_uisp
from app.integrations.mikrotik import collect_mikrotik
from app.services.monitoring import run_due_checks
from app.services.monitoring_scheduler import Runner
from app.services.worker_runtime import Runtime, LegacyTasks

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nwo-noc.worker")


def main():
    runtime = Runtime()
    runner = Runner(runtime.id)
    legacy = LegacyTasks()
    last = {"tcp": 0.0, "uisp": 0.0, "mikrotik": 0.0}
    while True:
        now = time.monotonic()
        database_ok = False
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
            database_ok = True
            runtime.heartbeat()
        except Exception:
            runtime.db_failed = True
            log.warning("Monitoring database unavailable; durable work retained")
        if database_ok:
            if now - last["tcp"] >= 15 and legacy.submit("tcp", run_due_checks):
                last["tcp"] = now
            if settings.rf_scheduler_enabled:
                try:
                    runner.tick()
                except Exception:
                    log.warning(
                        "Scheduled delivery unavailable; durable leases and outbox retained for recovery"
                    )
            if settings.integrations_admin_approved and not settings.offline_mode:
                if (
                    settings.uisp_enabled
                    and now - last["uisp"]
                    >= max(settings.uisp_sync_interval_seconds, 60)
                    and legacy.submit("uisp", sync_uisp)
                ):
                    last["uisp"] = now
                if (
                    not settings.rf_scheduler_enabled
                    and settings.mikrotik_enabled
                    and now - last["mikrotik"]
                    >= max(settings.mikrotik_sync_interval_seconds, 60)
                    and legacy.submit("mikrotik", collect_mikrotik)
                ):
                    last["mikrotik"] = now
        time.sleep(0.2 if settings.rf_scheduler_enabled else 1)


if __name__ == "__main__":
    main()
