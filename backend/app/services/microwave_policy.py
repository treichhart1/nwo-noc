"""Validated microwave diagnostic hysteresis; safe to import during settings load."""
from pydantic import Field, model_validator
from app.services.microwave_model import Closed


class MicrowaveThresholds(Closed):
    consecutive_samples: int = Field(3,ge=2,le=20,strict=True)
    stale_after_seconds: int = Field(900,ge=60,le=86400,strict=True)
    max_gap_seconds: int = Field(600,ge=1,le=3600,strict=True)
    rsl_enter_dbm: float = Field(-75,ge=-150,le=20)
    rsl_clear_dbm: float = Field(-72,ge=-150,le=20)
    capacity_enter_mbps: float = Field(100,ge=0,le=1e6)
    capacity_clear_mbps: float = Field(130,ge=0,le=1e6)
    temperature_enter_c: float = Field(75,ge=-100,le=200)
    temperature_clear_c: float = Field(70,ge=-100,le=200)
    availability_enter_percent: float = Field(99,ge=0,le=100)
    availability_clear_percent: float = Field(99.9,ge=0,le=100)
    modulation_enter_bits: int = Field(4,ge=1,le=12)
    modulation_clear_bits: int = Field(6,ge=1,le=12)
    modulation_drop_bits: int = Field(2,ge=1,le=11)
    fade_enter_db: float = Field(10,ge=0,le=100)
    fade_clear_db: float = Field(13,ge=0,le=100)
    errors_enter_per_second: float = Field(10,ge=0,le=1e9)
    errors_clear_per_second: float = Field(5,ge=0,le=1e9)
    error_rate_rise_enter: float = Field(10,gt=0,le=1e9)
    error_rate_rise_clear: float = Field(5,ge=0,le=1e9)
    signal_decline_db: float = Field(6,gt=0,le=100)
    capacity_drop_percent: float = Field(40,gt=0,lt=100)

    @model_validator(mode='after')
    def hysteresis(self):
        for enter,clear in [('rsl_enter_dbm','rsl_clear_dbm'),('capacity_enter_mbps','capacity_clear_mbps'),
                            ('availability_enter_percent','availability_clear_percent'),('modulation_enter_bits','modulation_clear_bits'),('fade_enter_db','fade_clear_db')]:
            if getattr(self,clear)<=getattr(self,enter):raise ValueError('Low-threshold clear must exceed enter')
        if self.temperature_clear_c>=self.temperature_enter_c or self.errors_clear_per_second>=self.errors_enter_per_second:
            raise ValueError('High-threshold clear must be below enter')
        if self.error_rate_rise_clear>=self.error_rate_rise_enter:raise ValueError('Error-rate rise hysteresis is inverted')
        return self
