"""Read-only RF diagnostics composed from persisted, explicitly tagged samples.

No implicit polling, API exposure, incident creation, or writes.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Device, TelemetrySample
from app.services.rf_degradation import RFThresholds, evaluate_rf_degradation
from app.services.rf_trends import RFTrendConfig, analyze_rf_trends


def get_rf_diagnostics(
    db: Session,
    device_id: int,
    *,
    now: datetime | None = None,
    lookback_hours: int = 24,
    max_samples: int = 288,
    thresholds: RFThresholds | None = None,
    trend_config: RFTrendConfig | None = None,
) -> dict:
    """Return diagnostics for a single device using a bounded history query."""
    if not isinstance(device_id, int) or isinstance(device_id, bool) or device_id < 1:
        raise ValueError("device_id must be a positive integer")
    if not 1 <= lookback_hours <= 168:
        raise ValueError("lookback_hours must be between 1 and 168")
    if not 6 <= max_samples <= 1000:
        raise ValueError("max_samples must be between 6 and 1000")
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    device = db.get(Device, device_id)
    if device is None:
        raise LookupError("device not found")
    rows = db.scalars(
        select(TelemetrySample)
        .where(
            TelemetrySample.device_id == device_id,
            TelemetrySample.sampled_at >= timestamp - timedelta(hours=lookback_hours),
            TelemetrySample.sampled_at <= timestamp,
        )
        .order_by(TelemetrySample.sampled_at.desc(), TelemetrySample.id.desc())
        .limit(max_samples)
    ).all()
    history = []
    for row in reversed(rows):
        raw = row.raw_data if isinstance(row.raw_data, dict) else {}
        metrics = raw.get("rf_metrics_v1")
        if not isinstance(metrics, dict):
            continue
        history.append({"sampled_at": row.sampled_at, "metrics": metrics})

    # Samples at the same timestamp are possible during retries. Keep the
    # last persisted sample per timestamp to satisfy strict chronological order.
    unique = {}
    for sample in history:
        unique[sample["sampled_at"]] = sample
    history = [unique[key] for key in sorted(unique)]
    degradation = evaluate_rf_degradation(
        [sample["metrics"] for sample in history], thresholds
    )
    trends = analyze_rf_trends(history, trend_config)
    return {
        "device_id": device_id,
        "device_name": device.name,
        "lookback_hours": lookback_hours,
        "rf_sample_count": len(history),
        "degradation": degradation,
        "trends": trends,
    }
