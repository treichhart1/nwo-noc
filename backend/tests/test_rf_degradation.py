import pytest

from app.services.rf_degradation import RFThresholds, evaluate_rf_degradation


def test_requires_three_consecutive_low_rssi_samples():
    samples = [{"rssi_dbm": -80}, {"rssi_dbm": -81}, {"rssi_dbm": -82}]
    result = evaluate_rf_degradation(samples)
    assert result["degraded"] is True
    assert result["reasons"] == ["weak_rssi"]
    assert result["consecutive_counts"]["weak_rssi"] == 3


def test_short_dip_does_not_trigger():
    samples = [{"snr_db": 15}, {"snr_db": 14}, {"snr_db": 25}]
    assert evaluate_rf_degradation(samples)["degraded"] is False


def test_missing_measurement_resets_streak():
    samples = [{"ccq_percent": 70}, {}, {"ccq_percent": 70}, {"ccq_percent": 70}]
    assert evaluate_rf_degradation(samples)["degraded"] is False


def test_multiple_degradation_reasons():
    samples = [{"snr_db": 10, "ccq_percent": 50}] * 3
    assert evaluate_rf_degradation(samples)["reasons"] == ["low_ccq", "low_snr"]


def test_custom_thresholds_and_streak():
    thresholds = RFThresholds(minimum_rssi_dbm=-65, consecutive_samples=2)
    result = evaluate_rf_degradation([{"rssi_dbm": -70}] * 2, thresholds)
    assert result["degraded"] is True


def test_invalid_measurements_do_not_trigger():
    assert not evaluate_rf_degradation([{"rssi_dbm": "nan", "snr_db": True}] * 3)["degraded"]


def test_thresholds_are_validated():
    with pytest.raises(ValueError):
        RFThresholds(consecutive_samples=1)
    with pytest.raises(ValueError):
        RFThresholds(minimum_ccq_percent=150)


def test_rejects_bad_sample_inputs():
    with pytest.raises(TypeError):
        evaluate_rf_degradation("bad")
    with pytest.raises(TypeError):
        evaluate_rf_degradation([None])
