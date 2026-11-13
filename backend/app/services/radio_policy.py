"""Validated operator RF diagnostic thresholds; no database imports."""
from pydantic import BaseModel, ConfigDict, Field


class RadioThresholds(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)
    minimum_rssi_dbm: float = Field(-75, ge=-150, le=20)
    minimum_snr_db: float = Field(20, ge=-20, le=100)
    maximum_noise_dbm: float = Field(-85, ge=-150, le=20)
    minimum_capacity_mbps: float = Field(100, ge=0, le=1e6)
    minimum_ccq_percent: float = Field(80, ge=0, le=100)
    signal_drop_db: float = Field(6, gt=0, le=100)
    noise_rise_db: float = Field(6, gt=0, le=100)
    capacity_drop_percent: float = Field(40, gt=0, lt=100)
    consecutive_samples: int = Field(3, ge=2, le=20, strict=True)
    max_gap_seconds: int = Field(600, ge=1, le=3600, strict=True)
    stale_after_seconds: int = Field(900, ge=1, le=86400, strict=True)

