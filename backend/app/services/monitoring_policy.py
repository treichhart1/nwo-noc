from pydantic import BaseModel,ConfigDict,Field,model_validator
from typing import Literal

class ReliabilityPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True,allow_inf_nan=False)
    consecutive:int=Field(3,ge=2,le=20)
    clear_samples:int=Field(3,ge=2,le=20)
    stale_seconds:int=Field(900,ge=15,le=86400)
    max_gap_seconds:int=Field(900,ge=15,le=86400)
    backoff_base:int=Field(5,ge=1,le=3600)
    backoff_cap:int=Field(300,ge=5,le=86400)
    signal_enter:float=Field(-75,ge=-150,le=0)
    signal_clear:float=Field(-72,ge=-150,le=0)
    snr_enter:float=Field(20,ge=-20,le=100)
    snr_clear:float=Field(23,ge=-20,le=100)
    capacity_enter:float=Field(100,ge=0,le=1e6)
    capacity_clear:float=Field(130,ge=0,le=1e6)
    noise_rise_db:float=Field(6,gt=0,le=100)
    modulation_drop_bits:float=Field(2,gt=0,le=12)
    utilization_enter:float=Field(85,ge=0,le=100)
    utilization_clear:float=Field(75,ge=0,le=100)
    temperature_enter:float=Field(80,ge=30,le=180)
    temperature_clear:float=Field(75,ge=20,le=180)
    @model_validator(mode='after')
    def hysteresis(self):
        if not(self.signal_clear>self.signal_enter and self.snr_clear>self.snr_enter and self.capacity_clear>self.capacity_enter and self.utilization_clear<self.utilization_enter and self.temperature_clear<self.temperature_enter and self.backoff_cap>=self.backoff_base):raise ValueError('Invalid monitoring hysteresis/backoff')
        return self

class Fixture(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    mode:Literal['normal','weak','low_snr','interference','fallback','capacity','utilization','outage','carrier_failure','overheat','ethernet','timeout','disconnected','stale','missing','invalid']='normal'
    latency_ms:int=Field(0,ge=0,le=1000)

class JobSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    device_id:int=Field(ge=1)
    kind:Literal['synthetic','radio','microwave','mikrotik']='synthetic'
    interval_seconds:int=Field(60,ge=15,le=86400)
    timeout_seconds:int=Field(10,ge=1,le=120)
    lease_seconds:int=Field(120,ge=15,le=300)
    fixture:Fixture=Field(default_factory=Fixture)
    @model_validator(mode='after')
    def bounded(self):
        if self.lease_seconds<self.timeout_seconds+5:raise ValueError('Lease must exceed collector deadline')
        if self.kind=='mikrotik' and self.timeout_seconds<40:raise ValueError('MikroTik deadline must cover the bounded read-client budget')
        return self

def backoff(failures,policy):return min(policy.backoff_cap,policy.backoff_base*2**min(max(0,failures-1),20))
