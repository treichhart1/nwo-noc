"""Read-only, vendor-neutral RF metrics normalization.

This module does not poll devices, change configuration, or persist telemetry.
Unknown or malformed fields are omitted rather than guessed.
"""
from math import isfinite
from typing import Any, Mapping


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def _metric(data: Mapping[str, Any], keys: tuple[str, ...], low: float, high: float) -> float | None:
    for key in keys:
        if key not in data:
            continue
        value = _number(data[key])
        if value is not None and low <= value <= high:
            return value
    return None


def normalize_rf_metrics(payload: Mapping[str, Any]) -> dict[str, float]:
    """Normalize explicitly named RF fields without inferring unavailable values.

    All frequencies are MHz, rates Mbps, RSSI/noise dBm, and SNR dB.
    Vendor-specific conversion belongs in a separate, tested adapter.
    """
    if not isinstance(payload, Mapping):
        raise TypeError("RF telemetry payload must be a mapping")
    specs = {
        "rssi_dbm": (("rssi_dbm", "signal_dbm"), -150, 20),
        "noise_floor_dbm": (("noise_floor_dbm",), -150, 20),
        "snr_db": (("snr_db",), -20, 100),
        "ccq_percent": (("ccq_percent",), 0, 100),
        "frequency_mhz": (("frequency_mhz",), 100, 100000),
        "channel_width_mhz": (("channel_width_mhz",), 1, 5000),
        "link_capacity_mbps": (("link_capacity_mbps",), 0, 1000000),
        "tx_rate_mbps": (("tx_rate_mbps",), 0, 1000000),
        "rx_rate_mbps": (("rx_rate_mbps",), 0, 1000000),
        "temperature_c": (("temperature_c",), -100, 200),
        "uptime_seconds": (("uptime_seconds",), 0, 315360000),
    }
    normalized = {}
    for name, (aliases, minimum, maximum) in specs.items():
        value = _metric(payload, aliases, minimum, maximum)
        if value is not None:
            normalized[name] = value
    return normalized
