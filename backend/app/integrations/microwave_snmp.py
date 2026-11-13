"""Prepared microwave MIB contracts. No vendor OIDs/endpoints are guessed."""
import asyncio
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Literal
from pydantic import Field, model_validator
from app.integrations.radio_snmp import MIBField, RadioCollector, PysnmpGET
from app.integrations.read_only import IntegrationFailure
from app.services.microwave_model import (Closed, CarrierMetrics, CarrierObservation, LinkMetrics,
    MicrowaveObservation, EngineeredReference, PROFILES, ENUMS, UNITS)


class MicrowaveField(MIBField):
    unit: Literal['dbm','db','mhz','hz','khz','mbps','bits_per_second','kbps','celsius','percent','seconds','centiseconds','count','enum','version']


def units_for(name):
    if name in ENUMS:return {'enum'}
    if name=='firmware_version':return {'version'}
    if name in {'hardware_id','errors'}:return {'count'}
    if name.endswith('_dbm'):return {'dbm'}
    if name.endswith('_db'):return {'db'}
    if name.endswith('_mhz'):return {'mhz','khz','hz'}
    if name.endswith('_mbps'):return {'mbps','kbps','bits_per_second'}
    if name.endswith('_percent'):return {'percent'}
    if name=='uptime_seconds':return {'seconds','centiseconds'}
    return {'celsius'}


class MicrowaveMapping(Closed):
    profile: Literal['racom-ray3-mib-v1','aviat-11ghz-mib-v1','aviat-18ghz-mib-v1','aviat-multicarrier-mib-v1']
    mib_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    carriers: dict[str,dict[str,MicrowaveField]] = Field(min_length=1,max_length=8)
    link_fields: dict[str,MicrowaveField] = Field(default_factory=dict)
    engineered_references: dict[str,EngineeredReference] = Field(default_factory=dict)

    @property
    def fields(self):
        return {**{f'link.{k}':v for k,v in self.link_fields.items()},
                **{f'{c}.{k}':v for c,fields in self.carriers.items() for k,v in fields.items()}}

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.model_dump(),sort_keys=True,separators=(',',':')).encode()).hexdigest()

    @model_validator(mode='after')
    def valid(self):
        carrier_fields=set(CarrierMetrics.model_fields)|{'radio_status','modulation','adaptive_modulation','errors'}|{k for k in ENUMS if k.startswith('alarm_')}
        link_fields=set(LinkMetrics.model_fields)|{'ethernet_status','firmware_version','hardware_id'}
        if self.profile=='racom-ray3-mib-v1' and len(self.carriers)>2:raise ValueError('Ray3 supports at most two configured channels')
        if any(not re.fullmatch(r'c[1-8]',c) or set(f)-carrier_fields for c,f in self.carriers.items()):raise ValueError('Invalid microwave carrier mapping')
        if set(self.link_fields)-link_fields or set(self.engineered_references)-set(self.carriers):raise ValueError('Invalid link/reference mapping')
        fields=self.fields
        if not 1<=len(fields)<=192 or len({f.oid for f in fields.values()})!=len(fields):raise ValueError('Invalid or duplicate OID set')
        for key,f in fields.items():
            name=key.split('.')[1]
            if f.unit not in units_for(name):raise ValueError('Incompatible microwave unit')
            if any(int(a)>4294967295 for a in f.oid.split('.')):raise ValueError('Invalid OID arc')
            if name in ENUMS:
                if not f.states or not set(f.states.values())<=ENUMS[name] or any(not k.isascii() or not k.isdigit() or len(k)>5 for k in f.states):raise ValueError('Invalid microwave enumeration')
            elif f.states:raise ValueError('Unexpected enumeration')
        return self


class MicrowaveCollector(RadioCollector):
    def __init__(self,mapping,policy,**kwargs):
        if kwargs.get('transport') is None:
            kwargs['transport']=PysnmpGET(version_oids=[f.oid for k,f in mapping.fields.items() if k=='link.firmware_version'])
        super().__init__(mapping,policy,**kwargs)

    def normalize(self,values,*,observed_at,received_at,source_kind='synthetic_export'):
        if not isinstance(values,dict) or len(values)>192:raise IntegrationFailure('invalid_response')
        originals,original_units={},{}
        def normalize_fields(fields,names,prefix,model):
            numeric,enums,missing={},{},{}
            for name in names:
                spec=fields.get(name)
                if spec is None:missing[name]='unsupported';continue
                if spec.oid not in values:missing[name]='missing';continue
                raw=values[spec.oid]
                if raw=='unsupported':missing[name]='unsupported';continue
                try:
                    if name=='firmware_version':
                        if not isinstance(raw,str) or not re.fullmatch(r'\d{1,4}(?:\.\d{1,4}){1,4}(?:-(?:rc|build)\d{1,6})?',raw):raise ValueError()
                        value=raw
                    else:
                        if isinstance(raw,bool) or not isinstance(raw,(int,float)) or not math.isfinite(raw):raise ValueError()
                        if name in ENUMS:
                            if not isinstance(raw,int) or not 0<=raw<=65535:raise ValueError()
                            value=spec.states.get(str(raw))
                            if value is None:raise ValueError()
                        elif name in {'hardware_id','errors'}:
                            if not isinstance(raw,int) or not 0<=raw<=(2**64-1 if name=='errors' else 2**32-1):raise ValueError()
                            value=raw
                        else:value=getattr(model(**{name:raw*UNITS[spec.unit]}),name)
                    if name in model.model_fields:numeric[name]=value
                    else:enums[name]=value
                    originals[f'{prefix}.{name}']=raw;original_units[f'{prefix}.{name}']=spec.unit
                except (ValueError,TypeError,OverflowError):missing[name]='invalid'
            return numeric,enums,missing
        carriers={}
        carrier_names=set(CarrierMetrics.model_fields)|{'radio_status','modulation','adaptive_modulation','errors'}|{k for k in ENUMS if k.startswith('alarm_')}
        for key,fields in self.mapping.carriers.items():
            metrics,enums,missing=normalize_fields(fields,carrier_names,key,CarrierMetrics)
            alarms={k.removeprefix('alarm_'):enums.pop(k) for k in list(enums) if k.startswith('alarm_')}
            carriers[key]=CarrierObservation(measurements=CarrierMetrics(**metrics),unavailable=missing,alarms=alarms,
                engineered_reference=self.mapping.engineered_references.get(key),**enums)
        metrics,extras,missing=normalize_fields(self.mapping.link_fields,set(LinkMetrics.model_fields)|{'ethernet_status','firmware_version','hardware_id'},'link',LinkMetrics)
        try:return MicrowaveObservation(profile=self.mapping.profile,vendor=PROFILES[self.mapping.profile],mapping_digest=self.mapping.digest,
            source_kind=source_kind,timestamp_basis='collector_receipt' if source_kind=='snmpv3_read_only' else 'source_export',
            observed_at=observed_at,received_at=received_at,carriers=carriers,measurements=LinkMetrics(**metrics),
            unavailable=missing,original_values=originals,original_units=original_units,**extras)
        except (ValueError,TypeError):raise IntegrationFailure('invalid_response') from None

    async def collect(self,credentials):
        address=self.policy.validate_connection()
        oids=tuple(f.oid for f in self.mapping.fields.values())
        async def batch():
            values={}
            for offset in range(0,len(oids),32):
                if offset:await self.sleep(.2)
                selected=oids[offset:offset+32]
                for attempt in range(self.policy.retries+1):
                    try:
                        result=await asyncio.wait_for(self.transport.get(address,credentials,selected,self.policy.timeout_seconds),self.policy.timeout_seconds+.5)
                        if not isinstance(result,dict) or set(result)-set(selected):raise IntegrationFailure('invalid_response')
                        values.update(result);break
                    except (TimeoutError,IntegrationFailure) as error:
                        code='timeout' if isinstance(error,TimeoutError) else error.code
                        if code not in {'timeout','unavailable'} or attempt==self.policy.retries:raise IntegrationFailure(code) from None
                        await self.sleep(.2*2**attempt)
            return values
        async with self.lock:
            if self.last_attempt is not None and self.clock()-self.last_attempt<self.policy.min_interval_seconds:raise IntegrationFailure('rate_limited')
            self.last_attempt=self.clock()
            try:values=await asyncio.wait_for(batch(),30)
            except TimeoutError:raise IntegrationFailure('timeout') from None
            except IntegrationFailure:raise
            except Exception:raise IntegrationFailure('unavailable') from None
            now=datetime.now(timezone.utc)
            return self.normalize(values,observed_at=now,received_at=now,source_kind='snmpv3_read_only')
