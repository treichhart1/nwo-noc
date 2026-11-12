"""Real PostgreSQL integration checks for RF ingestion and history.

Run only with RF_INTEGRATION_TEST_DATABASE_URL pointing to a disposable,
migrated PostgreSQL database. No production network calls are performed.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.entities import Device, RFCollectionRun, TelemetrySample
from app.services.rf_collection_reliability import collection_reliability, prune_rf_collection_history
from app.services.rf_diagnostics import get_rf_diagnostics
from app.services.rf_persistence import store_rf_sample


@pytest.fixture
def postgres_db():
    url = os.getenv("RF_INTEGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Disposable PostgreSQL integration URL not supplied")
    if not url.startswith("postgresql+psycopg://"):
        pytest.fail("RF integration requires psycopg PostgreSQL URL")
    engine = create_engine(url)
    with engine.connect() as conn:
        database_name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
        if database_name != "nwo_noc":
            pytest.fail("Refusing to run against a database not named nwo_noc")
    # Each test uses a top-level transaction rolled back even when the
    # application calls session.commit(), via join_transaction_mode.
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            with Session(bind=conn, join_transaction_mode="create_savepoint") as session:
                yield session
        finally:
            transaction.rollback()
    engine.dispose()


def test_postgres_rf_sample_roundtrip_and_diagnostics(postgres_db):
    db = postgres_db
    now = datetime.now(timezone.utc)
    device = Device(source="uisp", external_id="rf-ci-postgres-1", name="RF CI radio", enabled=True)
    db.add(device)
    db.flush()
    sample = store_rf_sample(db, device, {"rssi_dbm": -64, "snr_db": 29}, sampled_at=now)
    assert sample is not None
    db.flush()
    db.expire_all()
    stored = db.scalar(select(TelemetrySample).where(TelemetrySample.device_id == device.id))
    assert stored.raw_data["rf_metrics_v1"]["rssi_dbm"] == -64.0
    report = get_rf_diagnostics(db, device.id, now=now + timedelta(seconds=5))
    assert report["rf_sample_count"] == 1
    assert report["freshness"]["status"] == "fresh"


def test_postgres_rf_savepoint_rollback_preserves_inventory(postgres_db):
    db = postgres_db
    device = Device(source="uisp", external_id="rf-ci-postgres-rollback", name="RF CI rollback", enabled=True)
    db.add(device)
    db.flush()
    with pytest.raises(RuntimeError, match="simulated"):
        with db.begin_nested():
            store_rf_sample(db, device, {"rssi_dbm": -70})
            db.flush()
            raise RuntimeError("simulated RF failure")
    db.commit()
    assert db.get(Device, device.id) is not None
    assert db.scalars(select(TelemetrySample).where(TelemetrySample.device_id == device.id)).all() == []


def test_postgres_rf_history_retention_and_reliability(postgres_db):
    db = postgres_db
    now = datetime.now(timezone.utc)
    db.add_all([
        RFCollectionRun(started_at=now - timedelta(days=40), status="success", received=2, staged=1, skipped=1),
        RFCollectionRun(started_at=now - timedelta(days=39), status="failed", received=0, staged=0, skipped=0),
        RFCollectionRun(started_at=now - timedelta(days=1), status="success", received=3, staged=2, skipped=1),
    ])
    db.flush()
    assert prune_rf_collection_history(db, retention_days=30, max_delete=1, now=now) == 1
    db.flush()
    assert prune_rf_collection_history(db, retention_days=30, max_delete=1, now=now) == 1
    db.flush()
    summary = collection_reliability(db, days=7, now=now)
    assert summary["total_runs"] == 1
    assert summary["successful_runs"] == 1
    assert summary["success_rate_percent"] == 100.0
