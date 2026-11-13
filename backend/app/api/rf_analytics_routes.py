"""Authenticated, PostgreSQL-only read endpoints. No projection or retention writes."""
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Query,Path
from sqlalchemy import select,text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.api.rf_permissions import require_rf_reader
from app.core.database import get_db
from app.core.config import settings
from app.models.entities import Device,Site,NetworkLink,User
from app.services.rf_analytics_policy import AnalyticsPolicy
from app.services.rf_analytics_queries import analytics_detail
from app.services.rf_analytics_overview import analytics_overview

router=APIRouter(prefix='/api/rf/analytics',tags=['Historical RF analytics'])
Window=Literal['1h','24h','7d','30d']
Vendor=Literal['ubiquiti','cambium','racom','aviat','mikrotik','unknown']


def read_analytics(db,operation):
    try:
        if db.get_bind().dialect.name!='postgresql':raise ValueError('PostgreSQL required')
        db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        return operation()
    except LookupError:raise HTTPException(404,'Device not found') from None
    except (SQLAlchemyError,ValueError,TimeoutError):
        db.rollback()
        raise HTTPException(503,'Historical RF analytics unavailable; check projection, database health and query limits. Collection is independent.') from None


@router.get('/overview')
def overview(window: Window='24h',site_id: int|None=Query(None,ge=1),vendor: Vendor|None=None,
             device_id: int|None=Query(None,ge=1),link_id: int|None=Query(None,ge=1),
             offset: int=Query(0,ge=0,le=10000),limit: int=Query(100,ge=1,le=100),
             db: Session=Depends(get_db),_user: User=Depends(require_rf_reader)):
    return read_analytics(db,lambda:analytics_overview(db,window=window,site_id=site_id,vendor=vendor,device_id=device_id,
        link_id=link_id,offset=offset,limit=limit,policy=AnalyticsPolicy(**settings.rf_analytics_policy)))


@router.get('/devices/{device_id}')
def detail(device_id: int=Path(...,ge=1),window: Window='24h',db: Session=Depends(get_db),_user: User=Depends(require_rf_reader)):
    return read_analytics(db,lambda:analytics_detail(db,device_id,window=window,policy=AnalyticsPolicy(**settings.rf_analytics_policy)))


@router.get('/options')
def options(db: Session=Depends(get_db),_user: User=Depends(require_rf_reader)):
    def load():
        devices=list(db.scalars(select(Device).where(Device.enabled.is_(True)).order_by(Device.id).limit(1001)))
        sites=list(db.scalars(select(Site).order_by(Site.id).limit(1001)));links=list(db.scalars(select(NetworkLink).order_by(NetworkLink.id).limit(1001)))
        return {'devices':[{'id':d.id,'name':d.name,'site_id':d.wisp_site_id} for d in devices[:1000]],
            'sites':[{'id':s.id,'name':s.name} for s in sites[:1000]],'links':[{'id':l.id,'name':l.name} for l in links[:1000]],
            'vendors':['ubiquiti','cambium','racom','aviat','mikrotik','unknown'],
            'truncated':any(len(v)>1000 for v in (devices,sites,links)),'read_only':True,'projection_enabled':settings.rf_analytics_projection_enabled}
    return read_analytics(db,load)
