"""Closed microwave observations: carriers and remote values remain distinct."""
from datetime import timezone
from typing import Literal
import math
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, StrictInt, StrictFloat, StrictStr, model_validator, field_validator

PROFILES = {'racom-ray3-mib-v1':'racom', 'aviat-11ghz-mib-v1':'aviat',
            'aviat-18ghz-mib-v1':'aviat', 'aviat-multicarrier-mib-v1':'aviat'}
MODULATIONS = {'BPSK':1, 'QPSK':2, '16QAM':4, '32QAM':5, '64QAM':6, '128QAM':7,
               '256QAM':8, '512QAM':9, '1024QAM':10, '2048QAM':11, '4096QAM':12}
ALARMS = {'signal_low', 'temperature_high', 'link_loss', 'ethernet_fault', 'hardware_warning'}
ENUMS = {'radio_status':{'up','down','degraded','unknown'}, 'ethernet_status':{'up','down','unknown'},
         'modulation':set(MODULATIONS), 'adaptive_modulation':{'enabled','disabled','unknown'},
         **{f'alarm_{a}':{'active','clear','unknown'} for a in ALARMS}}
UNITS = {'dbm':1, 'db':1, 'mhz':1, 'hz':1e-6, 'khz':.001, 'mbps':1,
         'bits_per_second':1e-6, 'kbps':.001, 'celsius':1, 'percent':1,
         'seconds':1, 'centiseconds':.01, 'count':1, 'enum':1, 'version':1}


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True, allow_inf_nan=False)


class CarrierMetrics(Closed):
    local_rsl_dbm: float | None = Field(None, ge=-150, le=20, strict=True)
    remote_rsl_dbm: float | None = Field(None, ge=-150, le=20, strict=True)
    local_tx_power_dbm: float | None = Field(None, ge=-60, le=60, strict=True)
    remote_tx_power_dbm: float | None = Field(None, ge=-60, le=60, strict=True)
    snr_db: float | None = Field(None, ge=-20, le=100, strict=True)
    frequency_mhz: float | None = Field(None, ge=100, le=100000, strict=True)
    bandwidth_mhz: float | None = Field(None, gt=0, le=5000, strict=True)
    capacity_mbps: float | None = Field(None, ge=0, le=1e6, strict=True)
    tx_throughput_mbps: float | None = Field(None, ge=0, le=1e6, strict=True)
    rx_throughput_mbps: float | None = Field(None, ge=0, le=1e6, strict=True)
    utilization_percent: float | None = Field(None, ge=0, le=100, strict=True)
    temperature_c: float | None = Field(None, ge=-100, le=200, strict=True)
    availability_percent: float | None = Field(None, ge=0, le=100, strict=True)


class EngineeredReference(Closed):
    receiver_threshold_dbm: float = Field(ge=-150, le=0, strict=True)
    reference_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    modulation: Literal['BPSK','QPSK','16QAM','32QAM','64QAM','128QAM','256QAM','512QAM','1024QAM','2048QAM','4096QAM']
    bandwidth_mhz: float = Field(gt=0,le=5000,strict=True)


class CarrierObservation(Closed):
    measurements: CarrierMetrics
    radio_status: Literal['up','down','degraded','unknown'] | None = None
    modulation: Literal['BPSK','QPSK','16QAM','32QAM','64QAM','128QAM','256QAM','512QAM','1024QAM','2048QAM','4096QAM'] | None = None
    adaptive_modulation: Literal['enabled','disabled','unknown'] | None = None
    errors: int | None = Field(None, ge=0, le=2**64-1, strict=True)
    alarms: dict[str, Literal['active','clear','unknown']] = Field(default_factory=dict)
    engineered_reference: EngineeredReference | None = None
    unavailable: dict[str, Literal['missing','unsupported','invalid']] = Field(default_factory=dict)

    @model_validator(mode='after')
    def availability(self):
        fields = set(CarrierMetrics.model_fields) | {'radio_status','modulation','adaptive_modulation','errors'} | {f'alarm_{a}' for a in ALARMS}
        if set(self.alarms)-ALARMS or set(self.unavailable)-fields: raise ValueError('Unknown carrier field')
        for field in fields:
            value = getattr(self.measurements,field) if field in CarrierMetrics.model_fields else self.alarms.get(field[6:]) if field.startswith('alarm_') else getattr(self,field)
            if (value is None) != (field in self.unavailable): raise ValueError('Carrier availability mismatch')
        return self


class LinkMetrics(Closed):
    reported_capacity_mbps: float | None = Field(None, ge=0, le=1e7, strict=True)
    tx_throughput_mbps: float | None = Field(None, ge=0, le=1e7, strict=True)
    rx_throughput_mbps: float | None = Field(None, ge=0, le=1e7, strict=True)
    utilization_percent: float | None = Field(None, ge=0, le=100, strict=True)
    uptime_seconds: float | None = Field(None, ge=0, le=315360000, strict=True)


class MicrowaveObservation(Closed):
    schema_version: Literal['rf_microwave_v1'] = 'rf_microwave_v1'
    profile: Literal['racom-ray3-mib-v1','aviat-11ghz-mib-v1','aviat-18ghz-mib-v1','aviat-multicarrier-mib-v1']
    vendor: Literal['racom','aviat']
    mapping_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_kind: Literal['synthetic_export','snmpv3_read_only']
    timestamp_basis: Literal['source_export','collector_receipt']
    observed_at: AwareDatetime
    received_at: AwareDatetime
    carriers: dict[str, CarrierObservation] = Field(min_length=1,max_length=8)
    measurements: LinkMetrics
    ethernet_status: Literal['up','down','unknown'] | None = None
    firmware_version: str | None = Field(None,pattern=r'^\d{1,4}(?:\.\d{1,4}){1,4}(?:-(?:rc|build)\d{1,6})?$')
    hardware_id: int | None = Field(None,ge=0,le=2**32-1,strict=True)
    unavailable: dict[str, Literal['missing','unsupported','invalid']] = Field(default_factory=dict)
    original_values: dict[str, StrictInt | StrictFloat | StrictStr] = Field(default_factory=dict)
    original_units: dict[str, Literal['dbm','db','mhz','hz','khz','mbps','bits_per_second','kbps','celsius','percent','seconds','centiseconds','count','enum','version']] = Field(default_factory=dict)

    @field_validator('observed_at','received_at')
    @classmethod
    def utc(cls,v): return v.astimezone(timezone.utc)

    @model_validator(mode='after')
    def provenance(self):
        import re
        if PROFILES[self.profile]!=self.vendor: raise ValueError('Microwave vendor/profile mismatch')
        if self.profile=='racom-ray3-mib-v1' and len(self.carriers)>2: raise ValueError('Ray3 contract supports at most two explicit channels')
        if any(not re.fullmatch(r'c[1-8]',key) for key in self.carriers): raise ValueError('Invalid carrier identifier')
        if self.source_kind=='snmpv3_read_only' and (self.timestamp_basis!='collector_receipt' or self.observed_at!=self.received_at): raise ValueError('Invalid receipt timestamp')
        if self.source_kind=='synthetic_export' and self.timestamp_basis!='source_export': raise ValueError('Invalid source timestamp')
        link_fields=set(LinkMetrics.model_fields)|{'ethernet_status','firmware_version','hardware_id'}
        if set(self.unavailable)-link_fields: raise ValueError('Unknown link field')
        values={}
        for name in link_fields:
            value=getattr(self.measurements,name) if name in LinkMetrics.model_fields else getattr(self,name)
            if (value is None)!=(name in self.unavailable): raise ValueError('Link availability mismatch')
            values[f'link.{name}']=value
        for key,carrier in self.carriers.items():
            for name in set(CarrierMetrics.model_fields)|{'radio_status','modulation','adaptive_modulation','errors'}|{f'alarm_{a}' for a in ALARMS}:
                value=getattr(carrier.measurements,name) if name in CarrierMetrics.model_fields else carrier.alarms.get(name[6:]) if name.startswith('alarm_') else getattr(carrier,name)
                values[f'{key}.{name}']=value
        if set(self.original_values)!=set(self.original_units) or set(self.original_values)-set(values): raise ValueError('Unknown original fields or missing units')
        for key,original in self.original_values.items():
            value=values[key];unit=self.original_units[key];name=key.split('.')[1]
            if value is None:raise ValueError('Original missing metric')
            if unit=='enum':
                if not isinstance(original,int) or not 0<=original<=65535:raise ValueError('Invalid original enum')
            elif unit=='version':
                if name!='firmware_version' or original!=value:raise ValueError('Invalid original version')
            else:
                try:valid=isinstance(original,(float,int)) and not isinstance(original,bool) and math.isfinite(original)
                except OverflowError:valid=False
                if not valid:raise ValueError('Invalid original scalar')
                if name in {'errors','hardware_id'}:
                    if unit!='count' or original!=value:raise ValueError('Invalid original count')
                elif not math.isclose(original*UNITS[unit],value,rel_tol=1e-12):raise ValueError('Original value/unit mismatch')
        return self
