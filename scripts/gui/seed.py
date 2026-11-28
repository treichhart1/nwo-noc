"""Add bounded fictional screenshot records to the existing disposable WISP fixture.

Run only via stdin in the private staging CI backend. No transport or provider I/O.
"""
import hashlib
import json
from datetime import date, datetime, timezone
from uuid import uuid4
from sqlalchemy import select, func
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device, User, Site, TelemetrySample
from app.signup import crypto
from app.retention.models import Case, Policy, Competitor, Observation
from app.retention.contracts import PolicyInput
from app.retention.service import configure
from app.cellular.models import Dataset, SiteObservation
from app.cellular.contracts import CellularEvidence
from app.cellular.service import record_evidence
from app.integrations.mikrotik_advanced import AdvancedSnapshot

if not (settings.app_env == 'staging' and settings.offline_mode and settings.synthetic_monitoring_enabled):
    raise SystemExit('GUI fixture requires explicit offline synthetic staging')
with SessionLocal() as db:
    sources=set(db.scalars(select(Device.source)))
    if sources != {'synthetic'} or db.scalar(select(func.count(Device.id))) < 400:
        raise SystemExit('Requires existing isolated 400-device synthetic WISP acceptance data')
    if db.scalar(select(Device.id).where(Device.external_id=='noc135-gui-cellular')):
        raise SystemExit('GUI seed already exists; preserve the original capture database')
    actor=db.scalar(select(User).where(User.username=='acceptance'))
    if not actor or actor.role!='admin':raise SystemExit('Expected synthetic administrator')
    policy=db.scalar(select(Policy).where(Policy.active.is_(True)))
    if not policy:
        configure(db,actor,PolicyInput(text='Synthetic preview policy only. Cancellation requires identity review; Powercode remains authoritative for billing.'))
        policy=db.scalar(select(Policy).where(Policy.active.is_(True)))
    for i,reason in enumerate(['moving','price','reliability','switching_fiber']):
        ref='CAN-GUI-SYNTHETIC-'+str(i+1)
        c=Case(reference=ref,submission_key=str(uuid4()),digest=hashlib.sha256(ref.encode()).hexdigest(),receipt_hash=hashlib.sha256((ref+'-receipt').encode()).hexdigest(),requested_date=date.today(),reported_reason=reason,policy_id=policy.id,policy_ack_at=datetime.now(timezone.utc),equipment_ack=True,status='pending_verification',source='synthetic_gui',retention_state='pending_review')
        crypto.initialize(c,{'customer_name':'Synthetic preview applicant '+str(i+1),'email':'preview'+str(i+1)+'@example.invalid','phone':'+15555550135','service_address':'135 Fictional Test Lane','town':'Synthetic rural Ohio','replacement':{'provider':'Synthetic alternative','technology':'fiber'},'comments':'Fictional preview only; no real request or current competitor pricing.'})
        db.add(c);db.flush()
    competitor=db.scalar(select(Competitor).where(Competitor.name=='Starlink'))
    db.add(Observation(competitor_id=competitor.id,offer={'provider':'Starlink','technology':'satellite','package':'Synthetic customer-reported example; price unavailable','currency':None,'monthly':None},source_kind='customer_reported',source_reference='synthetic-gui-no-verified-price',observed_on=date.today(),verified=False))
    now=datetime.now(timezone.utc)
    site=db.scalar(select(Site).where(Site.latitude.is_not(None)).order_by(Site.id))
    device=Device(source='synthetic',external_id='noc135-gui-cellular',name='Synthetic RB5009 / LTE evidence',enabled=True,wisp_site_id=site.id)
    db.add(device);db.flush()
    dataset=Dataset(digest=hashlib.sha256(b'noc135-gui-cells').hexdigest(),source='synthetic',version='gui-preview-only',attribution='Synthetic fictional regional observations; no vendor dataset redistributed',license='synthetic-only',approval_reference='synthetic-gui-preview',observed_at=now,actor_id=actor.id,row_count=4)
    db.add(dataset);db.flush()
    for i,(lat,lon) in enumerate([(41.08,-84.26),(41.20,-84.40),(40.95,-84.55),(41.31,-84.10)]):
        db.add(SiteObservation(dataset_id=dataset.id,identity='synthetic-gui-cell-'+str(i),kind='estimated_cell',technology='LTE',mcc=310,mnc=260,area='135',cell=str(13500+i),latitude=lat,longitude=lon,observed_at=now,confidence='estimated'))
    record_evidence(db,CellularEvidence(device_id=device.id,observed_at=now,evidence_reference='synthetic-gui-preview',interface_id='*1',registration='registered',source_kind='synthetic',technology='LTE',cell_identity_technology='LTE',cell='13500',mcc=310,mnc=260,area='135',carrier='Synthetic carrier',band='B2',rsrp_dbm=-91.0,rsrq_db=-11.0,sinr_db=17.0),actor.id)
    advanced=AdvancedSnapshot(routeros_major=7,resources={'health':[{'name':'temperature','value':'44','type':'C'}],'connection_tracking':[{'total-entries':'418','max-entries':'262144'}]}).model_dump(mode='json')
    db.add(TelemetrySample(device_id=device.id,sampled_at=now,raw_data={'mikrotik_v1':{'routeros_version':'7.20','uptime_seconds':86400,'advanced':advanced,'interfaces':[]},'gui_evidence':'synthetic-only'}))
    db.commit()
    print(json.dumps({'cellular_device_id':device.id,'source':'synthetic-only','device_count':db.scalar(select(func.count(Device.id))),'datasets':1,'cancellation_requests':4,'external_calls':0}))
