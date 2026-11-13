"""Explicit staging/query of normalized vendor RF data; no polling or commits."""
from datetime import datetime,timezone
import json
from sqlalchemy import select,or_,text
from app.models.entities import TelemetrySample,Device
from app.services.rf_observation import NormalizedRFTelemetry,RFMeasurements
from app.services.rf_metrics import normalize_rf_metrics
from app.services.rf_freshness import evaluate_rf_freshness


def stage_observation(db,device,observation):
    if device.id is None:raise ValueError('Saved inventory device required')
    if not isinstance(observation,NormalizedRFTelemetry) or observation.source_kind=='legacy':
        raise ValueError('Validated vendor observation required')
    vendor=(device.vendor or '').casefold()
    aliases={'ubiquiti':{'ubiquiti','ubiquiti networks','ubnt'},'cambium':{'cambium','cambium networks'},
             'racom':{'racom'},'aviat':{'aviat','aviat networks'}}
    if vendor and vendor not in aliases[observation.vendor]:raise ValueError('Inventory vendor/profile mismatch')
    measurements=observation.measurements.model_dump(exclude_none=True)
    if not measurements and observation.radio_status is None:return None
    if db.get_bind().dialect.name=='postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':861000+device.id})
    payload=observation.model_dump(mode='json')
    existing=db.scalar(select(TelemetrySample).where(TelemetrySample.device_id==device.id,
        TelemetrySample.sampled_at==observation.observed_at,
        TelemetrySample.raw_data['rf_observation_v1']['profile'].as_string()==observation.profile).limit(1))
    if existing:
        if {k:v for k,v in existing.raw_data['rf_observation_v1'].items() if k!='received_at'}!={k:v for k,v in payload.items() if k!='received_at'}:raise ValueError('Conflicting RF observation at existing timestamp')
        return existing
    raw={'rf_observation_v1':payload}
    # A status-only record must not make legacy RF diagnostics falsely fresh.
    if measurements:raw['rf_metrics_v1']=measurements
    sample=TelemetrySample(device_id=device.id,sampled_at=observation.observed_at,
        signal_dbm=measurements.get('rssi_dbm'),temperature_c=measurements.get('temperature_c'),raw_data=raw)
    db.add(sample);db.flush();return sample


def latest_observation(db,device_id,*,now=None):
    now=now or datetime.now(timezone.utc)
    device=db.get(Device,device_id)
    if device is None:raise LookupError('device_not_found')
    sample=db.scalar(select(TelemetrySample).where(TelemetrySample.device_id==device_id,
        or_(TelemetrySample.raw_data['rf_observation_v1'].is_not(None),
            TelemetrySample.raw_data['rf_radio_v2'].is_not(None),
            TelemetrySample.raw_data['rf_metrics_v1'].is_not(None)))
        .order_by(TelemetrySample.sampled_at.desc(),TelemetrySample.id.desc()).limit(1))
    if sample is None:return {'device_id':device_id,'available':False,'reason':'missing','read_only':True}
    raw=sample.raw_data if isinstance(sample.raw_data,dict) else {}
    timestamp=sample.sampled_at
    if timestamp.tzinfo is None:timestamp=timestamp.replace(tzinfo=timezone.utc)
    try:
        if 'rf_radio_v2' in raw:
            from app.services.radio_observation import RadioObservation
            observation=RadioObservation.model_validate(raw['rf_radio_v2'])
            if observation.observed_at!=timestamp or normalize_rf_metrics(observation.measurements.model_dump(exclude_none=True))!=(raw.get('rf_metrics_v1') or {}):
                raise ValueError('metric mismatch')
        elif 'rf_observation_v1' in raw:
            observation=NormalizedRFTelemetry.model_validate(raw['rf_observation_v1'])
            if observation.observed_at!=timestamp:raise ValueError('timestamp mismatch')
            if observation.measurements.model_dump(exclude_none=True)!=(raw.get('rf_metrics_v1') or {}):
                raise ValueError('metric mismatch')
        else:
            metrics=normalize_rf_metrics(raw.get('rf_metrics_v1',{}))
            if not metrics:raise ValueError('no metrics')
            observation=NormalizedRFTelemetry(vendor='unknown',profile='legacy-unit-tagged-v1',source_kind='legacy',
                observed_at=timestamp,received_at=None,measurements=RFMeasurements(**metrics),
                unavailable={field:'missing' for field in RFMeasurements.model_fields if field not in metrics}|{'radio_status':'unsupported'})
    except (ValueError,TypeError):
        return {'device_id':device_id,'available':False,'reason':'invalid_observation','read_only':True}
    return {'device_id':device_id,'available':True,'read_only':True,
        'freshness':evaluate_rf_freshness(observation.observed_at,now=now),
        'observation':observation.model_dump(mode='json')}
