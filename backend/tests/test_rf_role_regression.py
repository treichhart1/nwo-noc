"""RF authorization regression tests across the actual HTTP dependency chain."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import current_user
from app.core.database import get_db
from app.main import app


ENDPOINTS = (
    "/api/devices/1/rf-diagnostics",
    "/api/rf/collection-health",
    "/api/rf/collection-history",
    "/api/rf/collection-reliability",
    "/api/rf/site-health",
)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_anonymous_rf_request_is_unauthorized(endpoint):
    app.dependency_overrides.clear()
    assert TestClient(app).get(endpoint).status_code == 401


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("role", ["viewer", "guest", "unexpected", ""])
def test_non_privileged_rf_request_is_forbidden(endpoint, role):
    db = Mock()
    app.dependency_overrides[current_user] = lambda: Mock(role=role)
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get(endpoint)
        assert response.status_code == 403
        db.execute.assert_not_called()
        db.scalars.assert_not_called()
        db.commit.assert_not_called()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_privileged_rf_history_read_succeeds_without_writes(role):
    db = Mock()
    db.scalars.return_value.all.return_value = []
    app.dependency_overrides[current_user] = lambda: Mock(role=role)
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/rf/collection-history")
        assert response.status_code == 200
        assert response.json() == []
        db.commit.assert_not_called()
        db.add.assert_not_called()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_privileged_site_health_read_succeeds_without_writes(role):
    db = Mock()
    db.execute.return_value.all.return_value = []
    db.scalars.return_value.all.return_value = []
    app.dependency_overrides[current_user] = lambda: Mock(role=role)
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/rf/site-health")
        assert response.status_code == 200
        assert response.json()["sites"] == []
        db.commit.assert_not_called()
        db.add.assert_not_called()
    finally:
        app.dependency_overrides.clear()
