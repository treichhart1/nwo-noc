"""Bounded read-only RF summaries grouped by assigned NOC site."""
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.entities import Device, Site, TelemetrySample
from app.services.rf_diagnostics import get_rf_diagnostics


def get_rf_site_health(db: Session, *, max_devices: int = 250) -> dict:
    """Summarize assigned UISP radios; never infer outages from RF quality."""
    if not 1 <= max_devices <= 250:
        raise ValueError("max_devices out of range")
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
    # Batch future-only timestamp lookup rather than one query per missing radio.
    # Keep diagnostics bounded to 24 samples per device and do not change its
    # degradation or trend interpretation.
    missing_ids = []
    device_reports = []
    for device in devices:
        if device.wisp_site_id not in summary:
            continue
        report = get_rf_diagnostics(db, device.id, max_samples=24)
        device_reports.append((device, report))
        if report["freshness"]["status"] == "missing":
            missing_ids.append(device.id)

    future_ids = set()
    if missing_ids:
        latest_rows = db.execute(
            select(TelemetrySample.device_id, func.max(TelemetrySample.sampled_at))
            .where(
                TelemetrySample.device_id.in_(missing_ids),
                TelemetrySample.raw_data["rf_metrics_v1"].is_not(None),
            )
            .group_by(TelemetrySample.device_id)
        ).all()
        current = datetime.now(timezone.utc)
        for device_id, latest in latest_rows:
            if latest is not None:
                if latest.tzinfo is None:
                    latest = latest.replace(tzinfo=timezone.utc)
                if latest > current:
                    future_ids.add(device_id)

    for device, report in device_reports:
        site = summary[device.wisp_site_id]
        status = report["freshness"]["status"]
        if status == "missing" and device.id in future_ids:
            status = "future"
        site["devices"] += 1
        site[status] += 1
        if report["degradation"]["degraded"]:
            site["degraded"] += 1
    return {
        "sites": [value for value in summary.values() if value["devices"]],
        "processed_devices": len(devices),
        "truncated": truncated,
        "max_devices": max_devices,
        "note": "RF degradation is informational, not a reachability outage.",
    }
