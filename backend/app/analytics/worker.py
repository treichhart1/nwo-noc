"""Opt-in recorded-data aggregation, not a device collector."""

import json, time
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.config import settings
from app.models.entities import User
from . import service as s
from .contracts import RollupInput


def tick(db):
    if not settings.analytics_rollup_enabled:
        return {"disabled": True, "device_polls": 0}
    db.execute(__import__("sqlalchemy").text("SET LOCAL statement_timeout='15000ms'"))
    u = db.scalar(
        select(User)
        .where(User.role == "admin", User.is_active.is_(True))
        .order_by(User.id)
        .limit(1)
    )
    if not u:
        raise ValueError(
            "Active administrator required for recorded-data projection audit"
        )
    end = s.now().replace(minute=0, second=0, microsecond=0)
    result = s.rollup(
        db,
        u,
        RollupInput(
            operation_key=uuid4(),
            reason="Opt-in closed-hour recorded-data aggregation; no network polling",
            start=end - timedelta(hours=2),
            end=end,
        ),
    )
    s.event(db, u, "scheduled_rollup", 1, {"rows": result["rows"], "device_polls": 0})
    db.commit()
    return result


if __name__ == "__main__":
    import sys

    if "--once" in sys.argv:
        with SessionLocal() as db:
            print(json.dumps(tick(db)))
    else:
        if not settings.analytics_rollup_enabled:
            raise SystemExit("ANALYTICS_ROLLUP_ENABLED is false")
        while True:
            try:
                with SessionLocal() as db:
                    tick(db)
            except Exception:
                pass  # Uncommitted aggregate work rolls back on storage interruption.
            time.sleep(300)
