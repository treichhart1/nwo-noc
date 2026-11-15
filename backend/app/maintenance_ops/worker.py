from app.core.database import SessionLocal
from app.core.config import settings
from .service import generate, reminders


def tick():
    if not settings.maintenance_scheduler_enabled:
        return {"enabled": False}
    with SessionLocal() as db:
        result = generate(db)
        result["reminders"] = reminders(db)
        db.commit()
        return result


class MaintenanceRunner:
    """One bounded maintenance task; independent of collector names/heartbeats."""

    def __init__(self, enabled, pool=None):
        from concurrent.futures import ThreadPoolExecutor

        self.enabled = enabled
        self.pool = (
            (
                pool
                or ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="noc-maintenance"
                )
            )
            if enabled
            else None
        )
        self.pending = None
        self.last = float("-inf")

    def submit_due(self, instant):
        if (
            not self.enabled
            or instant - self.last < 60
            or (self.pending is not None and not self.pending.done())
        ):
            return False
        if self.pending is not None:
            try:
                self.pending.result()
            except Exception:
                import logging

                logging.getLogger("nwo-noc.worker").warning(
                    "Maintenance interrupted; transactional occurrences and documents retained"
                )
        self.pending = self.pool.submit(tick)
        self.last = instant
        return True
