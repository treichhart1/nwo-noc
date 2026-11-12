from datetime import datetime, timezone
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.api.deps import current_user
from app.core.database import get_db
from app.main import app


def test_history_requires_auth():
    assert TestClient(app).get("/api/rf/collection-history").status_code == 401


def test_history_is_bounded_and_read_only():
    db = Mock()
    run = Mock(id=7, status="success", received=5, staged=3, skipped=2)
    run.started_at = datetime(2026, 11, 12, tzinfo=timezone.utc)
    db.scalars.return_value.all.return_value = [run]
    app.dependency_overrides[current_user] = lambda: Mock(role="admin")
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/rf/collection-history?limit=20")
        assert response.status_code == 200
        assert response.json()[0]["staged"] == 3
        assert set(response.json()[0]) == {"id", "started_at", "status", "received", "staged", "skipped"}
        assert TestClient(app).get("/api/rf/collection-history?limit=101").status_code == 422
        db.commit.assert_not_called()
    finally:
        app.dependency_overrides.clear()
