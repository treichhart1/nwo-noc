from datetime import datetime, timedelta, timezone

import pytest

from app.services.rf_freshness import evaluate_rf_freshness


NOW = datetime(2026, 11, 12, 16, tzinfo=timezone.utc)


def test_missing_is_not_healthy():
    assert evaluate_rf_freshness(None, now=NOW)["status"] == "missing"


def test_fresh_sample():
    result = evaluate_rf_freshness(NOW - timedelta(minutes=5), now=NOW)
    assert result["status"] == "fresh"
    assert result["age_seconds"] == 300


def test_stale_sample():
    result = evaluate_rf_freshness(NOW - timedelta(minutes=16), now=NOW)
    assert result["status"] == "stale"


def test_future_timestamp_is_not_fresh():
    assert evaluate_rf_freshness(NOW + timedelta(seconds=1), now=NOW)["status"] == "future"


def test_boundary_is_fresh():
    assert evaluate_rf_freshness(NOW - timedelta(seconds=900), now=NOW)["status"] == "fresh"


def test_invalid_config_and_naive_timestamps():
    with pytest.raises(ValueError):
        evaluate_rf_freshness(None, now=NOW, stale_after_seconds=0)
    with pytest.raises(ValueError):
        evaluate_rf_freshness(None, now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        evaluate_rf_freshness(NOW.replace(tzinfo=None), now=NOW)
