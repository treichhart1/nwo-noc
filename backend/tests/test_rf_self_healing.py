from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest

from app.services.rf_self_healing import Decision, RFObservation, decide, propose

NOW = datetime(2026, 11, 12, 20, tzinfo=timezone.utc)


def observation(**kwargs):
    base = RFObservation(42, NOW - timedelta(minutes=2), "up", -65, 10, 45)
    return replace(base, **kwargs)


def test_degraded_sample_only_creates_pending_simulation():
    p = propose(observation(), now=NOW)
    assert p.decision == Decision.PENDING
    assert "no configuration change" in p.recommendation
    approved = decide(p, actor="operator", approve=True, now=NOW)
    assert approved.decision == Decision.APPROVED_SIMULATION
    assert "no device action executed" in approved.reason
    with pytest.raises(ValueError):
        decide(approved, actor="operator", approve=True, now=NOW)


@pytest.mark.parametrize("changes", [
    {"checked_at": None}, {"checked_at": NOW - timedelta(minutes=16)},
    {"checked_at": NOW + timedelta(minutes=2)}, {"status": "down"},
    {"ccq_percent": None}, {"snr_db": 100}, {"device_id": 0},
    {"ccq_percent": 90, "snr_db": 30},
])
def test_missing_stale_unhealthy_or_unnecessary_actions_are_blocked(changes):
    p = propose(observation(**changes), now=NOW)
    assert p.decision == Decision.BLOCKED
    with pytest.raises(ValueError):
        decide(p, actor="operator", approve=True, now=NOW)


def test_approval_requires_actor_and_unexpired_pending_proposal():
    p = propose(observation(), now=NOW)
    with pytest.raises(ValueError):
        decide(p, actor=" ", approve=True, now=NOW)
    with pytest.raises(ValueError):
        decide(p, actor="operator", approve=True, now=NOW + timedelta(minutes=11))
    assert decide(p, actor="operator", approve=False, now=NOW).decision == Decision.REJECTED


def test_naive_timestamps_fail_closed():
    assert propose(observation(checked_at=NOW.replace(tzinfo=None)), now=NOW).decision == Decision.BLOCKED
    with pytest.raises(ValueError):
        propose(observation(), now=NOW.replace(tzinfo=None))
