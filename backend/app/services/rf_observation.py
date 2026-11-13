"""Versioned, explicit-unit RF observations. Missing values remain None."""
from datetime import datetime,timezone
from typing import Literal
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,model_validator,field_validator

Vendor=Literal['ubiquiti','cambium','racom','aviat','unknown']
Profile=Literal['ubiquiti-uisp-explicit-v1','cambium-unit-export-v1','racom-unit-export-v1',
                'aviat-unit-export-v1','legacy-unit-tagged-v1']


class RFMeasurements(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid',allow_inf_nan=False)
    rssi_dbm: float|None=Field(default=None,ge=-150,le=20,strict=True)
    snr_db: float|None=Field(default=None,ge=-20,le=100,strict=True)
    noise_floor_dbm: float|None=Field(default=None,ge=-150,le=20,strict=True)
    frequency_mhz: float|None=Field(default=None,ge=100,le=100000,strict=True)
    channel_width_mhz: float|None=Field(default=None,ge=1,le=5000,strict=True)
    link_capacity_mbps: float|None=Field(default=None,ge=0,le=1000000,strict=True)
    ccq_percent: float|None=Field(default=None,ge=0,le=100,strict=True)
    tx_rate_mbps: float|None=Field(default=None,ge=0,le=1000000,strict=True)
    rx_rate_mbps: float|None=Field(default=None,ge=0,le=1000000,strict=True)
    temperature_c: float|None=Field(default=None,ge=-100,le=200,strict=True)
    uptime_seconds: float|None=Field(default=None,ge=0,le=315360000,strict=True)


class NormalizedRFTelemetry(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid',hide_input_in_errors=True)
    schema_version: Literal['rf_observation_v1']='rf_observation_v1'
    vendor: Vendor
    profile: Profile
    source_kind: Literal['synthetic_export','uisp_read_only','legacy']
    observed_at: AwareDatetime
    received_at: AwareDatetime|None
    measurements: RFMeasurements
    radio_status: Literal['up','down','degraded','unknown']|None=None
    unavailable: dict[str,Literal['missing','invalid','unsupported']]=Field(default_factory=dict)

    @field_validator('observed_at','received_at')
    @classmethod
    def utc_timestamp(cls,value):
        return value.astimezone(timezone.utc) if value is not None else None

    @model_validator(mode='after')
    def validate_provenance(self):
        expected={'ubiquiti-uisp-explicit-v1':'ubiquiti','cambium-unit-export-v1':'cambium',
            'racom-unit-export-v1':'racom','aviat-unit-export-v1':'aviat','legacy-unit-tagged-v1':'unknown'}
        if expected[self.profile]!=self.vendor:raise ValueError('RF vendor/profile mismatch')
        if (self.source_kind=='legacy') != (self.profile=='legacy-unit-tagged-v1'):raise ValueError('RF source/profile mismatch')
        if self.source_kind!='legacy' and self.received_at is None:raise ValueError('RF receipt timestamp required')
        if self.source_kind=='uisp_read_only' and self.vendor!='ubiquiti':raise ValueError('No approved UISP transport for this profile')
        allowed=set(RFMeasurements.model_fields)|{'radio_status'}
        if set(self.unavailable)-allowed:raise ValueError('Unknown RF availability field')
        for field in self.unavailable:
            value=self.radio_status if field=='radio_status' else getattr(self.measurements,field)
            if value is not None:raise ValueError('RF value conflicts with unavailable marker')
        return self
