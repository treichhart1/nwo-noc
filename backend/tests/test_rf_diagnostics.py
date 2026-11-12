from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from app.services.rf_diagnostics import get_rf_diagnostics


NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def sample(minutes, metrics, sample_id):
    row = Mock()
    row.id = sample_id
    row.sampled_at = NOW - timedelta(minutes=minutes)
    row.raw_data = {"rf_metrics_v1": metrics}
    return row


def test_combines_degradation_and_trends_without_writes():
    db = Mock()
    db.get.return_value = Mock(name="device", **{"name": "Willowdell radio"})
    rows = [
        sample((5 - i) * 10, {"rssi_dbm": v}, i + 1)
        for i, v in enumerate([-60, -61, -62, -80, -81, -82])
    ]
    db.scalars.return_value.all.return_value = list(reversed(rows))
    result = get_rf_diagnostics(db, 4, now=NOW)
    assert result["device_id"] == 4
    assert result["rf_sample_count"] == 6
    assert result["degradation"]["degraded"] is True
    assert result["trends"]["status"] == "declining"
    db.commit.assert_not_called()
    db.add.assert_not_called()


def test_skips_non_rf_samples():
    db = Mock()
    db.get.return_value = Mock(name="device", **{"name": "Radio"})
    row = sample(5, {}, 1)
    row.raw_data = {"other": 1}
    db.scalars.return_value.all.return_value = [row]
    result = get_rf_diagnostics(db, 4, now=NOW)
    assert result["rf_sample_count"] == 0
    assert result["trends"]["status"] == "insufficient_data"


def test_unknown_device_raises():
    db = Mock()
    db.get.return_value = None
    with pytest.raises(LookupError):
        get_rf_diagnostics(db, 42, now=NOW)
    db.scalars.assert_not_called()


def test_invalid_bounds():
    db = Mock()
    with pytest.raises(ValueError):
        get_rf_diagnostics(db, 0)
    with pytest.raises(ValueError):
        get_rf_diagnostics(db, 1, lookback_hours=0)
    with pytest.raises(ValueError):
        get_rf_diagnostics(db, 1, max_samples=1001)
    with pytest.raises(ValueError):
        get_rf_diagnostics(db, 1, now=datetime(2026, 11, 12))
    db.get.assert_not_called()
