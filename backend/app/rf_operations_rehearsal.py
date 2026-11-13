"""Append-only synthetic missing/stale RF fixtures and an assignment-only technician."""
import json,secrets
from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import Device,Site,User
from app.services.radio_history import stage_radio
from app.services.rf_analytics_projection import project_batch
from app.rf_analytics_rehearsal import synthetic_observation


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
        tech=user.id;db.commit()
        while True:
            result=project_batch(db,limit=5000);db.commit()
            if not result['processed']:break
    print(json.dumps({'device_ids':ids,'technician_id':tech,'network_requests':0,'synthetic_export':True},sort_keys=True))

if __name__=='__main__':main()
