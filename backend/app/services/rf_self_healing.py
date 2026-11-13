"""Fail-closed, in-memory RF recovery approval simulation.

No device transports, command execution, database writes, or network access.
A proposal is not an authorization to execute anything.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from secrets import token_urlsafe


class Decision(str, Enum):
    BLOCKED = "blocked"
    PENDING = "pending_approval"
    APPROVED_SIMULATION = "approved_simulation"
    REJECTED = "rejected"


@dataclass(frozen=True)
class RFObservation:
    device_id: int
    checked_at: datetime | None
    status: str
    rssi_dbm: float | None
    snr_db: float | None
    ccq_percent: float | None


@dataclass(frozen=True)
class SimulationProposal:
    proposal_id: str
    device_id: int
    decision: Decision
    reason: str
    recommendation: str
    expires_at: datetime | None


def propose(observation: RFObservation, *, now: datetime | None = None) -> SimulationProposal:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    reason = None
    if observation.device_id < 1:
        reason = "Invalid device identifier"
    elif observation.checked_at is None or observation.checked_at.tzinfo is None:
        reason = "No trustworthy timestamp"
    elif not now - timedelta(minutes=15) <= observation.checked_at <= now + timedelta(minutes=1):
        reason = "RF sample is stale or from the future"
    elif observation.status != "up":
        reason = "Device reachability is not confirmed"
    elif any(v is None for v in (observation.rssi_dbm, observation.snr_db, observation.ccq_percent)):
        reason = "Required RF measurements are missing"
    elif not (-120 <= observation.rssi_dbm <= 0 and -20 <= observation.snr_db <= 80 and 0 <= observation.ccq_percent <= 100):
        reason = "RF measurements are outside supported bounds"
    elif observation.ccq_percent >= 60 and observation.snr_db >= 15:
        reason = "No sustained RF problem established from this sample"
    if reason:
        return SimulationProposal(token_urlsafe(16), observation.device_id, Decision.BLOCKED, reason, "Collect and verify more telemetry; no changes permitted", None)
    return SimulationProposal(
        token_urlsafe(16), observation.device_id, Decision.PENDING,
        "Potential RF degradation; a single sample does not prove root cause",
        "Simulate reviewing channel utilization, neighboring APs, and historical RF measurements; no configuration change",
        now + timedelta(minutes=10),
    )


def decide(proposal: SimulationProposal, *, actor: str, approve: bool, now: datetime | None = None) -> SimulationProposal:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not actor.strip() or proposal.decision != Decision.PENDING or proposal.expires_at is None or now >= proposal.expires_at:
        raise ValueError("Proposal cannot be approved or rejected")
    return SimulationProposal(proposal.proposal_id, proposal.device_id,
                              Decision.APPROVED_SIMULATION if approve else Decision.REJECTED,
                              f"Simulation decision recorded for {actor}; no device action executed",
                              proposal.recommendation, None)
