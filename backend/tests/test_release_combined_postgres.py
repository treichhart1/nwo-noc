"""Combined scheduled synthetic collection -> history -> analytics -> operator views."""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select, func
from test_rf_postgres_integration import postgres_db
from app.core.config import settings
from app.models.entities import (
    Device,
    Site,
    NetworkLink,
    TelemetrySample,
    RFAnalyticsPoint,
    MonitoringAlert,
    MonitoringReceipt,
    RFRecommendation,
)
from app.services.monitoring_scheduler import enroll, claim, execute
from app.services.monitoring_spool import acknowledge
from app.services.rf_analytics_projection import project_batch
from app.services.rf_operations import overview
from app.services.rf_troubleshooting import evaluate


@pytest.mark.parametrize(
    "vendor", ["Ubiquiti", "Cambium", "Racom", "Aviat", "MikroTik"]
)
def test_scheduled_five_vendor_pipeline(postgres_db, monkeypatch, tmp_path, vendor):
    db = postgres_db
    for k, v in dict(
        offline_mode=True,
        app_env="staging",
        synthetic_monitoring_enabled=True,
        rf_scheduler_enabled=True,
        rf_analytics_projection_enabled=True,
    ).items():
        monkeypatch.setattr(settings, k, v)
    now = datetime.now(timezone.utc)
    base = now - timedelta(minutes=30)
    site = Site(name="Synthetic consolidated POP")
    db.add(site)
    db.flush()
    d = Device(
        source="synthetic",
        external_id="noc93-combined",
        name="Synthetic combined " + vendor,
        vendor=vendor,
        wisp_site_id=site.id,
        enabled=True,
    )
    db.add(d)
    db.flush()
    if vendor in {"Racom", "Aviat"}:
        peer = Device(
            source="synthetic",
            external_id="noc93-peer",
            name="Synthetic microwave peer",
            vendor=vendor,
            wisp_site_id=site.id,
        )
        db.add(peer)
        db.flush()
        db.add(
            NetworkLink(
                name="Synthetic explicitly associated backhaul",
                a_site_id=site.id,
                z_site_id=site.id,
                a_device_id=d.id,
                z_device_id=peer.id,
            )
        )
        db.flush()
    job = enroll(db, {"device_id": d.id, "interval_seconds": 15}, now=base)
    for n in range(31):
        at = base + timedelta(seconds=n * 60)
        job.fixture = {
            "mode": (
                "normal" if n < 24 else "overheat" if vendor == "MikroTik" else "weak"
            )
        }
        db.flush()
        pair = claim(db, "consolidated-fixture", now=at, limit=1)[0]
        db.commit()
        r = execute(db, *pair, now=at, spool_dir=tmp_path)
        assert r["result"] == "success"
        db.commit()
        acknowledge(tmp_path, r["spool_key"], r["spool_slot"])
    assert db.scalar(select(func.count(TelemetrySample.id))) == 31
    assert db.scalar(select(func.count(MonitoringReceipt.id))) == 31
    assert (
        db.scalar(
            select(MonitoringAlert.id).where(
                MonitoringAlert.device_id == d.id, MonitoringAlert.state == "active"
            )
        )
        is not None
    )
    while project_batch(db, limit=1000)["processed"]:
        pass
    first = db.scalar(select(func.count(RFAnalyticsPoint.id)))
    assert project_batch(db, limit=1000)["processed"] == 0
    assert db.scalar(select(func.count(RFAnalyticsPoint.id))) == first
    evaluate(db, [d.id], user_id=None, now=now)
    db.flush()
    view = overview(db, now=now, window="1h")
    assert view["execution_enabled"] is False and view["inventory"]
    if vendor == "MikroTik":
        assert first == 0  # CPU/temperature/OSPF are not fabricated RF series.
        assert (
            db.scalar(
                select(MonitoringAlert.id).where(MonitoringAlert.rule == "overheating")
            )
            is not None
        )
    else:
        assert first >= 31 and view["series"]
        assert (
            db.scalar(
                select(RFRecommendation.id).where(RFRecommendation.device_id == d.id)
            )
            is not None
        )
        assert view["recommendations"]
