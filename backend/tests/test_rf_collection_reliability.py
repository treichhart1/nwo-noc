from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import current_user
from app.core.database import get_db
from app.main import app
from app.services.rf_collection_reliability import collection_reliability, prune_rf_collection_history


NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def test_reliability_counts_and_empty_window():
    db = Mock()
    db.execute.return_value.all.return_value = [("success", 8), ("failed", 2)]
    result = collection_reliability(db, days=7, now=NOW)
    assert result["success_rate_percent"] == 80.0
    assert result["total_runs"] == 10
    db.execute.return_value.all.return_value = []
    assert collection_reliability(db, now=NOW)["success_rate_percent"] is None


def test_pruning_is_bounded_and_never_commits():
    db = Mock()
    db.scalars.return_value.all.return_value = [1, 2]
    assert prune_rf_collection_history(db, now=NOW, max_delete=2) == 2
    db.execute.assert_called_once()
    db.commit.assert_not_called()


def test_empty_pruning_does_not_delete():
    db = Mock()
    db.scalars.return_value.all.return_value = []
    assert prune_rf_collection_history(db, now=NOW) == 0
    db.execute.assert_not_called()


def test_invalid_retention_settings():
    with pytest.raises(ValueError):
        prune_rf_collection_history(Mock(), retention_days=1)
    with pytest.raises(ValueError):
        prune_rf_collection_history(Mock(), max_delete=1001)


def test_reliability_endpoint_auth_and_bounds():
    assert TestClient(app).get("/api/rf/collection-reliability").status_code == 401
    db = Mock()
    db.execute.return_value.all.return_value = []
    app.dependency_overrides[current_user] = lambda: Mock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        assert TestClient(app).get("/api/rf/collection-reliability").status_code == 200
        assert TestClient(app).get("/api/rf/collection-reliability?days=31").status_code == 422
        db.commit.assert_not_called()
    finally:
        app.dependency_overrides.clear()
