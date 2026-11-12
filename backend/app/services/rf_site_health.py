"""Bounded read-only RF summaries grouped by assigned NOC site."""
from collections import defaultdict

from datetime import datetime, timezone

from sqlalchemy import select
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
    for device in devices:
        site = summary.get(device.wisp_site_id)
        if site is None:
            continue
        report = get_rf_diagnostics(db, device.id, max_samples=24)
        status = report["freshness"]["status"]
        # Diagnostics exclude future timestamps from trend analysis.
        # Identify future-only latest samples without marking them missing.
        if status == "missing":
            latest = db.scalar(
                select(TelemetrySample.sampled_at)
                .where(
                    TelemetrySample.device_id == device.id,
                    TelemetrySample.raw_data["rf_metrics_v1"].is_not(None),
                )
                .order_by(TelemetrySample.sampled_at.desc(), TelemetrySample.id.desc())
                .limit(1)
            )
            if latest is not None:
                if latest.tzinfo is None:
                    latest = latest.replace(tzinfo=timezone.utc)
                if latest > datetime.now(timezone.utc):
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
