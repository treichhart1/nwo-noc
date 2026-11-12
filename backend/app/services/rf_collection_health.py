"""Read-only RF collection health summary with no credentials or raw payloads."""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Device, Integration, TelemetrySample, RFCollectionRun
from app.services.rf_freshness import evaluate_rf_freshness


def get_rf_collection_health(db: Session, *, now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    integration = db.scalar(select(Integration).where(Integration.name == "uisp"))
    device_count = db.scalar(select(func.count()).select_from(Device).where(
        Device.source == "uisp", Device.enabled.is_(True),
    )) or 0
    # Per-device indexed lookup avoids grouping the entire telemetry history.\n    # Only timestamps are returned; no raw payloads or secrets leave the DB.\n    latest_rf_at = (\n        select(TelemetrySample.sampled_at)\n        .where(\n            TelemetrySample.device_id == Device.id,\n            TelemetrySample.raw_data["rf_metrics_v1"].is_not(None),\n        )\n        .order_by(TelemetrySample.sampled_at.desc(), TelemetrySample.id.desc())\n        .limit(1)\n        .correlate(Device)\n        .scalar_subquery()\n    )\n    rows = db.execute(\n        select(Device.id, latest_rf_at)\n        .where(Device.source == "uisp", Device.enabled.is_(True))\n    ).all()
    counts = {"fresh": 0, "stale": 0, "future": 0}
    for _, sampled_at in rows:
        if sampled_at is None:
            continue
        # PostgreSQL returns aware UTC timestamps; SQLite may return naive UTC.
        if sampled_at.tzinfo is None:
            sampled_at = sampled_at.replace(tzinfo=timezone.utc)
        state = evaluate_rf_freshness(sampled_at, now=current)["status"]
        counts[state] += 1
    last_run = db.scalar(select(RFCollectionRun).order_by(
        RFCollectionRun.started_at.desc(), RFCollectionRun.id.desc(),
    ).limit(1))
    last_counts = ({
        "received": last_run.received,
        "staged": last_run.staged,
        "skipped": last_run.skipped,
    } if last_run and last_run.status == "success" else None)
    return {
        "enabled": settings.rf_collection_enabled,
        "uisp_status": integration.status if integration else "not_configured",
        "last_uisp_sync_at": integration.last_sync_at.isoformat() if integration and integration.last_sync_at else None,
        "enabled_uisp_devices": device_count,
        "rf_devices_fresh": counts["fresh"],
        "rf_devices_stale": counts["stale"],
        "rf_devices_future": counts["future"],
        "rf_devices_missing": max(0, device_count - sum(counts.values())),
        "last_batch_counts": last_counts,
        "last_batch_status": last_run.status if last_run else None,
        "last_batch_at": last_run.started_at.isoformat() if last_run else None,
    }
