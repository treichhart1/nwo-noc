"""Offline authenticated HTTP contracts for NOC operations and monitoring summaries."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import current_user
from app.core.database import get_db
from app.main import app


@pytest.mark.parametrize("endpoint", ["/api/operations/summary", "/api/monitoring/summary"])
def test_operational_summaries_require_authentication(endpoint):
    app.dependency_overrides.clear()
    assert TestClient(app).get(endpoint).status_code == 401


def test_monitoring_summary_tracks_unknown_and_unacknowledged_events():
    db = Mock()
    # One device aggregate plus one event-count query.
    db.execute.return_value.one.return_value = (12, 7, 3, 14.567, None, 2, 5, 2, 1)
    db.scalar.return_value = 4
    app.dependency_overrides[current_user] = lambda: Mock(role="operator")
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/monitoring/summary")
        assert response.status_code == 200, response.text
        assert response.json() == {
            "enabled_devices": 12,
            "stale_devices": 2,
            "never_checked_devices": 1,
            "fresh_up_devices": 5,
            "fresh_down_devices": 2,
            "up_devices": 7,
            "down_devices": 3,
            "unknown_devices": 2,
            "average_latency_ms": 14.57,
            "unacknowledged_events": 4,
            "last_check_at": None,
        }
        assert db.execute.call_count == 1
        assert db.scalar.call_count == 1
        db.commit.assert_not_called()
        db.add.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_operations_summary_counts_distinct_offline_sites():
    db = Mock()
    # Two aggregate rows (incidents, devices) plus maintenance and links.
    db.execute.return_value.one.side_effect = [(5, 2, 41), (2, 4, 6)]
    db.scalar.side_effect = [3, 1]
    app.dependency_overrides[current_user] = lambda: Mock(role="operator")
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/operations/summary")
        assert response.status_code == 200, response.text
        assert response.json() == {
            "open_incidents": 5,
            "critical_incidents": 2,
            "impacted_customers": 41,
            "upcoming_maintenance": 3,
            "offline_sites": 2,
            "degraded_links": 1,
            "unassigned_devices": 4,
            "stale_devices": 6,
        }
        assert db.execute.call_count == 2
        assert db.scalar.call_count == 2
        db.scalars.assert_not_called()
        db.commit.assert_not_called()
        db.add.assert_not_called()
    finally:
        app.dependency_overrides.clear()
