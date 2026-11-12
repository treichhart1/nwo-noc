"""Explicit, opt-in RF telemetry persistence using existing telemetry_samples.

No device polling or scheduler changes. RF values are stored under a namespaced
raw_data key to avoid destructive schema migrations or unit confusion.
"""
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.models.entities import Device, TelemetrySample
from app.services.rf_metrics import normalize_rf_metrics


def store_rf_sample(
    db: Session,
    device: Device,
    metrics: Mapping[str, Any],
    *,
    sampled_at: datetime | None = None,
) -> TelemetrySample | None:
    """Stage a validated RF sample; the caller owns commit/rollback.

    Returns None for empty or invalid metrics. No implicit commits, polling,
    or changes to device state. A persistent Device ID is required.
    """
    if device.id is None:
        raise ValueError("Cannot store RF metrics for an unsaved device")
    normalized = normalize_rf_metrics(metrics)
    if not normalized:
        return None
    timestamp = sampled_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("sampled_at must be timezone-aware")
    sample = TelemetrySample(
        device_id=device.id,
        sampled_at=timestamp,
        signal_dbm=normalized.get("rssi_dbm"),
        temperature_c=normalized.get("temperature_c"),
        raw_data={"rf_metrics_v1": normalized},
    )
    db.add(sample)
    return sample
