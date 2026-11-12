"""Bounded RF collection reliability statistics and opt-in history pruning."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.entities import RFCollectionRun


def collection_reliability(db: Session, *, days: int = 7, now: datetime | None = None) -> dict:
    if not 1 <= days <= 30:
        raise ValueError("days must be between 1 and 30")
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=days)
    rows = db.execute(
        select(RFCollectionRun.status, func.count(RFCollectionRun.id))
        .where(RFCollectionRun.started_at >= cutoff, RFCollectionRun.started_at <= current)
        .group_by(RFCollectionRun.status)
    ).all()
    counts = dict(rows)
    successful = counts.get("success", 0)
    failed = counts.get("failed", 0)
    total = successful + failed
    return {
        "window_days": days,
        "total_runs": total,
        "successful_runs": successful,
        "failed_runs": failed,
        "success_rate_percent": round(successful * 100 / total, 1) if total else None,
    }


def prune_rf_collection_history(
    db: Session, *, retention_days: int = 30, max_delete: int = 500,
    now: datetime | None = None,
) -> int:
    """Stage deletion of at most max_delete old rows; caller controls commit."""
    if not 7 <= retention_days <= 365:
        raise ValueError("retention_days must be between 7 and 365")
    if not 1 <= max_delete <= 1000:
        raise ValueError("max_delete must be between 1 and 1000")
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=retention_days)
    ids = db.scalars(
        select(RFCollectionRun.id)
        .where(RFCollectionRun.started_at < cutoff)
        .order_by(RFCollectionRun.started_at, RFCollectionRun.id)
        .limit(max_delete)
    ).all()
    if ids:
        db.execute(delete(RFCollectionRun).where(RFCollectionRun.id.in_(ids)))
    return len(ids)
