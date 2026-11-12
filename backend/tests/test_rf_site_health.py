from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from app.api.deps import current_user
from app.core.database import get_db
from app.main import app
from app.services.rf_site_health import get_rf_site_health


def test_rf_site_health_requires_authentication():
    assert TestClient(app).get("/api/rf/site-health").status_code == 401


@pytest.mark.parametrize("limit", [0, 251])
def test_rf_site_health_rejects_unbounded_work(limit):
    with pytest.raises(ValueError):
        get_rf_site_health(Mock(), max_devices=limit)


def test_authenticated_empty_site_health_is_read_only():
    db = Mock()
    db.execute.return_value.all.return_value = []
    db.scalars.return_value.all.return_value = []
    app.dependency_overrides[current_user] = lambda: Mock(role="admin")
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/rf/site-health")
        assert response.status_code == 200
        assert response.json()["sites"] == []
        db.add.assert_not_called()
        db.commit.assert_not_called()
    finally:
        app.dependency_overrides.clear()
