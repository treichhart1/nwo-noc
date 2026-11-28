from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, UniqueConstraint, Index, CheckConstraint
from app.core.database import Base
from app.field.models import now


class Dataset(Base):
    __tablename__='cellular_datasets'
    id=Column(Integer,primary_key=True)
    digest=Column(String(64),nullable=False,unique=True)
    source=Column(String(30),nullable=False)
    version=Column(String(120),nullable=False)
    attribution=Column(String(500),nullable=False)
    license=Column(String(120),nullable=False)
    approval_reference=Column(String(120),nullable=False)
    observed_at=Column(DateTime(timezone=True),nullable=False)
    imported_at=Column(DateTime(timezone=True),nullable=False,default=now)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    row_count=Column(Integer,nullable=False)


class SiteObservation(Base):
    __tablename__='cellular_site_observations'
    __table_args__=(UniqueConstraint('dataset_id','identity',name='uq_cellular_dataset_identity'),
        CheckConstraint('latitude >= -90 AND latitude <= 90 AND longitude >= -180 AND longitude <= 180',name='ck_cellular_coordinates'),
        CheckConstraint("kind IN ('estimated_cell','registered_structure','mapped_structure')",name='ck_cellular_kind'),
        Index('ix_cellular_bounds','latitude','longitude'),
        Index('ix_cellular_cell_identity','mcc','mnc','technology','area','cell'))
    id=Column(Integer,primary_key=True)
    dataset_id=Column(Integer,ForeignKey('cellular_datasets.id'),nullable=False,index=True)
    identity=Column(String(180),nullable=False)
    kind=Column(String(30),nullable=False)
    technology=Column(String(20),nullable=True)
    mcc=Column(Integer,nullable=True)
    mnc=Column(Integer,nullable=True)
    area=Column(String(30),nullable=True)
    cell=Column(String(30),nullable=True)
    latitude=Column(Float,nullable=False)
    longitude=Column(Float,nullable=False)
    uncertainty_m=Column(Float,nullable=True)
    observed_at=Column(DateTime(timezone=True),nullable=False,index=True)
    confidence=Column(String(20),nullable=False)
    band=Column(String(20),nullable=True)
