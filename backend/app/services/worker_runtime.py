"""Redis loss degrades health but cannot abort a PostgreSQL monitoring batch."""

import os, time, uuid
from pathlib import Path
from datetime import datetime, timezone
from redis import Redis
from sqlalchemy import text
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import MonitoringWorker, AuditLog
from app.services.monitoring_spool import count

ID_FILE = "/tmp/nwo-noc-worker-id"


class Runtime:
    def __init__(
        self, *, factory=SessionLocal, redis_factory=None, worker_id=None, write_id=True
    ):
        self.id = worker_id or str(uuid.uuid4())
        self.factory = factory
        self.redis_factory = redis_factory or (
            lambda: Redis.from_url(
                settings.redis_url, socket_connect_timeout=2, socket_timeout=2
            )
        )
        self.last = 0.0
        self.db_failed = False
        if write_id:
            temp = Path(ID_FILE + "." + self.id)
            temp.write_text(self.id)
            temp.chmod(0o600)
            os.replace(temp, ID_FILE)

    def heartbeat(self, phase="monitoring", force=False):
        mono = time.monotonic()
        if not force and mono - self.last < 10:
            return
        self.last = mono
        now = datetime.now(timezone.utc)
        try:
            with self.factory() as db:
                db.execute(text("SELECT 1"))
                row = db.get(MonitoringWorker, self.id)
                if row is None:
                    row = MonitoringWorker(id=self.id, started_at=now)
                    db.add(row)
                    db.add(
                        AuditLog(
                            action="monitoring.worker_started",
                            entity_type="worker",
                            entity_id=self.id,
                            details={"execution_enabled": False},
                        )
                    )
                old = row.redis_state
                try:
                    client = self.redis_factory()
                    client.set(
                        "noc:worker:" + self.id + ":heartbeat", str(time.time()), ex=60
                    )
                    client.set("noc:worker:heartbeat", str(time.time()), ex=60)
                    state = "available"
                except Exception:
                    state = "unavailable"
                if old == "unavailable" and state == "available":
                    db.add(
                        AuditLog(
                            action="monitoring.redis_recovered",
                            entity_type="worker",
                            entity_id=self.id,
                            details={},
                        )
                    )
                if self.db_failed:
                    db.add(
                        AuditLog(
                            action="monitoring.database_recovered",
                            entity_type="worker",
                            entity_id=self.id,
                            details={},
                        )
                    )
                row.redis_state = state
                row.heartbeat_at = now
                row.phase = phase
                if settings.rf_scheduler_enabled:
                    try:
                        row.spool_pending = count(settings.rf_scheduler_spool_dir)
                    except (OSError, ValueError):
                        row.phase = "spool_unavailable"
                db.commit()
                self.db_failed = False
        except Exception:
            self.db_failed = (
                True  # No heartbeat is published when the database probe fails.
            )
