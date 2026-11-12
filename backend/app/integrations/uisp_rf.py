"""Conservative read-only adapter for RF values in UISP device responses.

Only explicit, unit-bearing fields are accepted. Device APIs vary across
firmware and radio families; unknown units must not be guessed.
"""
from typing import Any, Mapping

from app.services.rf_metrics import normalize_rf_metrics


def extract_uisp_rf_metrics(device: Mapping[str, Any]) -> dict[str, float]:
    """Extract explicitly unit-tagged RF telemetry from a UISP device object.

    UISP responses may contain different nested radio data across hardware.
    This adapter deliberately avoids interpreting ambiguous fields such as
    'signal', 'frequency', 'txRate', or 'rxRate' without confirmed units.
    """
    if not isinstance(device, Mapping):
        raise TypeError("UISP device must be a mapping")

    radio = device.get("radio")
    overview = device.get("overview")
    sources = (
        radio if isinstance(radio, Mapping) else {},
        overview if isinstance(overview, Mapping) else {},
    )
    allowed = (
        "rssi_dbm", "signal_dbm", "noise_floor_dbm", "snr_db",
        "ccq_percent", "frequency_mhz", "channel_width_mhz",
        "tx_rate_mbps", "rx_rate_mbps", "temperature_c",
        "uptime_seconds",
    )
    explicit: dict[str, Any] = {}
    for source in sources:
        for field in allowed:
            if field not in explicit and field in source:
                explicit[field] = source[field]
    return normalize_rf_metrics(explicit)
