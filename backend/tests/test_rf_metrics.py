"""Regression tests for vendor-neutral RF metric normalization."""
import pytest

from app.services.rf_metrics import normalize_rf_metrics


def test_normalizes_valid_explicit_fields():
    result = normalize_rf_metrics({
        "signal_dbm": "-62.5",
        "noise_floor_dbm": -94,
        "snr_db": 31.5,
        "ccq_percent": "98",
        "frequency_mhz": 10995,
        "channel_width_mhz": 80,
        "tx_rate_mbps": 1200,
        "uptime_seconds": 86400,
    })
    assert result == {
        "rssi_dbm": -62.5,
        "noise_floor_dbm": -94.0,
        "snr_db": 31.5,
        "ccq_percent": 98.0,
        "frequency_mhz": 10995.0,
        "channel_width_mhz": 80.0,
        "tx_rate_mbps": 1200.0,
        "uptime_seconds": 86400.0,
    }


def test_missing_fields_are_not_invented():
    assert normalize_rf_metrics({}) == {}
    assert normalize_rf_metrics({"signal": -60, "frequency": 11000}) == {}


def test_rejects_invalid_and_out_of_range_measurements():
    result = normalize_rf_metrics({
        "ccq_percent": 120,
        "rssi_dbm": float("nan"),
        "noise_floor_dbm": float("inf"),
        "snr_db": True,
        "frequency_mhz": -1,
        "tx_rate_mbps": "-5",
        "temperature_c": "not available",
    })
    assert result == {}


def test_ignores_unexpected_keys_and_does_not_mutate_input():
    payload = {"rssi_dbm": -70, "password": "private", "raw": {"token": "secret"}}
    assert normalize_rf_metrics(payload) == {"rssi_dbm": -70.0}
    assert payload["rssi_dbm"] == -70


def test_non_mapping_payload_rejected():
    with pytest.raises(TypeError):
        normalize_rf_metrics([{"rssi_dbm": -70}])
