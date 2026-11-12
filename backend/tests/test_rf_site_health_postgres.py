"""Site RF accuracy and bounded-load tests against disposable PostgreSQL."""
from datetime import datetime, timedelta, timezone

from app.models.entities import Device, Site
from app.services.rf_persistence import store_rf_sample
from app.services.rf_site_health import get_rf_site_health
from test_rf_postgres_integration import postgres_db


def test_site_summary_accuracy_and_assignment(postgres_db):
    db = postgres_db
    now = datetime.now(timezone.utc)
    site = Site(name="RF Site CI")
    db.add(site)
    db.flush()
    fresh = Device(source="uisp", external_id="rf-site-fresh", name="Fresh", enabled=True, wisp_site_id=site.id)
    stale = Device(source="uisp", external_id="rf-site-stale", name="Stale", enabled=True, wisp_site_id=site.id)
    missing = Device(source="uisp", external_id="rf-site-missing", name="Missing", enabled=True, wisp_site_id=site.id)
    degraded = Device(source="uisp", external_id="rf-site-degraded", name="Degraded", enabled=True, wisp_site_id=site.id)
    future = Device(source="uisp", external_id="rf-site-future", name="Future", enabled=True, wisp_site_id=site.id)
    unassigned = Device(source="uisp", external_id="rf-site-unassigned", name="Unassigned", enabled=True)
    disabled = Device(source="uisp", external_id="rf-site-disabled", name="Disabled", enabled=False, wisp_site_id=site.id)
    db.add_all([fresh, stale, missing, degraded, future, unassigned, disabled])
    db.flush()
    store_rf_sample(db, fresh, {"rssi_dbm": -62}, sampled_at=now - timedelta(minutes=1))
    store_rf_sample(db, stale, {"rssi_dbm": -65}, sampled_at=now - timedelta(hours=2))
    for minutes in (3, 2, 1):
        store_rf_sample(db, degraded, {"rssi_dbm": -90, "snr_db": 8, "ccq_percent": 40}, sampled_at=now - timedelta(minutes=minutes))
    store_rf_sample(db, future, {"rssi_dbm": -63}, sampled_at=now + timedelta(hours=2))
    store_rf_sample(db, unassigned, {"rssi_dbm": -90}, sampled_at=now)
    store_rf_sample(db, disabled, {"rssi_dbm": -90}, sampled_at=now)
    db.flush()
    result = get_rf_site_health(db)
    assert result["truncated"] is False
    assert result["processed_devices"] == 5
    assert result["sites"] == [{
        "site_id": site.id, "site_name": "RF Site CI",
        "devices": 5, "fresh": 2, "stale": 1,
        "missing": 1, "future": 1, "degraded": 1,
    }]


def test_site_summary_caps_inventory_and_flags_partial_results(postgres_db):
    db = postgres_db
    site = Site(name="RF Scale CI")
    db.add(site)
    db.flush()
    db.add_all([
        Device(source="uisp", external_id=f"rf-site-scale-{i}", name=f"Radio {i}",
               enabled=True, wisp_site_id=site.id)
        for i in range(260)
    ])
    db.flush()
    result = get_rf_site_health(db)
    assert result["processed_devices"] == 250
    assert result["truncated"] is True
    assert result["sites"][0]["devices"] == 250
    assert result["sites"][0]["missing"] == 250


def test_future_lookup_is_batched_for_missing_radios(postgres_db):
    """Missing radios should not each trigger a separate future-only query."""
    from sqlalchemy import event
    db = postgres_db
    site = Site(name="RF Batch CI")
    db.add(site)
    db.flush()
    db.add_all([
        Device(source="uisp", external_id=f"rf-batch-{i}", name=f"Missing {i}",
               enabled=True, wisp_site_id=site.id)
        for i in range(40)
    ])
    db.flush()
    counts = {"grouped": 0}
    engine = db.get_bind().engine

    def record(_conn, _cursor, statement, _params, _context, _executemany):
        if "GROUP BY" in statement.upper() and "telemetry_samples" in statement:
            counts["grouped"] += 1

    event.listen(engine, "before_cursor_execute", record)
    try:
        result = get_rf_site_health(db)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert result["processed_devices"] == 40
    assert result["sites"][0]["missing"] == 40
    assert counts["grouped"] == 1


def test_bulk_site_history_matches_bounded_device_diagnostics(postgres_db):
    from app.services.rf_diagnostics import get_rf_diagnostics
    db = postgres_db
    site = Site(name="RF Bulk Parity CI")
    db.add(site)
    db.flush()
    radio = Device(source="uisp", external_id="rf-bulk-parity", name="Bulk parity",
                   enabled=True, wisp_site_id=site.id)
    db.add(radio)
    db.flush()
    now = datetime.now(timezone.utc)
    for i in range(30):
        store_rf_sample(db, radio, {"rssi_dbm": -90 if i < 3 else -60},
                        sampled_at=now - timedelta(minutes=i + 1))
    db.flush()
    single = get_rf_diagnostics(db, radio.id, max_samples=24)
    bulk = get_rf_site_health(db)
    site_result = next(s for s in bulk["sites"] if s["site_id"] == site.id)
    assert site_result[single["freshness"]["status"]] == 1
    assert site_result["degraded"] == int(single["degradation"]["degraded"])
