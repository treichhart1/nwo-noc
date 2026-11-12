"""Bounded, read-only RF summaries grouped by assigned NOC site."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.entities import Device, Site, TelemetrySample
from app.services.rf_degradation import evaluate_rf_degradation
from app.services.rf_freshness import evaluate_rf_freshness


def get_rf_site_health(db: Session, *, max_devices: int = 250) -> dict:
    """Aggregate RF samples in bulk; never infer network outages."""
    if not 1 <= max_devices <= 250:
        raise ValueError("max_devices out of range")
    now = datetime.now(timezone.utc)
    sites = db.execute(select(Site.id, Site.name).order_by(Site.id)).all()
    devices = db.scalars(
        select(Device).where(
            Device.source == "uisp",
            Device.enabled.is_(True),
            Device.wisp_site_id.is_not(None),
        ).order_by(Device.id).limit(max_devices + 1)
    ).all()
    truncated = len(devices) > max_devices
    devices = devices[:max_devices]
    summary = {
        site_id: {
            "site_id": site_id, "site_name": name,
            "devices": 0, "fresh": 0, "stale": 0,
            "missing": 0, "future": 0, "degraded": 0,
        }
        for site_id, name in sites
    }
    ids = [device.id for device in devices if device.wisp_site_id in summary]
    if not ids:
        return {
            "sites": [], "processed_devices": len(devices),
            "truncated": truncated, "max_devices": max_devices,
            "note": "RF degradation is informational, not a reachability outage.",
        }

    # Match per-device diagnostics: rank ALL telemetry first (including
    # non-RF samples), then take the most recent 24 within 24 hours.
    ranked = select(
        TelemetrySample.device_id.label("device_id"),
        TelemetrySample.sampled_at.label("sampled_at"),
        TelemetrySample.raw_data.label("raw_data"),
        func.row_number().over(
            partition_by=TelemetrySample.device_id,
            order_by=(TelemetrySample.sampled_at.desc(), TelemetrySample.id.desc()),
        ).label("rn"),
    ).where(
        TelemetrySample.device_id.in_(ids),
        TelemetrySample.sampled_at >= now - timedelta(hours=24),
        TelemetrySample.sampled_at <= now,
    ).subquery()
    rows = db.execute(
        select(ranked.c.device_id, ranked.c.sampled_at, ranked.c.raw_data)
        .where(ranked.c.rn <= 24)
        .order_by(ranked.c.device_id, ranked.c.sampled_at.desc(), ranked.c.rn)
    ).all()
    history_by_device = defaultdict(list)
    for device_id, sampled_at, raw in rows:
        metrics = raw.get("rf_metrics_v1") if isinstance(raw, dict) else None
        if not isinstance(metrics, dict):
            continue
        if sampled_at.tzinfo is None:
            sampled_at = sampled_at.replace(tzinfo=timezone.utc)
        else:
            sampled_at = sampled_at.astimezone(timezone.utc)
        history_by_device[device_id].append((sampled_at, metrics))

    # Future-only telemetry is excluded from diagnostics, but still
    # distinguished from missing RF data in site summaries.
    future_rows = db.execute(
        select(TelemetrySample.device_id, func.max(TelemetrySample.sampled_at))
        .where(
            TelemetrySample.device_id.in_(ids),
            TelemetrySample.raw_data["rf_metrics_v1"].is_not(None),
        ).group_by(TelemetrySample.device_id)
    ).all()
    future_ids = {
        device_id for device_id, sampled_at in future_rows
        if sampled_at is not None and (
            sampled_at.replace(tzinfo=timezone.utc) if sampled_at.tzinfo is None
            else sampled_at.astimezone(timezone.utc)
        ) > now
    }

    for device in devices:
        if device.wisp_site_id not in summary:
            continue
        site = summary[device.wisp_site_id]
        # Diagnostics reverse descending query results before deduplication.
        ordered = list(reversed(history_by_device[device.id]))
        unique = {}
        for sampled_at, metrics in ordered:
            unique[sampled_at] = metrics
        history = [(at, unique[at]) for at in sorted(unique)]
        status = evaluate_rf_freshness(history[-1][0] if history else None, now=now)["status"]
        if status == "missing" and device.id in future_ids:
            status = "future"
        site["devices"] += 1
        site[status] += 1
        if evaluate_rf_degradation([metrics for _, metrics in history])["degraded"]:
            site["degraded"] += 1
    return {
        "sites": [value for value in summary.values() if value["devices"]],
        "processed_devices": len(devices),
        "truncated": truncated,
        "max_devices": max_devices,
        "note": "RF degradation is informational, not a reachability outage.",
    }
