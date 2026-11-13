"""Explicit, rebuildable projection of committed telemetry. No collector hooks."""
import hashlib
from datetime import datetime,timezone
from sqlalchemy import select,text
from sqlalchemy.dialects.postgresql import insert
from app.models.entities import Device,NetworkLink,TelemetrySample,RFAnalyticsPoint as Point,RFAnalyticsCheckpoint
from app.services.rf_observation import NormalizedRFTelemetry
from app.services.radio_observation import RadioObservation
from app.services.microwave_model import MicrowaveObservation,MODULATIONS
from app.services.rf_metrics import normalize_rf_metrics

COUNTERS=('tx_errors','rx_errors','retries','disconnects')
VENDORS={'ubiquiti','cambium','racom','aviat','mikrotik','unknown'}


def normalize_source(sample,device,*,known=None,links=None):
    """Return sanitized points, retaining invalid markers to break streaks."""
    raw=sample.raw_data if isinstance(sample.raw_data,dict) else {}
    known=known or {};links=links or {}
    vendor=(device.vendor or '').casefold()
    vendor=vendor if vendor in VENDORS else 'unknown'
    when=sample.sampled_at.replace(tzinfo=timezone.utc) if sample.sampled_at.tzinfo is None else sample.sampled_at.astimezone(timezone.utc)
    base=dict(source_sample_id=sample.id,device_id=device.id,site_id=device.wisp_site_id,
              sampled_at=when,projected_at=datetime.now(timezone.utc),vendor=vendor,link_id=None,valid=True)
    key='rf_microwave_v1' if 'rf_microwave_v1' in raw else 'rf_radio_v2' if 'rf_radio_v2' in raw else 'rf_observation_v1' if 'rf_observation_v1' in raw else 'rf_metrics_v1' if 'rf_metrics_v1' in raw else None
    if key is None:return []
    try:
        if key=='rf_microwave_v1':
            obs=MicrowaveObservation.model_validate(raw[key]);link_id=raw.get('microwave_link_id');link=links.get(link_id)
            if isinstance(link_id,bool) or not link or device.id not in {link.a_device_id,link.z_device_id}:raise ValueError()
            if vendor not in {obs.vendor,'unknown'}:raise ValueError()
            if obs.observed_at!=when:raise ValueError()
            points=[]
            for carrier,c in obs.carriers.items():
                m=c.measurements
                points.append(base|dict(series_key='microwave:'+carrier,vendor=obs.vendor,link_id=link_id,
                    source_profile=obs.profile,mapping_digest=obs.mapping_digest,radio_state=c.radio_status,
                    signal_dbm=m.local_rsl_dbm,remote_signal_dbm=m.remote_rsl_dbm,snr_db=m.snr_db,
                    modulation_bits=MODULATIONS.get(c.modulation),capacity_mbps=m.capacity_mbps,
                    tx_throughput_mbps=m.tx_throughput_mbps,rx_throughput_mbps=m.rx_throughput_mbps,
                    utilization_percent=m.utilization_percent,temperature_c=m.temperature_c,
                    uptime_seconds=obs.measurements.uptime_seconds,frequency_mhz=m.frequency_mhz,
                    bandwidth_mhz=m.bandwidth_mhz,availability_percent=m.availability_percent,tx_errors=c.errors))
            return points
        if key=='rf_radio_v2':
            obs=RadioObservation.model_validate(raw[key]);metrics=obs.measurements.model_dump();counter=obs.counters.model_dump()
            digest=obs.mapping_digest;profile=obs.profile;state=obs.radio_status
            if obs.observed_at!=when or vendor not in {obs.vendor,'unknown'}:raise ValueError()
            vendor=obs.vendor
        elif key=='rf_observation_v1':
            obs=NormalizedRFTelemetry.model_validate(raw[key]);metrics=obs.measurements.model_dump();counter={};state=obs.radio_status
            if obs.observed_at!=when or vendor not in {obs.vendor,'unknown'} and obs.vendor!='unknown':raise ValueError()
            profile=obs.profile;digest=hashlib.sha256(profile.encode()).hexdigest()
            if obs.vendor!='unknown':vendor=obs.vendor
        else:
            metrics=normalize_rf_metrics(raw[key]);counter={};state=None;profile='legacy-unit-tagged-v1'
            if not metrics:raise ValueError()
            digest=hashlib.sha256(profile.encode()).hexdigest();obs=None
        fields={'signal_dbm':'rssi_dbm','remote_signal_dbm':'remote_rssi_dbm','snr_db':'snr_db','noise_floor_dbm':'noise_floor_dbm',
            'mcs_index':'mcs_index','capacity_mbps':'link_capacity_mbps','tx_throughput_mbps':'tx_throughput_mbps','rx_throughput_mbps':'rx_throughput_mbps',
            'tx_phy_mbps':'tx_rate_mbps','rx_phy_mbps':'rx_rate_mbps','quality_percent':'ccq_percent','temperature_c':'temperature_c',
            'uptime_seconds':'uptime_seconds','frequency_mhz':'frequency_mhz','bandwidth_mhz':'channel_width_mhz'}
        fields['utilization_percent']='utilization_percent'
        return [base|dict(series_key='radio',vendor=vendor,source_profile=profile,mapping_digest=digest,radio_state=state,
            modulation_bits=MODULATIONS.get(getattr(obs,'modulation',None)),**{name:metrics.get(src) for name,src in fields.items()},
            **{name:counter.get(name) for name in COUNTERS})]
    except (ValueError,TypeError,KeyError,OverflowError):
        selected={k:v for k,v in known.items() if k.startswith('microwave:')==(key=='rf_microwave_v1')}
        if not selected:selected={'microwave:unknown' if key=='rf_microwave_v1' else 'radio':('invalid','0'*64,None,None,None)}
        return [base|dict(series_key=series,valid=False,source_profile=profile,mapping_digest=digest,link_id=link_id,
                          frequency_mhz=frequency,bandwidth_mhz=bandwidth)
                for series,(profile,digest,link_id,frequency,bandwidth) in selected.items()]


def project_batch(db,*,limit=1000):
    if not 1<=limit<=5000:raise ValueError('Projection batch must be 1–5000 records')
    if db.get_bind().dialect.name!='postgresql':raise ValueError('Analytics projection requires PostgreSQL')
    if not db.scalar(text('SELECT pg_try_advisory_xact_lock(890001)')):return {'status':'overlap_skipped','processed':0,'points':0,'skipped':0}
    checkpoint=db.get(RFAnalyticsCheckpoint,'rf-v1')
    if checkpoint is None:
        checkpoint=RFAnalyticsCheckpoint(name='rf-v1',last_source_id=0,processed=0,skipped=0);db.add(checkpoint);db.flush()
    rows=list(db.scalars(select(TelemetrySample).where(TelemetrySample.id>checkpoint.last_source_id).order_by(TelemetrySample.id).limit(limit)))
    if not rows:return {'status':'current','processed':0,'points':0,'skipped':0,'last_source_id':checkpoint.last_source_id}
    ids={r.device_id for r in rows};devices={d.id:d for d in db.scalars(select(Device).where(Device.id.in_(ids)))}
    links={l.id:l for l in db.scalars(select(NetworkLink).where((NetworkLink.a_device_id.in_(ids))|(NetworkLink.z_device_id.in_(ids))))}
    previous=db.execute(select(Point.device_id,Point.series_key,Point.source_profile,Point.mapping_digest,Point.link_id,Point.frequency_mhz,Point.bandwidth_mhz)
        .where(Point.device_id.in_(ids)).distinct(Point.device_id,Point.series_key)
        .order_by(Point.device_id,Point.series_key,Point.sampled_at.desc(),Point.id.desc())).all()
    known={}
    for d,s,p,m,l,f,b in previous:known.setdefault(d,{})[s]=(p,m,l,f,b)
    points=[];skipped=0
    for row in rows:
        device=devices.get(row.device_id)
        values=normalize_source(row,device,known=known.get(row.device_id),links=links) if device else []
        if not values:skipped+=1
        for value in values:
            known.setdefault(row.device_id,{})[value['series_key']]=(value['source_profile'],value['mapping_digest'],value.get('link_id'),value.get('frequency_mhz'),value.get('bandwidth_mhz'))
        points.extend(values)
    written=0
    # Group dictionaries by column set so executemany never reuses another
    # vendor's missing values as defaults or reports incompatible bind shapes.
    groups={}
    for point in points:groups.setdefault(tuple(sorted(point)),[]).append(point)
    for values in groups.values():
        for offset in range(0,len(values),500):
            result=db.execute(insert(Point).values(values[offset:offset+500]).on_conflict_do_nothing(constraint='uq_rf_analytics_source_series').returning(Point.id))
            written+=len(result.scalars().all())
    checkpoint.last_source_id=rows[-1].id;checkpoint.processed+=len(rows);checkpoint.skipped+=skipped;checkpoint.updated_at=datetime.now(timezone.utc)
    db.flush()
    return {'status':'projected','processed':len(rows),'points':written,'skipped':skipped,'last_source_id':checkpoint.last_source_id}
