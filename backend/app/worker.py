import logging
import time
from app.core.config import settings
from app.core.database import SessionLocal
from app.integrations.uisp import sync_uisp
from app.services.monitoring import run_due_checks

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nwo-noc.worker")

def main():
    interval = 15
    last_uisp_sync = 0.0
    while True:
        try:
            with SessionLocal() as db:
                result = run_due_checks(db)
                if result["checked"]:
                    log.info("Monitoring result: %s", result)
        except Exception:
            log.exception("Scheduled monitoring failed")
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
