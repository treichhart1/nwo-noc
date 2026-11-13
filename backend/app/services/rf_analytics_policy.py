"""Validated thresholds and known fields. No database or collector imports."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, model_validator

METRICS = tuple('signal_dbm remote_signal_dbm snr_db noise_floor_dbm modulation_bits mcs_index capacity_mbps tx_throughput_mbps rx_throughput_mbps tx_phy_mbps rx_phy_mbps utilization_percent quality_percent temperature_c uptime_seconds frequency_mhz bandwidth_mhz availability_percent'.split())
RATES = ('tx_errors_per_second','rx_errors_per_second','retries_per_second','disconnects_per_second')
WINDOWS = {'1h':(3600,900,60),'24h':(86400,3600,900),'7d':(604800,21600,3600),'30d':(2592000,86400,14400)}


class AnalyticsPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)
    minimum_baseline_samples: int = Field(12,ge=6,le=1000,strict=True)
    minimum_valid_fraction: float = Field(.8,ge=.5,le=1)
    minimum_span_fraction: float = Field(.25,ge=.1,le=1)
    consecutive_samples: int = Field(3,ge=2,le=20,strict=True)
    max_gap_seconds: int = Field(900,ge=60,le=3600,strict=True)
    stale_after_seconds: int = Field(900,ge=60,le=86400,strict=True)
    analysis_timezone: str = Field('America/New_York',max_length=64)
    signal_low: float = Field(-75,ge=-150,le=20)
    signal_clear: float = Field(-72,ge=-150,le=20)
    signal_drop_db: float = Field(6,gt=0,le=100)
    noise_rise_db: float = Field(6,gt=0,le=100)
    snr_drop_db: float = Field(6,gt=0,le=100)
    modulation_drop: float = Field(2,gt=0,le=12)
    capacity_drop_percent: float = Field(40,gt=0,lt=100)
    utilization_enter: float = Field(85,ge=0,le=100)
    utilization_clear: float = Field(75,ge=0,le=100)
    saturation_enter: float = Field(.9,gt=0,le=2)
    saturation_clear: float = Field(.75,ge=0,le=2)
    rate_enter: float = Field(10,gt=0,le=1e9)
    rate_clear: float = Field(5,ge=0,le=1e9)
    minimum_recurring_days: int = Field(3,ge=3,le=15,strict=True)
    minimum_observed_days: int = Field(5,ge=3,le=30,strict=True)
    recurring_fraction: float = Field(.6,ge=.5,le=1)
    score_snr_min: float = Field(20,ge=-20,le=100)
    score_quality_min: float = Field(80,ge=0,le=100)
    score_capacity_min: float = Field(100,ge=0,le=1e6)
    snr_clear: float = Field(23,ge=-20,le=100)
    quality_clear: float = Field(85,ge=0,le=100)
    capacity_clear: float = Field(130,ge=0,le=1e6)
    disconnected_score_cap: float = Field(20,ge=0,le=100)
    weights: dict[str,float] = Field(default_factory=lambda:dict(signal=1,snr=1,quality=1,capacity=1,utilization=1,errors=1,retries=1,connection=2))

    @model_validator(mode='after')
    def validate_policy(self):
        import math
        try:ZoneInfo(self.analysis_timezone)
        except (ZoneInfoNotFoundError,ValueError):raise ValueError('Unknown analytics time zone') from None
        if self.signal_clear<=self.signal_low or self.utilization_clear>=self.utilization_enter or self.saturation_clear>=self.saturation_enter or self.rate_clear>=self.rate_enter:
            raise ValueError('Analytics hysteresis is inverted')
        allowed={'signal','snr','quality','capacity','utilization','errors','retries','connection'}
        if not self.weights or set(self.weights)-allowed or any(not math.isfinite(v) or not 0<v<=100 for v in self.weights.values()):raise ValueError('Invalid score weights')
        if self.minimum_observed_days<self.minimum_recurring_days:raise ValueError('Recurring evidence exceeds observed days')
        if self.snr_clear<=self.score_snr_min or self.quality_clear<=self.score_quality_min or self.capacity_clear<=self.score_capacity_min:
            raise ValueError('Absolute RF clear thresholds must exceed enter thresholds')
        return self
