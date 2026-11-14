"""Bounded, credential-free recorded observations. No probe transport exists here."""

from datetime import datetime
from typing import Literal
from pydantic import Field, model_validator, StrictBool
from app.customers.contracts import SafeModel

SIGNALS = {
    "icmp",
    "snmp",
    "telemetry",
    "interface",
    "ospf",
    "wireless",
    "microwave",
    "fiber",
    "collector",
    "commercial_power",
    "ups",
    "battery",
    "power_supply",
}


class Observation(SafeModel):
    event_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    kind: Literal["device", "site", "link", "sector"]
    target_id: int = Field(ge=1)
    source: Literal["synthetic", "monitoring", "rf"]
    observed_at: datetime
    signals: dict[str, StrictBool | None] = Field(max_length=14)
    uptime_seconds: float | None = Field(None, ge=0, le=315360000, allow_inf_nan=False)
    packet_loss_percent: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)
    latency_ms: float | None = Field(None, ge=0, le=600000, allow_inf_nan=False)
    utilization_percent: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)
    voltage: float | None = Field(None, ge=0, le=1000, allow_inf_nan=False)

    @model_validator(mode="after")
    def safe(self):
        if self.observed_at.tzinfo is None or not set(self.signals) <= SIGNALS:
            raise ValueError("Aware time and registered signal names required")
        if any(v is not None and type(v) is not bool for v in self.signals.values()):
            raise ValueError("Signals must be boolean or unknown")
        return self


class DetectionPolicy(SafeModel):
    confirmation_samples: int = Field(3, ge=2, le=10)
    confirmation_seconds: int = Field(60, ge=15, le=3600)
    recovery_samples: int = Field(3, ge=3, le=10)
    recovery_seconds: int = Field(60, ge=30, le=3600)
    stale_seconds: int = Field(180, ge=30, le=3600)
    correlation_seconds: int = Field(300, ge=30, le=1800)
    independent_signals: int = Field(2, ge=1, le=5)
