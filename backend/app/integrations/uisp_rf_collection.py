"""Opt-in, bounded ingestion of RF data already returned by UISP.

This is a pure integration entry point: no API requests, scheduler registration,
or commits. The caller must explicitly invoke it inside a DB transaction.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.uisp_rf import extract_uisp_rf_metrics
from app.models.entities import Device, TelemetrySample
from app.services.rf_persistence import store_rf_sample


def stage_uisp_rf_batch(
    db: Session,
    payload: Sequence[Mapping[str, Any]],
    *,
    sampled_at: datetime | None = None,
    min_interval_seconds: int = 300,
    max_devices: int = 1000,
) -> dict[str, int]:
    """Stage RF telemetry from a previously fetched UISP device list.

    Does not commit. A device must already exist in inventory. At most one
    sample per device is staged per batch; recent persisted samples are skipped.
    """
    if not 60 <= min_interval_seconds <= 86400:
        raise ValueError("min_interval_seconds must be between 60 and 86400")
    if not 1 <= max_devices <= 10000:
        raise ValueError("max_devices must be between 1 and 10000")
    if isinstance(payload, (str, bytes)) or not isinstance(payload, (list, tuple)):
        raise TypeError("UISP payload must be a list or tuple")
    if len(payload) > max_devices:
        raise ValueError("UISP RF batch exceeds configured device limit")
    now = sampled_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("sampled_at must be timezone-aware")

    counts = {"received": len(payload), "staged": 0, "skipped": 0}
    seen: set[str] = set()
    cutoff = now - timedelta(seconds=min_interval_seconds)
    for raw in payload:
        if not isinstance(raw, Mapping):
            counts["skipped"] += 1
            continue
        ident = raw.get("identification")
        if not isinstance(ident, Mapping):
            counts["skipped"] += 1
            continue
        external_id = ident.get("id")
        if not isinstance(external_id, str) or not external_id or external_id in seen:
            counts["skipped"] += 1
            continue
        seen.add(external_id)
        metrics = extract_uisp_rf_metrics(raw)
        if not metrics:
            counts["skipped"] += 1
            continue
        device = db.scalar(select(Device).where(
            Device.source == "uisp", Device.external_id == external_id,
        ))
        if device is None or not device.enabled:
            counts["skipped"] += 1
            continue
        recent = db.scalar(
            select(TelemetrySample.id).where(
                TelemetrySample.device_id == device.id,
                TelemetrySample.sampled_at >= cutoff,
                TelemetrySample.sampled_at <= now,
            ).limit(1)
        )
        if recent is not None:
            counts["skipped"] += 1
            continue
        store_rf_sample(db, device, metrics, sampled_at=now)
        counts["staged"] += 1
    return counts
