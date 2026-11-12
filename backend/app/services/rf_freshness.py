"""Read-only telemetry freshness classification.

Freshness is independent of signal quality: absent/stale samples must not
be presented as healthy radios.
"""
from datetime import datetime, timezone


def evaluate_rf_freshness(
    last_sampled_at: datetime | None,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = 900,
) -> dict:
    if not 60 <= stale_after_seconds <= 86400:
        raise ValueError("stale_after_seconds must be between 60 and 86400")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if last_sampled_at is None:
        return {"status": "missing", "last_sampled_at": None, "age_seconds": None,
                "stale_after_seconds": stale_after_seconds}
    if last_sampled_at.tzinfo is None or last_sampled_at.utcoffset() is None:
        raise ValueError("last_sampled_at must be timezone-aware")
    age = (current.astimezone(timezone.utc) - last_sampled_at.astimezone(timezone.utc)).total_seconds()
    if age < 0:
        status = "future"
    elif age > stale_after_seconds:
        status = "stale"
    else:
        status = "fresh"
    return {
        "status": status,
        "last_sampled_at": last_sampled_at.astimezone(timezone.utc).isoformat(),
        "age_seconds": round(age),
        "stale_after_seconds": stale_after_seconds,
    }
