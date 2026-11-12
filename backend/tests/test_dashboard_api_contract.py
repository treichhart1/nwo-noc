"""Offline HTTP contract regression for the NOC dashboard."""
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.api.deps import current_user
from app.core.database import get_db
from app.main import app


def test_dashboard_rejects_anonymous_access():
    app.dependency_overrides.clear()
    response = TestClient(app).get("/api/dashboard")
    assert response.status_code == 401


def test_dashboard_synthetic_operational_summary_is_read_only():
    db = Mock()
    # Device counters come from one aggregate row; remaining scalars are sites, links, sectors, subscribers.
    db.execute.return_value.one.return_value = (10, 6, 2, 3, 8)
    db.scalar.side_effect = [2, 1, 4, 25]
    db.scalars.return_value = []
    app.dependency_overrides[current_user] = lambda: Mock(role="operator")
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/dashboard")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data == {
            "total_devices": 10,
            "active_devices": 6,
            "unauthorized_devices": 2,
            "offline_devices": 3,
            "warning_devices": 1,
            "mapped_devices": 8,
            "sites": 2,
            "links": 1,
            "sectors": 4,
            "subscribers": 25,
            "health_score": 60,
            "integrations": [],
        }
        db.commit.assert_not_called()
        db.add.assert_not_called()
        db.delete.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_dashboard_empty_inventory_is_healthy_and_read_only():
    db = Mock()
    db.execute.return_value.one.return_value = (0, 0, 0, 0, 0)
    db.scalar.side_effect = [0] * 4
    db.scalars.return_value = []
    app.dependency_overrides[current_user] = lambda: Mock(role="operator")
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/dashboard")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total_devices"] == 0
        assert data["health_score"] == 100
        assert data["warning_devices"] == 0
        db.commit.assert_not_called()
    finally:
        app.dependency_overrides.clear()
