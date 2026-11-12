from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.models.entities import Device, TelemetrySample
from app.services.rf_persistence import store_rf_sample


def test_stages_normalized_sample_without_committing():
    db = Mock()
    device = Device(source="uisp", external_id="radio-1", name="Radio")
    device.id = 17
    timestamp = datetime(2026, 11, 12, tzinfo=timezone.utc)
    result = store_rf_sample(
        db, device, {"rssi_dbm": -65, "snr_db": 32, "temperature_c": 41},
        sampled_at=timestamp,
    )
    assert isinstance(result, TelemetrySample)
    assert result.device_id == 17
    assert result.sampled_at == timestamp
    assert result.signal_dbm == -65.0
    assert result.temperature_c == 41.0
    assert result.raw_data == {
        "rf_metrics_v1": {"rssi_dbm": -65.0, "snr_db": 32.0, "temperature_c": 41.0}
    }
    db.add.assert_called_once_with(result)
    db.commit.assert_not_called()


def test_empty_or_invalid_metrics_do_not_write():
    db = Mock()
    device = Device(source="uisp", external_id="radio-2", name="Radio")
    device.id = 18
    assert store_rf_sample(db, device, {"ccq_percent": 300}) is None
    assert store_rf_sample(db, device, {}) is None
    db.add.assert_not_called()


def test_unsaved_device_rejected():
    with pytest.raises(ValueError, match="unsaved"):
        store_rf_sample(Mock(), Device(source="uisp", external_id="x", name="X"), {"snr_db": 10})


def test_naive_timestamp_rejected_without_write():
    db = Mock()
    device = Device(source="uisp", external_id="x", name="X")
    device.id = 19
    with pytest.raises(ValueError, match="timezone-aware"):
        store_rf_sample(db, device, {"snr_db": 10}, sampled_at=datetime(2026, 11, 12))
    db.add.assert_not_called()
