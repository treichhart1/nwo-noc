import logging
import time
from app.core.config import settings
from app.core.database import SessionLocal
from app.integrations.uisp import sync_uisp
from app.services.monitoring import run_due_checks
from redis import Redis
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nwo-noc.worker")

def main():
    interval = 15
    last_uisp_sync = 0.0
    while True:
        database_ok = False
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
                result = run_due_checks(db)
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
        if settings.uisp_base_url and settings.uisp_token and now - last_uisp_sync >= max(settings.uisp_sync_interval_seconds, 60):
            try:
                with SessionLocal() as db:
                    log.info("Scheduled sync result: %s", sync_uisp(db)); last_uisp_sync = now
            except Exception:
                log.exception("Scheduled UISP sync failed")
        time.sleep(interval)

if __name__ == "__main__":
    main()
