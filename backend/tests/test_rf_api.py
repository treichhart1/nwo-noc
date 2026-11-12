from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.api.deps import current_user
from app.core.database import get_db
from app.main import app


def test_rf_route_requires_authentication():
    client = TestClient(app)
    response = client.get("/api/devices/1/rf-diagnostics")
    assert response.status_code == 401


def test_rf_route_rejects_invalid_bounds_before_query():
    app.dependency_overrides[current_user] = lambda: Mock()
    app.dependency_overrides[get_db] = lambda: Mock()
    try:
        client = TestClient(app)
        assert client.get("/api/devices/1/rf-diagnostics?lookback_hours=169").status_code == 422
        assert client.get("/api/devices/1/rf-diagnostics?max_samples=1001").status_code == 422
        assert client.get("/api/devices/0/rf-diagnostics").status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_unknown_device_returns_404():
    db = Mock()
    db.get.return_value = None
    app.dependency_overrides[current_user] = lambda: Mock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/devices/42/rf-diagnostics")
        assert response.status_code == 404
        db.scalars.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_authenticated_read_only_response():
    db = Mock()
    device = Mock()
    device.name = "Radio 1"
    db.get.return_value = device
    db.scalars.return_value.all.return_value = []
    app.dependency_overrides[current_user] = lambda: Mock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/devices/1/rf-diagnostics")
        assert response.status_code == 200
        assert response.json()["rf_sample_count"] == 0
        db.commit.assert_not_called()
        db.add.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_rf_collection_health_requires_authentication():
    assert TestClient(app).get("/api/rf/collection-health").status_code == 401


def test_rf_collection_history_requires_authentication():
    assert TestClient(app).get("/api/rf/collection-history").status_code == 401


def test_rf_collection_reliability_requires_authentication():
    assert TestClient(app).get("/api/rf/collection-reliability").status_code == 401
