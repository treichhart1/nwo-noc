import logging
import time
from app.core.config import settings
from app.core.database import SessionLocal
from app.integrations.uisp import sync_uisp
from app.integrations.mikrotik import collect_mikrotik
from app.services.monitoring import run_due_checks
from redis import Redis
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nwo-noc.worker")

def main():
    interval = 15
    last_uisp_sync = 0.0
    last_mikrotik_sync = 0.0
    last_heartbeat = 0.0
    def heartbeat():
        nonlocal last_heartbeat
        current = time.monotonic()
        if current - last_heartbeat >= 10:
            Redis.from_url(settings.redis_url, socket_timeout=3).set("noc:worker:heartbeat", str(time.time()), ex=60)
            last_heartbeat = current
    while True:
        database_ok = False
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
                heartbeat()
                result = run_due_checks(db, progress=heartbeat)
                database_ok = True
                if result["checked"]:
                    log.info("Monitoring result: %s", result)
        except Exception:
            log.exception("Scheduled monitoring failed")
        try:
            if not database_ok:
                raise RuntimeError("Database checks failed; heartbeat withheld")
            Redis.from_url(settings.redis_url, socket_timeout=3).set("noc:worker:heartbeat", str(time.time()), ex=60)
        except Exception:
            log.exception("Worker heartbeat failed; check Redis connectivity")
        now = time.monotonic()
        if settings.uisp_enabled and settings.integrations_admin_approved and not settings.offline_mode and now-last_uisp_sync>=max(settings.uisp_sync_interval_seconds,60):
            heartbeat()
            last_uisp_sync=now
            try:
                with SessionLocal() as db:log.info("Read-only UISP sync result: %s",sync_uisp(db,progress=heartbeat))
            except Exception:log.warning("Read-only UISP synchronization failed; inspect sanitized integration status")
        if settings.mikrotik_enabled and settings.integrations_admin_approved and not settings.offline_mode and now-last_mikrotik_sync>=max(settings.mikrotik_sync_interval_seconds,60):
            heartbeat()
            last_mikrotik_sync=now
            try:
                with SessionLocal() as db:log.info("Read-only MikroTik collection result: %s",collect_mikrotik(db))
            except Exception:log.warning("Read-only MikroTik collection failed; inspect sanitized integration status")
        time.sleep(interval)

if __name__ == "__main__":
    main()
