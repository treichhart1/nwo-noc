import logging, time
from app.core.config import settings
from app.core.database import SessionLocal
from app.integrations.uisp import sync_uisp
from app.integrations.mikrotik import collect_mikrotik
from app.services.monitoring import run_due_checks
from app.services.monitoring_scheduler import Runner
from app.services.worker_runtime import Runtime
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nwo-noc.worker")


def main():
    runtime = Runtime()
    runner = Runner(runtime.id)
    last_tcp = last_uisp = last_mikrotik = 0.0
    while True:
        now = time.monotonic()
        database_ok = False
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
                database_ok = True
                if now - last_tcp >= 15:
                    last_tcp = now
                    result = run_due_checks(db, progress=runtime.heartbeat)
                    if result["checked"]:
                        log.info("Monitoring batch checked=%s", result["checked"])
            runtime.heartbeat()
        except Exception:
            runtime.db_failed = True
            log.warning(
                "Monitoring database/batch unavailable; retrying without publishing a healthy probe"
            )
        if database_ok and settings.rf_scheduler_enabled:
            try:
                runner.tick()
            except Exception:
                log.warning(
                    "Scheduled delivery unavailable; durable leases and outbox retained for recovery"
                )
        if (
            database_ok
            and settings.uisp_enabled
            and settings.integrations_admin_approved
            and not settings.offline_mode
            and now - last_uisp >= max(settings.uisp_sync_interval_seconds, 60)
        ):
            last_uisp = now
            try:
                with SessionLocal() as db:
                    sync_uisp(db, progress=runtime.heartbeat)
            except Exception:
                log.warning(
                    "Read-only UISP synchronization failed; inspect sanitized integration status"
                )
        if (
            database_ok
            and not settings.rf_scheduler_enabled
            and settings.mikrotik_enabled
            and settings.integrations_admin_approved
            and not settings.offline_mode
            and now - last_mikrotik >= max(settings.mikrotik_sync_interval_seconds, 60)
        ):
            last_mikrotik = now
            try:
                with SessionLocal() as db:
                    collect_mikrotik(db)
            except Exception:
                log.warning(
                    "Read-only MikroTik collection failed; inspect sanitized integration status"
                )
        time.sleep(0.2 if settings.rf_scheduler_enabled else 15)


if __name__ == "__main__":
    main()
