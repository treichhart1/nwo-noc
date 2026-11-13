"""Prepared vendor RF collectors for explicit-unit exports and guarded UISP reads.

Export profiles are synthetic contracts, NOT claims about vendor wire protocols.
No guessed REST paths, SNMP OIDs, SSH, login POST or radio commands are provided.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
from typing import Mapping
from app.integrations.read_only import ReadClient,UISP_PATHS,IntegrationFailure
from app.integrations.normalization import parse_dt,identifier
from app.services.rf_metrics import normalize_rf_metrics
from app.services.rf_observation import NormalizedRFTelemetry,RFMeasurements


@dataclass(frozen=True)
class RFProfile:
    vendor: str
    name: str
    node: str
    fields: dict[str,tuple[str,float]]
    states: dict[str,str]


PROFILES={
 'ubiquiti':RFProfile('ubiquiti','ubiquiti-uisp-explicit-v1','radio',
    {k:(k,1) for k in RFMeasurements.model_fields},
    {'up':'up','down':'down','degraded':'degraded','unknown':'unknown'}),
 'cambium':RFProfile('cambium','cambium-unit-export-v1','rf',{
    'rssi_dbm':('rssiDbm',1),'snr_db':('snrDb',1),'noise_floor_dbm':('noiseFloorDbm',1),
    'frequency_mhz':('frequencyHz',1e-6),'channel_width_mhz':('channelWidthHz',1e-6),
    'link_capacity_mbps':('capacityBps',1e-6)},
    {'UP':'up','DOWN':'down','DEGRADED':'degraded','UNKNOWN':'unknown'}),
 'racom':RFProfile('racom','racom-unit-export-v1','radio',{
    'rssi_dbm':('rxLevelDbm',1),'snr_db':('snrDb',1),'noise_floor_dbm':('noiseDbm',1),
    'frequency_mhz':('frequencyKhz',.001),'channel_width_mhz':('bandwidthKhz',.001),
    'link_capacity_mbps':('capacityKbps',.001)},
    {'running':'up','down':'down','degraded':'degraded','unknown':'unknown'}),
 'aviat':RFProfile('aviat','aviat-unit-export-v1','link',{
    'rssi_dbm':('rxPowerDbm',1),'snr_db':('snrDb',1),'noise_floor_dbm':('noiseFloorDbm',1),
    'frequency_mhz':('frequencyGhz',1000),'channel_width_mhz':('channelWidthMhz',1),
    'link_capacity_mbps':('capacityMbps',1)},
    {'inService':'up','outOfService':'down','degraded':'degraded','unknown':'unknown'}),
}
STATUS_FIELDS={'ubiquiti':'radio_status','cambium':'linkState','racom':'state','aviat':'operationalStatus'}


class VendorRFCollector:
    def __init__(self,vendor):
        if vendor not in PROFILES:raise IntegrationFailure('unsupported')
        self.profile=PROFILES[vendor]

    def normalize(self,payload,*,observed_at=None,received_at=None,source_kind='synthetic_export'):
        if not isinstance(payload,Mapping):raise IntegrationFailure('invalid_response')
        node=payload.get(self.profile.node)
        if not isinstance(node,Mapping):raise IntegrationFailure('invalid_response')
        observed=observed_at or parse_dt(node.get('sampled_at'))
        received=received_at or datetime.now(timezone.utc)
        if not isinstance(observed,datetime) or observed.tzinfo is None or observed.utcoffset() is None:
            raise IntegrationFailure('invalid_response')
        if not isinstance(received,datetime) or received.tzinfo is None or received.utcoffset() is None:
            raise IntegrationFailure('invalid_response')
        measurements={};unavailable={}
        for target in RFMeasurements.model_fields:
            spec=self.profile.fields.get(target)
            if spec is None:unavailable[target]='unsupported';continue
            field,scale=spec
            if field not in node or node[field] is None:unavailable[target]='missing';continue
            value=node[field]
            if isinstance(value,bool):unavailable[target]='invalid';continue
            try:value=float(value)*scale
            except (ValueError,TypeError,OverflowError):unavailable[target]='invalid';continue
            normalized=normalize_rf_metrics({target:value})
            if target not in normalized:unavailable[target]='invalid';continue
            measurements[target]=normalized[target]
        state_field=STATUS_FIELDS[self.profile.vendor]
        raw_state=node.get(state_field)
        state=self.profile.states.get(raw_state) if isinstance(raw_state,str) else None
        if state is None:unavailable['radio_status']='missing' if raw_state is None else 'invalid'
        try:
            return NormalizedRFTelemetry(vendor=self.profile.vendor,profile=self.profile.name,
                source_kind=source_kind,observed_at=observed,received_at=received,
                measurements=RFMeasurements(**measurements),radio_status=state,unavailable=unavailable)
        except ValueError:raise IntegrationFailure('invalid_response') from None

    def collect_from_uisp(self,client,external_id,*,received_at=None):
        # Only the existing guarded UISP path is approved. Other vendor wire
        # collectors need real schema/permission review; never guess endpoints.
        if self.profile.vendor!='ubiquiti':raise IntegrationFailure('unsupported')
        if not isinstance(client,ReadClient) or client.policy.paths!=UISP_PATHS:
            raise IntegrationFailure('operation_denied')
        try:external_id=identifier(external_id)
        except ValueError:raise IntegrationFailure('invalid_response') from None
        rows=client.pages('/nms/api/v2.1/devices')
        matched=[row for row in rows if isinstance(row,dict) and isinstance(row.get('identification'),dict)
            and row['identification'].get('id')==external_id]
        if len(matched)!=1:raise IntegrationFailure('invalid_response')
        # lastSeen is an inventory timestamp, not a substitute for RF sample time.
        return self.normalize(matched[0],received_at=received_at,source_kind='uisp_read_only')


def profiles_status():
    return [dict(vendor=p.vendor,profile=p.name,prepared=True,enabled=False,read_only=True,
        direct_transport='not_configured',supported_input='guarded UISP explicit fields or synthetic export' if p.vendor=='ubiquiti' else 'explicit-unit export only; wire protocol unverified')
        for p in PROFILES.values()]
