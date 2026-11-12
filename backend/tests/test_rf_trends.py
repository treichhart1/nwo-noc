from datetime import datetime, timedelta, timezone

import pytest

from app.services.rf_trends import RFTrendConfig, analyze_rf_trends


START = datetime(2026, 11, 12, tzinfo=timezone.utc)


def samples(values, field="rssi_dbm", minutes=10):
    return [
        {"sampled_at": START + timedelta(minutes=i * minutes),
         "metrics": {field: value}}
        for i, value in enumerate(values)
    ]


def test_sustained_rssi_decline():
    result = analyze_rf_trends(samples([-60, -61, -62, -68, -69, -70]))
    assert result["status"] == "declining"
    assert result["declining_metrics"] == ["rssi_dbm"]
    assert result["metrics"]["rssi_dbm"]["change"] == -9.0


def test_stable_signal():
    result = analyze_rf_trends(samples([-60, -61, -60, -61, -60, -61]))
    assert result["status"] == "stable"


def test_single_spike_is_filtered_by_median():
    result = analyze_rf_trends(samples([-60, -60, -100, -60, -60, -60, -60, -60, -60]))
    assert result["status"] == "stable"


def test_missing_metrics_not_treated_as_decline():
    raw = samples([-60, -60, -60, -60, -60, -60])
    for item in raw[-3:]:
        item["metrics"] = {}
    assert analyze_rf_trends(raw)["status"] == "insufficient_data"


def test_short_duration_is_insufficient():
    assert analyze_rf_trends(samples([-60] * 6, minutes=1))["status"] == "insufficient_data"


def test_snr_decline():
    result = analyze_rf_trends(samples([30, 30, 29, 23, 22, 22], "snr_db"))
    assert result["declining_metrics"] == ["snr_db"]


def test_custom_threshold():
    cfg = RFTrendConfig(rssi_decline_db=20)
    assert analyze_rf_trends(samples([-60, -61, -62, -68, -69, -70]), cfg)["status"] == "stable"


def test_rejects_unordered_timestamps():
    raw = samples([-60] * 6)
    raw[2]["sampled_at"] = raw[1]["sampled_at"]
    with pytest.raises(ValueError, match="chronological"):
        analyze_rf_trends(raw)


def test_rejects_naive_timestamp():
    raw = samples([-60] * 6)
    raw[0]["sampled_at"] = datetime(2026, 11, 12)
    with pytest.raises(ValueError, match="timezone-aware"):
        analyze_rf_trends(raw)


def test_invalid_configuration():
    with pytest.raises(ValueError):
        RFTrendConfig(rssi_decline_db=float("nan"))
