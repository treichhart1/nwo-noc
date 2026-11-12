"""Read-only, bounded historical RF trend evaluation.

Samples are supplied oldest-to-newest, with timestamps in UTC. The analysis
never polls hardware, creates alerts, or modifies persistent state.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Sequence

from app.services.rf_metrics import normalize_rf_metrics


@dataclass(frozen=True)
class RFTrendConfig:
    minimum_samples: int = 6
    minimum_duration_minutes: int = 30
    rssi_decline_db: float = 5.0
    snr_decline_db: float = 4.0
    ccq_decline_percent: float = 10.0

    def __post_init__(self) -> None:
        if not 4 <= self.minimum_samples <= 100:
            raise ValueError("minimum_samples must be between 4 and 100")
        if not 1 <= self.minimum_duration_minutes <= 10080:
            raise ValueError("minimum_duration_minutes out of range")
        if not all(isfinite(v) and v > 0 for v in (
            self.rssi_decline_db, self.snr_decline_db, self.ccq_decline_percent,
        )):
            raise ValueError("decline thresholds must be positive and finite")


def analyze_rf_trends(
    samples: Sequence[Mapping[str, Any]],
    config: RFTrendConfig | None = None,
) -> dict[str, Any]:
    """Compare first and last thirds using medians to suppress isolated spikes.

    Each sample has 'sampled_at' (aware datetime) and 'metrics' (unit-bearing
    RF fields). Gaps or missing values are excluded per metric. This is a
    diagnostic, not a prediction of link failure.
    """
    from statistics import median

    cfg = config or RFTrendConfig()
    if isinstance(samples, (str, bytes)) or not isinstance(samples, (list, tuple)):
        raise TypeError("samples must be a list or tuple")
    if len(samples) > 1000:
        raise ValueError("too many samples")
    ordered = []
    previous = None
    for sample in samples:
        if not isinstance(sample, Mapping):
            raise TypeError("sample must be a mapping")
        when = sample.get("sampled_at")
        if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("sampled_at must be a timezone-aware datetime")
        when = when.astimezone(timezone.utc)
        if previous is not None and when <= previous:
            raise ValueError("samples must be strictly chronological")
        previous = when
        ordered.append((when, normalize_rf_metrics(sample.get("metrics", {}))))

    result = {"status": "insufficient_data", "declining_metrics": [], "metrics": {},
              "samples_evaluated": len(ordered)}
    if len(ordered) < cfg.minimum_samples:
        return result
    duration = (ordered[-1][0] - ordered[0][0]).total_seconds() / 60
    if duration < cfg.minimum_duration_minutes:
        return result
    window = max(2, len(ordered) // 3)
    fields = {
        "rssi_dbm": cfg.rssi_decline_db,
        "snr_db": cfg.snr_decline_db,
        "ccq_percent": cfg.ccq_decline_percent,
    }
    for field, threshold in fields.items():
        first = [metrics[field] for _, metrics in ordered[:window] if field in metrics]
        last = [metrics[field] for _, metrics in ordered[-window:] if field in metrics]
        if len(first) < 2 or len(last) < 2:
            continue
        baseline = float(median(first))
        recent = float(median(last))
        change = round(recent - baseline, 3)
        declining = change <= -threshold
        result["metrics"][field] = {
            "baseline": baseline, "recent": recent, "change": change,
            "declining": declining, "observations": len(first) + len(last),
        }
        if declining:
            result["declining_metrics"].append(field)
    if result["metrics"]:
        result["status"] = "declining" if result["declining_metrics"] else "stable"
    return result
