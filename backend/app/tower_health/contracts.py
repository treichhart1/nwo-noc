"""Explicit-unit recorded health contracts; no network transport or guessed metrics."""

from datetime import datetime, timezone
from typing import Literal
from pydantic import Field, StrictBool, model_validator, field_validator
from app.customers.contracts import SafeModel


class Metrics(SafeModel):
    reachable: StrictBool | None = None
    collector_ok: StrictBool | None = None
    rf_ok: StrictBool | None = None
    uptime_seconds: float | None = Field(
        None, ge=0, le=315360000, allow_inf_nan=False, strict=True
    )
    cpu_percent: float | None = Field(
        None, ge=0, le=100, allow_inf_nan=False, strict=True
    )
    memory_percent: float | None = Field(
        None, ge=0, le=100, allow_inf_nan=False, strict=True
    )
    temperature_c: float | None = Field(
        None, ge=-100, le=200, allow_inf_nan=False, strict=True
    )
    interface_up: StrictBool | None = None
    interface_error_count: int | None = Field(None, ge=0, le=2**64 - 1, strict=True)
    rssi_dbm: float | None = Field(
        None, ge=-150, le=20, allow_inf_nan=False, strict=True
    )
    snr_db: float | None = Field(None, ge=-20, le=100, allow_inf_nan=False, strict=True)
    tx_mbps: float | None = Field(None, ge=0, le=1e7, allow_inf_nan=False, strict=True)
    rx_mbps: float | None = Field(None, ge=0, le=1e7, allow_inf_nan=False, strict=True)
    tx_capacity_mbps: float | None = Field(
        None, ge=0.000001, le=1e7, allow_inf_nan=False, strict=True
    )
    rx_capacity_mbps: float | None = Field(
        None, ge=0.000001, le=1e7, allow_inf_nan=False, strict=True
    )
    reported_capacity_mbps: float | None = Field(
        None, ge=0, le=1e7, allow_inf_nan=False, strict=True
    )
    subscriber_down_capacity_mbps: float | None = Field(
        None, ge=0.000001, le=1e7, allow_inf_nan=False, strict=True
    )
    utilization_percent: float | None = Field(
        None, ge=0, le=100, allow_inf_nan=False, strict=True
    )
    airtime_percent: float | None = Field(
        None, ge=0, le=100, allow_inf_nan=False, strict=True
    )
    subscribers: int | None = Field(None, ge=0, le=100000, strict=True)
    packet_loss_percent: float | None = Field(
        None, ge=0, le=100, allow_inf_nan=False, strict=True
    )
    latency_ms: float | None = Field(
        None, ge=0, le=600000, allow_inf_nan=False, strict=True
    )
    modulation_bits: int | None = Field(None, ge=1, le=16, strict=True)
    tx_phy_rate_mbps: float | None = Field(
        None, ge=0, le=1e7, allow_inf_nan=False, strict=True
    )
    rx_phy_rate_mbps: float | None = Field(
        None, ge=0, le=1e7, allow_inf_nan=False, strict=True
    )


class Power(SafeModel):
    ac_present: StrictBool | None = None
    dc_voltage: float | None = Field(
        None, ge=0, le=1000, allow_inf_nan=False, strict=True
    )
    battery_voltage: float | None = Field(
        None, ge=0, le=1000, allow_inf_nan=False, strict=True
    )
    ups_state: (
        Literal["online", "on_battery", "low_battery", "fault", "unknown"] | None
    ) = None
    charge_state: Literal["charging", "discharging", "idle", "unknown"] | None = None
    remaining_energy_wh: float | None = Field(
        None, ge=0, le=1e7, allow_inf_nan=False, strict=True
    )
    load_watts: float | None = Field(
        None, ge=0, le=1e7, allow_inf_nan=False, strict=True
    )
    reported_runtime_minutes: float | None = Field(
        None, ge=0, le=1e6, allow_inf_nan=False, strict=True
    )
    measured_full_capacity_wh: float | None = Field(
        None, ge=0, le=1e7, allow_inf_nan=False, strict=True
    )
    rated_capacity_wh: float | None = Field(
        None, ge=0.000001, le=1e7, allow_inf_nan=False, strict=True
    )
    supply_ok: StrictBool | None = None


class Observation(SafeModel):
    event_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    site_id: int = Field(ge=1)
    device_id: int = Field(ge=1)
    sector_id: int | None = Field(None, ge=1)
    link_id: int | None = Field(None, ge=1)
    source: Literal[
        "synthetic",
        "recorded_radio",
        "recorded_microwave",
        "recorded_router",
        "recorded_power",
        "recorded_legacy",
    ]
    observed_at: datetime
    metrics: Metrics = Field(default_factory=Metrics)
    power: Power = Field(default_factory=Power)
    unavailable: dict[str, Literal["missing", "unsupported", "invalid"]] = Field(
        default_factory=dict, max_length=40
    )
    counter_scope: str | None = Field(None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("observed_at")
    @classmethod
    def utc(cls, value):
        if value.tzinfo is None:
            raise ValueError("Aware observation time required")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def valid(self):
        if self.observed_at.tzinfo is None or self.sector_id and self.link_id:
            raise ValueError("Aware time and unambiguous resource scope required")
        fields = {**self.metrics.model_dump(), **self.power.model_dump()}
        if set(self.unavailable) - set(fields) or any(
            fields[k] is not None for k in self.unavailable
        ):
            raise ValueError(
                "Unavailable markers must identify absent registered metrics"
            )
        return self


class VoltageProfile(SafeModel):
    low_dc_voltage: float | None = Field(None, gt=0, le=1000, allow_inf_nan=False)
    low_battery_voltage: float | None = Field(None, gt=0, le=1000, allow_inf_nan=False)


class Policy(SafeModel):
    device_voltage_thresholds: dict[int, VoltageProfile] = Field(
        default_factory=dict, max_length=2000
    )
    rssi_warning_dbm: float = Field(-75, ge=-150, le=0, allow_inf_nan=False)
    snr_warning_db: float = Field(15, ge=-20, le=100, allow_inf_nan=False)
    max_age_seconds: int = Field(300, ge=60, le=3600)
    confirmation_samples: int = Field(3, ge=3, le=10)
    confirmation_seconds: int = Field(60, ge=30, le=3600)
    temperature_warning_c: float = Field(75, ge=20, le=150, allow_inf_nan=False)
    temperature_critical_c: float = Field(90, ge=25, le=180, allow_inf_nan=False)
    temperature_hysteresis_c: float = Field(5, ge=1, le=20, allow_inf_nan=False)
    temperature_rise_c: float = Field(5, ge=1, le=30, allow_inf_nan=False)
    utilization_warning_percent: float = Field(85, ge=50, le=99, allow_inf_nan=False)
    utilization_hysteresis_percent: float = Field(10, ge=1, le=30, allow_inf_nan=False)
    low_dc_voltage: float | None = Field(None, gt=0, le=1000, allow_inf_nan=False)
    low_battery_voltage: float | None = Field(None, gt=0, le=1000, allow_inf_nan=False)
    voltage_hysteresis: float = Field(0.5, gt=0, le=20, allow_inf_nan=False)
    battery_health_ratio: float = Field(0.8, gt=0, le=1, allow_inf_nan=False)
    reboot_warning_count: int = Field(3, ge=2, le=20)
    power_transition_warning_count: int = Field(3, ge=2, le=20)
    error_rate_warning_per_second: float = Field(1, gt=0, le=1e6, allow_inf_nan=False)
    provisioned_ratio_warning: float = Field(10, ge=1, le=1000, allow_inf_nan=False)
    raw_retention_days: int = Field(30, ge=7, le=90)
    aggregate_retention_days: int = Field(400, ge=180, le=730)
    customer_max_age_hours: int = Field(48, ge=1, le=168)
    timezone: str = "America/New_York"
    forecast_coverage_fraction: float = Field(0.8, ge=0.8, le=1, allow_inf_nan=False)
    forecast_hours_per_day: int = Field(12, ge=8, le=24)

    @model_validator(mode="after")
    def coherent(self):
        if any(k < 1 for k in self.device_voltage_thresholds):
            raise ValueError("Registered positive device identifiers required")
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(self.timezone)
        except Exception:
            raise ValueError("Known IANA time zone required") from None
        if (
            self.temperature_critical_c <= self.temperature_warning_c
            or self.confirmation_seconds > self.max_age_seconds
        ):
            raise ValueError("Coherent temperature and freshness thresholds required")
        return self
