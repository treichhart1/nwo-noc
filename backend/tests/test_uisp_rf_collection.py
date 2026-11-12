from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.integrations.uisp_rf_collection import stage_uisp_rf_batch
from app.models.entities import Device


NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def _device():
    device = Device(source="uisp", external_id="radio-1", name="Radio")
    device.id = 12
    return device


def _payload():
    return [{"identification": {"id": "radio-1"}, "radio": {"rssi_dbm": -62}}]


def test_stages_known_device_without_commit():
    db = Mock()
    db.scalar.side_effect = [_device(), None]
    result = stage_uisp_rf_batch(db, _payload(), sampled_at=NOW)
    assert result == {"received": 1, "staged": 1, "skipped": 0}
    db.add.assert_called_once()
    db.commit.assert_not_called()


def test_recent_sample_skips_write():
    db = Mock()
    db.scalar.side_effect = [_device(), 99]
    assert stage_uisp_rf_batch(db, _payload(), sampled_at=NOW)["staged"] == 0
    db.add.assert_not_called()


def test_unknown_device_skips_write():
    db = Mock()
    db.scalar.return_value = None
    assert stage_uisp_rf_batch(db, _payload(), sampled_at=NOW)["skipped"] == 1
    db.add.assert_not_called()


def test_duplicate_device_is_skipped():
    db = Mock()
    db.scalar.side_effect = [_device(), None]
    result = stage_uisp_rf_batch(db, _payload() * 2, sampled_at=NOW)
    assert result == {"received": 2, "staged": 1, "skipped": 1}
    db.add.assert_called_once()


def test_invalid_and_ambiguous_payloads_skip():
    db = Mock()
    raw = [
        {"identification": {"id": "radio-1"}, "overview": {"signal": -60}},
        {"identification": {"id": 123}, "radio": {"rssi_dbm": -60}},
        {},
    ]
    assert stage_uisp_rf_batch(db, raw, sampled_at=NOW)["skipped"] == 3
    db.scalar.assert_not_called()


def test_limits_are_enforced_before_database_access():
    db = Mock()
    with pytest.raises(ValueError, match="device limit"):
        stage_uisp_rf_batch(db, _payload() * 2, max_devices=1)
    with pytest.raises(ValueError, match="min_interval"):
        stage_uisp_rf_batch(db, [], min_interval_seconds=1)
    with pytest.raises(ValueError, match="timezone-aware"):
        stage_uisp_rf_batch(db, [], sampled_at=datetime(2026, 11, 12))
    db.scalar.assert_not_called()
