"""Read-only RF collection health summary with no credentials or raw payloads."""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Device, Integration, TelemetrySample
from app.services.rf_freshness import evaluate_rf_freshness


def get_rf_collection_health(db: Session, *, now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    integration = db.scalar(select(Integration).where(Integration.name == "uisp"))
    device_count = db.scalar(select(func.count()).select_from(Device).where(
        Device.source == "uisp", Device.enabled.is_(True),
    )) or 0
    # Aggregate latest explicitly tagged RF samples, never raw UISP device payloads.
    rows = db.execute(
        select(TelemetrySample.device_id, func.max(TelemetrySample.sampled_at))
        .join(Device, Device.id == TelemetrySample.device_id)
        .where(Device.source == "uisp", Device.enabled.is_(True),
               TelemetrySample.raw_data["rf_metrics_v1"].is_not(None))
        .group_by(TelemetrySample.device_id)
    ).all()
    counts = {"fresh": 0, "stale": 0, "future": 0}
    for _, sampled_at in rows:
        # PostgreSQL returns aware UTC timestamps; SQLite may return naive UTC.
        if sampled_at.tzinfo is None:
            sampled_at = sampled_at.replace(tzinfo=timezone.utc)
        state = evaluate_rf_freshness(sampled_at, now=current)["status"]
        counts[state] += 1
    return {
        "enabled": settings.rf_collection_enabled,
        "uisp_status": integration.status if integration else "not_configured",
        "last_uisp_sync_at": integration.last_sync_at.isoformat() if integration and integration.last_sync_at else None,
        "enabled_uisp_devices": device_count,
        "rf_devices_fresh": counts["fresh"],
        "rf_devices_stale": counts["stale"],
        "rf_devices_future": counts["future"],
        "rf_devices_missing": max(0, device_count - sum(counts.values())),
        "last_batch_counts": None,
        "last_batch_note": "Per-batch staged/skipped counts are logged; historical batch totals are not persisted.",
    }
