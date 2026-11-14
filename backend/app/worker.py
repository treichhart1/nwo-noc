"""Independent bounded legacy tasks plus the durable RF scheduler."""

import logging
import time
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
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
    from concurrent.futures import ThreadPoolExecutor
    notification_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='noc-mock-notifications') if settings.notifications_worker_enabled else None
    notification_future = None
    detector_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="noc-down-detector") if settings.down_detector_enabled else None
    detector_future = None
    detector_last = 0.0
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
            if detector_pool and now-detector_last>=settings.down_detector_interval_seconds and (detector_future is None or detector_future.done()):
                if detector_future is not None:
                    try: detector_future.result()
                    except Exception: log.warning("Down Detector interrupted; durable observations retained")
                from app.down_detector.worker import tick as detector_tick
                detector_future=detector_pool.submit(detector_tick)
                detector_last=now
            if notification_pool and (notification_future is None or notification_future.done()):
                if notification_future is not None:
                    try: notification_future.result()
                    except Exception: log.warning('Notification queue interrupted; durable work retained')
                from app.notifications.worker import tick
                notification_future = notification_pool.submit(tick)
            if now - last["tcp"] >= 15 and legacy.submit("tcp", run_due_checks):
                last["tcp"] = now
            if settings.rf_scheduler_enabled:
                try:
                    runner.tick()
                except SQLAlchemyError:
                    runtime.db_failed = True
                    log.warning(
                        "Monitoring database delivery interrupted; durable work retained"
                    )
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
