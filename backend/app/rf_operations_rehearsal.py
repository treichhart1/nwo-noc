"""Append-only synthetic missing/stale RF fixtures and an assignment-only technician."""
import json,secrets
from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import Device,Site,User,NetworkLink,TelemetrySample
from app.services.radio_history import stage_radio
from app.services.rf_analytics_projection import project_batch
from app.rf_analytics_rehearsal import synthetic_observation
from app.services.microwave_history import stage_microwave,parse_microwave_row
from app.services.microwave_model import MicrowaveObservation


def append_link_history(db,*,now):
    """Generate explicitly synthetic chart history; never fabricate live history.

    The older microwave fixture has only 12 seconds of samples, which is one
    24h analytics bucket. Preserve it and append separate historical exports.
    """
    if not settings.offline_mode or settings.app_env!='staging' or not settings.synthetic_monitoring_enabled:
        raise ValueError('Explicit offline synthetic staging required')
    if db.scalar(select(Device.id).where(Device.source!='synthetic').limit(1)):raise ValueError('Synthetic inventory only')
    count=0
    links=list(db.scalars(select(NetworkLink).where(NetworkLink.name.like('Synthetic NOC88 %')).order_by(NetworkLink.id)))
    for link in links:
        device=db.get(Device,link.a_device_id)
        if device is None:continue
        row=db.scalar(select(TelemetrySample).where(TelemetrySample.device_id==device.id,
            TelemetrySample.raw_data['rf_microwave_v1'].is_not(None),
            TelemetrySample.raw_data['microwave_link_id'].as_integer()==link.id)
            .order_by(TelemetrySample.sampled_at.desc(),TelemetrySample.id.desc()).limit(1))
        if row is None:continue
        source=parse_microwave_row(row)
        for minutes in (15,30,45,60):
            when=now-timedelta(minutes=minutes,microseconds=1)
            data=source.model_dump();data.update(observed_at=when,received_at=when,source_kind='synthetic_export',timestamp_basis='source_export')
            # Historical export fixture has no independently generated uptime
            # history. Keep it missing instead of inventing uptime/counter rates.
            data['measurements']['uptime_seconds']=None;data['unavailable']['uptime_seconds']='missing'
            data['original_values'].pop('link.uptime_seconds',None);data['original_units'].pop('link.uptime_seconds',None)
            stage_microwave(db,device,link,MicrowaveObservation.model_validate(data));count+=1
    return count


def main():
    if not settings.offline_mode or settings.app_env!='staging' or not settings.synthetic_monitoring_enabled:raise SystemExit('Explicit offline synthetic staging required')
    now=datetime.now(timezone.utc);ids={}
    with SessionLocal() as db:
        if db.scalar(select(Device.id).where(Device.source!='synthetic').limit(1)):raise SystemExit('Synthetic inventory only')
        site=db.scalar(select(Site).order_by(Site.id).limit(1))
        if site is None:raise SystemExit('Existing synthetic WISP site fixture required')
        for kind in ('stale','missing'):
            d=db.scalar(select(Device).where(Device.source=='synthetic',Device.external_id=='noc91-'+kind))
            if d is None:
                d=Device(source='synthetic',external_id='noc91-'+kind,name='NOC91 Synthetic '+kind,vendor='Cambium',
                    wisp_site_id=site.id,enabled=True,authorized=True,monitor_enabled=True);db.add(d);db.flush()
            ids[kind]=d.id
            if kind=='stale':
                at=now-timedelta(hours=2);stage_radio(db,d,synthetic_observation(at,now=at,sequence=1,scenario='healthy'))
                d.monitor_status='up';d.monitor_last_checked_at=at
        user=db.scalar(select(User).where(User.username=='synthetic-field-tech'))
        if user is None:
            user=User(username='synthetic-field-tech',email='synthetic-field-tech@synthetic.invalid',
                password_hash=hash_password(secrets.token_urlsafe(40)),role='operator',is_active=True);db.add(user);db.flush()
        tech=user.id;historical_samples=append_link_history(db,now=now);db.commit()
        while True:
            result=project_batch(db,limit=5000);db.commit()
            if not result['processed']:break
    print(json.dumps({'device_ids':ids,'technician_id':tech,'synthetic_historical_samples':historical_samples,'network_requests':0,'synthetic_export':True},sort_keys=True))

if __name__=='__main__':main()
