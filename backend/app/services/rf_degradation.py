"""Pure, read-only RF degradation evaluation.

Only consecutive, valid samples contribute to a warning. This module does not
create incidents, send alerts, poll devices, or alter radio configuration.
"""
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.services.rf_metrics import normalize_rf_metrics


@dataclass(frozen=True)
class RFThresholds:
    minimum_rssi_dbm: float = -75.0
    minimum_snr_db: float = 20.0
    minimum_ccq_percent: float = 80.0
    consecutive_samples: int = 3

    def __post_init__(self) -> None:
        import math
        if not all(math.isfinite(v) for v in (
            self.minimum_rssi_dbm, self.minimum_snr_db, self.minimum_ccq_percent
        )):
            raise ValueError("RF thresholds must be finite")
        if not -150 <= self.minimum_rssi_dbm <= 20:
            raise ValueError("RSSI threshold outside supported range")
        if not -20 <= self.minimum_snr_db <= 100:
            raise ValueError("SNR threshold outside supported range")
        if not 0 <= self.minimum_ccq_percent <= 100:
            raise ValueError("CCQ threshold outside supported range")
        if not 2 <= self.consecutive_samples <= 20:
            raise ValueError("consecutive_samples must be between 2 and 20")


def evaluate_rf_degradation(
    samples: Sequence[Mapping[str, Any]],
    thresholds: RFThresholds | None = None,
) -> dict[str, Any]:
    """Evaluate ordered oldest-to-newest RF samples.

    A missing or invalid measurement resets the streak for that metric.
    Callers must supply samples in chronological order.
    """
    limits = thresholds or RFThresholds()
    if isinstance(samples, (str, bytes)) or not isinstance(samples, (list, tuple)):
        raise TypeError("samples must be an ordered list or tuple")
    if len(samples) > 10000:
        raise ValueError("too many RF samples")
    checks = {
        "weak_rssi": ("rssi_dbm", limits.minimum_rssi_dbm),
        "low_snr": ("snr_db", limits.minimum_snr_db),
        "low_ccq": ("ccq_percent", limits.minimum_ccq_percent),
    }
    streaks = {name: 0 for name in checks}
    for raw in samples:
        metrics = normalize_rf_metrics(raw)
        for name, (field, minimum) in checks.items():
            value = metrics.get(field)
            streaks[name] = streaks[name] + 1 if value is not None and value < minimum else 0
    reasons = sorted(name for name, count in streaks.items() if count >= limits.consecutive_samples)
    return {
        "degraded": bool(reasons),
        "reasons": reasons,
        "consecutive_counts": streaks,
        "samples_evaluated": len(samples),
    }
