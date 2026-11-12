"""End-to-end RF pipeline against disposable migrated PostgreSQL.

Simulated UISP response -> inventory sync -> normalized RF sample -> authenticated
diagnostics API. No external HTTP requests, radio commands or production DB.
"""
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import get_db
from app.integrations.uisp import sync_uisp
from app.main import app
from app.models.entities import Device, RFCollectionRun, TelemetrySample
from test_rf_postgres_integration import postgres_db  # shared disposable DB fixture


class SimulatedUISPResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return [
            {
                "identification": {"id": "rf-e2e-ci-radio", "name": "RF end-to-end CI"},
                "overview": {"status": "active", "rssi_dbm": -66, "snr_db": 27,
                             "ccq_percent": 94, "signal": -99},
                "enabled": True,
            },
            {
                "identification": {"id": "rf-e2e-ci-no-units", "name": "Ambiguous CI radio"},
                "overview": {"signal": -55},
                "enabled": True,
            },
        ]


def test_simulated_uisp_to_authenticated_diagnostics(postgres_db):
    db = postgres_db
    with patch("app.integrations.uisp.httpx.get", return_value=SimulatedUISPResponse()) as fetch, \
         patch.object(settings, "uisp_base_url", "https://uisp.invalid"), \
         patch.object(settings, "uisp_token", "ci-placeholder-only"), \
         patch.object(settings, "rf_collection_enabled", True), \
         patch.object(settings, "rf_history_pruning_enabled", False):
        result = sync_uisp(db)
    assert result["ok"] is True
    assert result["imported"] == 2
    assert result["rf_collection"] == {"received": 2, "staged": 1, "skipped": 1}
    fetch.assert_called_once()

    radio = db.scalar(select(Device).where(Device.external_id == "rf-e2e-ci-radio"))
    ambiguous = db.scalar(select(Device).where(Device.external_id == "rf-e2e-ci-no-units"))
    assert radio is not None and ambiguous is not None
    samples = db.scalars(select(TelemetrySample).where(TelemetrySample.device_id == radio.id)).all()
    assert len(samples) == 1
    assert samples[0].raw_data["rf_metrics_v1"] == {
        "rssi_dbm": -66.0, "snr_db": 27.0, "ccq_percent": 94.0,
    }
    assert db.scalars(select(TelemetrySample).where(TelemetrySample.device_id == ambiguous.id)).all() == []
    run = db.scalar(select(RFCollectionRun).order_by(RFCollectionRun.id.desc()))
    assert run.status == "success" and run.staged == 1

    # Test actual HTTP route serialization and authentication while reusing
    # the same isolated PostgreSQL session.
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: object()
    try:
        response = TestClient(app).get(f"/api/devices/{radio.id}/rf-diagnostics")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["device_id"] == radio.id
        assert report["rf_sample_count"] == 1
        assert report["freshness"]["status"] == "fresh"
        assert report["degradation"]["degraded"] is False
        assert report["trends"]["status"] == "insufficient_data"
    finally:
        app.dependency_overrides.clear()


def test_end_to_end_api_rejects_unauthenticated_requests(postgres_db):
    db = postgres_db
    app.dependency_overrides[get_db] = lambda: db
    try:
        assert TestClient(app).get("/api/devices/1/rf-diagnostics").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_staging_repeated_uisp_sync_respects_collection_interval(postgres_db):
    """Two successful UISP syncs should not duplicate RF samples."""
    db = postgres_db
    with patch("app.integrations.uisp.httpx.get", return_value=SimulatedUISPResponse()) as fetch, \
         patch.object(settings, "uisp_base_url", "https://uisp.invalid"), \
         patch.object(settings, "uisp_token", "ci-placeholder-only"), \
         patch.object(settings, "rf_collection_enabled", True), \
         patch.object(settings, "rf_collection_min_interval_seconds", 300), \
         patch.object(settings, "rf_history_pruning_enabled", False):
        first = sync_uisp(db)
        second = sync_uisp(db)
    assert first["rf_collection"]["staged"] == 1
    assert second["rf_collection"]["staged"] == 0
    assert second["rf_collection"]["skipped"] == 2
    assert fetch.call_count == 2
    radio = db.scalar(select(Device).where(Device.external_id == "rf-e2e-ci-radio"))
    assert len(db.scalars(select(TelemetrySample).where(
        TelemetrySample.device_id == radio.id,
    )).all()) == 1


def test_rf_collection_failure_recovers_on_next_sync(postgres_db):
    """A failed RF savepoint must preserve inventory and permit clean retry."""
    db = postgres_db
    with patch("app.integrations.uisp.httpx.get", return_value=SimulatedUISPResponse()), \
         patch.object(settings, "uisp_base_url", "https://uisp.invalid"), \
         patch.object(settings, "uisp_token", "ci-placeholder-only"), \
         patch.object(settings, "rf_collection_enabled", True), \
         patch.object(settings, "rf_history_pruning_enabled", False):
        with patch("app.integrations.uisp.stage_uisp_rf_batch",
                   side_effect=RuntimeError("simulated RF failure")):
            failed = sync_uisp(db)
        assert failed["ok"] is True
        assert failed["imported"] == 2
        assert failed["rf_collection"] == {"ok": False, "status": "failed"}
        radio = db.scalar(select(Device).where(Device.external_id == "rf-e2e-ci-radio"))
        assert radio is not None
        assert db.scalars(select(TelemetrySample).where(
            TelemetrySample.device_id == radio.id,
        )).all() == []
        recovered = sync_uisp(db)
        repeated = sync_uisp(db)
    assert recovered["rf_collection"]["staged"] == 1
    assert repeated["rf_collection"]["staged"] == 0
    assert len(db.scalars(select(TelemetrySample).where(
        TelemetrySample.device_id == radio.id,
    )).all()) == 1
    runs = db.scalars(select(RFCollectionRun).order_by(RFCollectionRun.id)).all()
    assert [run.status for run in runs[-3:]] == ["failed", "success", "success"]


def test_rf_partial_staging_rolls_back_before_retry(postgres_db):
    """Failure after RF sample insertion must not persist a partial batch."""
    db = postgres_db
    from app.integrations.uisp_rf_collection import stage_uisp_rf_batch
    def interrupted_stage(session, payload, **kwargs):
        stage_uisp_rf_batch(session, payload, **kwargs)
        session.flush()  # Force a database write inside the RF savepoint.
        raise RuntimeError("simulated interruption after RF insert")

    with patch("app.integrations.uisp.httpx.get", return_value=SimulatedUISPResponse()), \
         patch.object(settings, "uisp_base_url", "https://uisp.invalid"), \
         patch.object(settings, "uisp_token", "ci-placeholder-only"), \
         patch.object(settings, "rf_collection_enabled", True), \
         patch.object(settings, "rf_history_pruning_enabled", False):
        with patch("app.integrations.uisp.stage_uisp_rf_batch",
                   side_effect=interrupted_stage):
            interrupted = sync_uisp(db)
        assert interrupted["ok"] is True
        assert interrupted["rf_collection"] == {"ok": False, "status": "failed"}
        radio = db.scalar(select(Device).where(Device.external_id == "rf-e2e-ci-radio"))
        assert radio is not None
        assert db.scalars(select(TelemetrySample).where(
            TelemetrySample.device_id == radio.id,
        )).all() == []
        retried = sync_uisp(db)
        assert retried["rf_collection"]["staged"] == 1
    assert len(db.scalars(select(TelemetrySample).where(
        TelemetrySample.device_id == radio.id,
    )).all()) == 1
