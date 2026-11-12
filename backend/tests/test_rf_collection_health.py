from datetime import datetime, timezone
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.deps import current_user
from app.core.database import get_db
from app.main import app
from app.services.rf_collection_health import get_rf_collection_health


def test_collection_health_requires_auth():
    assert TestClient(app).get("/api/rf/collection-health").status_code == 401


def test_collection_health_never_exposes_credentials_or_raw_payload():
    db = Mock()
    db.scalar.side_effect = [None, 2, None]
    db.execute.return_value.all.return_value = [
        (1, datetime(2026, 11, 12, 16, tzinfo=timezone.utc)),
    ]
    with patch("app.services.rf_collection_health.settings") as settings:
        settings.rf_collection_enabled = False
        report = get_rf_collection_health(db, now=datetime(2026, 11, 12, 16, tzinfo=timezone.utc))
    assert report["rf_devices_fresh"] == 1
    assert report["rf_devices_missing"] == 1
    assert report["enabled"] is False
    assert "token" not in str(report).lower()
    assert "raw_data" not in str(report)


def test_collection_health_authenticated_route():
    db = Mock()
    db.scalar.side_effect = [None, 0, None]
    db.execute.return_value.all.return_value = []
    app.dependency_overrides[current_user] = lambda: Mock(role="admin")
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/rf/collection-health")
        assert response.status_code == 200
        assert response.json()["rf_devices_missing"] == 0
        db.commit.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_collection_health_includes_persisted_latest_success_counts():
    db = Mock()
    run = Mock(status="success", received=12, staged=7, skipped=5)
    run.started_at = datetime(2026, 11, 12, 16, tzinfo=timezone.utc)
    db.scalar.side_effect = [None, 0, run]
    db.execute.return_value.all.return_value = []
    report = get_rf_collection_health(db, now=run.started_at)
    assert report["last_batch_counts"] == {"received": 12, "staged": 7, "skipped": 5}
    assert report["last_batch_status"] == "success"
    db.commit.assert_not_called()


def test_collection_health_failed_run_has_no_misleading_counts():
    db = Mock()
    run = Mock(status="failed")
    run.started_at = datetime(2026, 11, 12, 16, tzinfo=timezone.utc)
    db.scalar.side_effect = [None, 0, run]
    db.execute.return_value.all.return_value = []
    report = get_rf_collection_health(db, now=run.started_at)
    assert report["last_batch_counts"] is None
    assert report["last_batch_status"] == "failed"
