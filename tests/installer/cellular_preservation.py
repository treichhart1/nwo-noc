"""Synthetic cellular data preservation; no external dataset or device calls."""
SEED='''
from datetime import datetime,timezone
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.entities import User,Device
from app.cellular.models import Dataset,SiteObservation
from app.cellular.contracts import CellularEvidence
from app.cellular.service import record_evidence
with SessionLocal() as db:
 u=db.scalar(select(User).where(User.role=='admin'))
 d=Device(source='synthetic',external_id='noc134-recovery',name='Synthetic cellular recovery',enabled=True)
 db.add(d);db.flush()
 ds=Dataset(digest='134'*21+'1',source='synthetic',version='recovery-fixture',attribution='Synthetic fixture; no vendor data redistributed',license='synthetic-only',approval_reference='synthetic-recovery',observed_at=datetime.now(timezone.utc),actor_id=u.id,row_count=1)
 db.add(ds);db.flush()
 db.add(SiteObservation(dataset_id=ds.id,identity='synthetic-cell',kind='estimated_cell',technology='LTE',mcc=310,mnc=260,area='10',cell='12345',latitude=41.1,longitude=-84.2,observed_at=datetime.now(timezone.utc),confidence='estimated'))
 record_evidence(db,CellularEvidence(device_id=d.id,observed_at=datetime.now(timezone.utc),evidence_reference='synthetic-recovery',interface_id='*1',source_kind='synthetic',technology='LTE',cell_identity_technology='LTE',cell='12345',mcc=310,mnc=260,area='10'),u.id)
 db.commit()
'''
PROBE='''
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.entities import Device,TelemetrySample
from app.cellular.models import Dataset,SiteObservation
with SessionLocal() as db:
 ds=db.scalar(select(Dataset).where(Dataset.version=='recovery-fixture'))
 assert ds and ds.attribution=='Synthetic fixture; no vendor data redistributed'
 assert db.scalar(select(SiteObservation).where(SiteObservation.dataset_id==ds.id)).cell=='12345'
 d=db.scalar(select(Device).where(Device.external_id=='noc134-recovery'))
 assert db.scalar(select(TelemetrySample).where(TelemetrySample.device_id==d.id)).raw_data['cellular_recorded_v1']['cell']=='12345'
print('Cellular dataset attribution and recorded history preserved')
'''
