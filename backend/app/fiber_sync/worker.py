"""Opt-in mock/manual staging scheduler. Never applies topology or contacts either application."""

import time
from datetime import timedelta
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import entities
from app.models.entities import User
from app.field.models import now
from app.equipment.service import lock
from .models import Source
from .adapters import MockAdapter
from . import service as s


def tick(db):
    if not settings.fiber_designer_worker_enabled:
        return {"staged": 0, "external_calls": 0, "automatic_apply": False}
    db.execute(__import__("sqlalchemy").text("SET LOCAL lock_timeout='5s'"))
    db.execute(__import__("sqlalchemy").text("SET LOCAL statement_timeout='30000ms'"))
    lock(db)
    source = db.scalar(
        select(Source)
        .where(
            Source.enabled.is_(True),
            Source.schedule_enabled.is_(True),
            Source.recovery_required.is_(False),
            Source.next_sync_at <= now(),
        )
        .order_by(Source.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not source:
        return {"staged": 0, "external_calls": 0, "automatic_apply": False}
    user = s.get(db, User, source.created_by)
    source.next_sync_at = now() + timedelta(seconds=source.interval_seconds)
    source.last_attempt_at = now()
    try:
        if source.adapter != "mock" or not s.isolated():
            raise s.Conflict("mock_requires_offline_synthetic_staging")
        envelope = MockAdapter(source.fixture).fetch_approved(
            instance_id=source.instance_id,
            project_id=source.project_id,
            after_cursor=source.applied_cursor,
        )
        r = s.preview(
            db,
            user,
            source,
            envelope,
            "Scheduled synthetic export staging; human approval still required",
            origin="scheduled",
        )
        s.event(
            db,
            user,
            "scheduled_preview_only",
            source.id,
            {"run_id": r["id"], "origin": "scheduled", "automatic_apply": False},
        )
        db.commit()
        return {
            "staged": 1,
            "run_id": r["id"],
            "external_calls": 0,
            "automatic_apply": False,
        }
    except (ValueError, LookupError):
        source.last_error_code = "scheduled_export_validation_failed"
        s.event(
            db,
            user,
            "scheduled_fetch_failed",
            source.id,
            {"error_code": source.last_error_code},
        )
        db.commit()
        return {
            "staged": 0,
            "error_code": source.last_error_code,
            "external_calls": 0,
            "automatic_apply": False,
        }


def heartbeat_path():
    from pathlib import Path
    import os

    p = Path(settings.fiber_designer_heartbeat_file)
    if not p.is_absolute() or any(x.is_symlink() for x in [p, *p.parents]):
        raise ValueError("Private heartbeat path required")
    if p.exists() and (p.stat().st_uid != os.getuid() or p.stat().st_mode & 0o077):
        raise ValueError("Private owned heartbeat required")
    return p


if __name__ == "__main__":
    import sys, json

    if "--health" in sys.argv:
        p = heartbeat_path()
        if not p.exists() or time.time() - p.stat().st_mtime > 90:
            raise SystemExit("Fiber staging worker heartbeat missing/stale")
    elif "--once" in sys.argv:
        with SessionLocal() as db:
            print(json.dumps(tick(db)))
    else:
        if not settings.fiber_designer_worker_enabled:
            raise SystemExit("FIBER_DESIGNER_WORKER_ENABLED is false")
        while True:
            heartbeat_path().touch(mode=0o600)
            try:
                with SessionLocal() as db:
                    tick(db)
            except Exception:
                pass  # DB restart: uncommitted stage rolls back; next tick reclaims safely.
            time.sleep(10)
