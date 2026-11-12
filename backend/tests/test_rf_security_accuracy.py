"""RF adapter input validation and unit accuracy regressions."""
import math

from app.integrations.uisp_rf import extract_uisp_rf_metrics
from app.services.rf_metrics import normalize_rf_metrics


def test_unitless_uisp_fields_are_not_inferred():
    raw = {"radio": {"signal": -67, "frequency": 11000, "txRate": 1500},
           "overview": {"signal": -68, "ccq": 95}}
    assert extract_uisp_rf_metrics(raw) == {}


def test_invalid_and_nonfinite_values_are_rejected():
    raw = {"rssi_dbm": float("nan"), "snr_db": float("inf"),
           "ccq_percent": 101, "frequency_mhz": -1,
           "tx_rate_mbps": True, "temperature_c": "-999"}
    assert normalize_rf_metrics(raw) == {}


def test_explicit_units_preserve_valid_metrics():
    raw = {"radio": {"rssi_dbm": -67, "snr_db": 24,
                     "ccq_percent": 96, "frequency_mhz": 11000}}
    assert extract_uisp_rf_metrics(raw) == {
        "rssi_dbm": -67.0, "snr_db": 24.0,
        "ccq_percent": 96.0, "frequency_mhz": 11000.0,
    }


def test_boolean_signal_is_not_numeric():
    assert normalize_rf_metrics({"rssi_dbm": True}) == {}
