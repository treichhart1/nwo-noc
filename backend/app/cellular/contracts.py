from datetime import datetime
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,AwareDatetime,model_validator


class Closed(BaseModel):
    model_config=ConfigDict(extra='forbid',hide_input_in_errors=True)


class DatasetInput(Closed):
    source: Literal['opencellid','fcc_asr_reviewed','osm_reviewed']
    version: str=Field(pattern=r'^[A-Za-z0-9_.:-]{1,120}$')
    attribution: str=Field(min_length=10,max_length=500,pattern=r'^[^\x00-\x1f<>]+$')
    license: str=Field(max_length=120)
    approval_reference: str=Field(pattern=r'^[A-Za-z0-9_.:-]{8,120}$')
    observed_at: AwareDatetime
    csv_text: str=Field(min_length=1,max_length=1500000)
    region: Literal['ohio','indiana','ohio_indiana']='ohio_indiana'
    reviewed_license: bool=Field(False,strict=True)
    preview_digest: str|None=Field(None,pattern=r'^[a-f0-9]{64}$')


class CellularEvidence(Closed):
    device_id: int=Field(gt=0,strict=True)
    observed_at: AwareDatetime
    source_kind: Literal['synthetic','operator_recorded']='synthetic'
    evidence_reference: str=Field(pattern=r'^[A-Za-z0-9_.:-]{8,120}$')
    interface_id: str=Field(pattern=r'^\*[0-9A-Fa-f]{1,8}$')
    registration: Literal['registered','not_registered','unknown']='unknown'
    carrier: str|None=Field(None,max_length=80,pattern=r'^[A-Za-z0-9 ._-]+$')
    technology: Literal['LTE','5G_NSA','5G_SA','unknown']='unknown'
    mcc: int|None=Field(None,ge=1,le=999,strict=True)
    mnc: int|None=Field(None,ge=0,le=999,strict=True)
    area: str|None=Field(None,pattern=r'^\d{1,10}$')
    cell: str|None=Field(None,pattern=r'^\d{1,16}$')
    cell_identity_technology: Literal['LTE','NR']|None=None
    enodeb: str|None=Field(None,pattern=r'^\d{1,10}$')
    gnodeb: str|None=Field(None,pattern=r'^\d{1,16}$')
    pci: int|None=Field(None,ge=0,le=1007,strict=True)
    band: str|None=Field(None,pattern=r'^(?:B|n)[0-9]{1,3}$')
    earfcn: int|None=Field(None,ge=0,le=262143,strict=True)
    nr_arfcn: int|None=Field(None,ge=0,le=3279165,strict=True)
    bandwidth_mhz: float|None=Field(None,gt=0,le=1000,strict=True,allow_inf_nan=False)
    rsrp_dbm: float|None=Field(None,ge=-160,le=0,strict=True,allow_inf_nan=False)
    rsrq_db: float|None=Field(None,ge=-50,le=20,strict=True,allow_inf_nan=False)
    rssi_dbm: float|None=Field(None,ge=-160,le=0,strict=True,allow_inf_nan=False)
    sinr_db: float|None=Field(None,ge=-50,le=100,strict=True,allow_inf_nan=False)
    modem_temperature_c: float|None=Field(None,ge=-100,le=200,strict=True,allow_inf_nan=False)
    connection_uptime_seconds: float|None=Field(None,ge=0,le=315360000,strict=True,allow_inf_nan=False)
    @model_validator(mode='after')
    def identity(self):
        if self.cell is not None and self.cell_identity_technology is None:
            raise ValueError('Explicit technology namespace required for full cell identity')
        if self.technology=='LTE' and self.cell_identity_technology not in {None,'LTE'}:
            raise ValueError('LTE/NR identity mismatch')
        if self.technology=='5G_SA' and self.cell_identity_technology not in {None,'NR'}:
            raise ValueError('SA cannot reinterpret LTE identity')
        if self.cell_identity_technology=='LTE' and self.cell is not None and int(self.cell)>=2**28:
            raise ValueError('LTE ECI out of range')
        if self.cell_identity_technology=='NR' and self.cell is not None and int(self.cell)>=2**36:
            raise ValueError('NR NCI out of range')
        return self
