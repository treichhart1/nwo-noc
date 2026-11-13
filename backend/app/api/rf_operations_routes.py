"""Unified stored-data operations and shared incident context. No device actions."""
import re
from datetime import datetime,timezone
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,field_validator
from fastapi import APIRouter,Depends,HTTPException,Query,Path
from sqlalchemy import select,text,or_
from sqlalchemy.exc import SQLAlchemyError
from app.api.deps import require_operator
from app.core.database import get_db
from app.models.entities import (Device,NetworkLink,User,Incident,IncidentNote,AuditLog,RFAlertReview)
from app.services.rf_operations import overview,link_detail,incident_out,ACTIVE
from app.services.rf_analytics_queries import utc

router=APIRouter(prefix='/api/rf/operations',tags=['Unified RF operations'])
Window=Literal['1h','24h','7d','30d']
Vendor=Literal['ubiquiti','cambium','racom','aviat','mikrotik','unknown']
Severity=Literal['critical','warning','info']
Status=Literal['active','all','open','acknowledged','investigating','resolved','closed']


def run(db,operation,*,write=False):
    try:
        if db.get_bind().dialect.name!='postgresql':raise ValueError('PostgreSQL is required for RF operations')
        db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        result=operation()
        if write:db.commit()
        return result
    except LookupError:
        db.rollback();raise HTTPException(404,'RF operations record not found') from None
    except ValueError as exc:
        db.rollback();raise HTTPException(409,str(exc)) from None
    except SQLAlchemyError:
        db.rollback();raise HTTPException(503,'RF operations unavailable; check migrations, database and projection health. No equipment requests were made.') from None


@router.get('')
def dashboard(window:Window='24h',site_id:int|None=Query(None,ge=1),vendor:Vendor|None=None,q:str=Query('',max_length=100),
        link_id:int|None=Query(None,ge=1),device_id:int|None=Query(None,ge=1),severity:Severity|None=None,incident_status:Status='active',
        offset:int=Query(0,ge=0,le=10000),limit:int=Query(50,ge=1,le=100),incident_offset:int=Query(0,ge=0,le=10000),
        db=Depends(get_db),user=Depends(require_operator)):
    return run(db,lambda:overview(db,window=window,site_id=site_id,vendor=vendor,q=q,link_id=link_id,device_id=device_id,
        severity=severity,incident_status=incident_status,offset=offset,limit=limit,incident_offset=incident_offset))


@router.get('/sites/{ident}')
def site(ident:int=Path(...,ge=1),window:Window='24h',offset:int=Query(0,ge=0,le=10000),db=Depends(get_db),user=Depends(require_operator)):
    return run(db,lambda:overview(db,site_id=ident,window=window,offset=offset))


@router.get('/links/{ident}')
def link(ident:int=Path(...,ge=1),window:Window='24h',db=Depends(get_db),user=Depends(require_operator)):
    return run(db,lambda:link_detail(db,ident,window=window))


@router.get('/technicians')
def technicians(db=Depends(get_db),user=Depends(require_operator)):
    def load():
        rows=list(db.scalars(select(User).where(User.is_active.is_(True),User.role.in_(('operator','admin'))).order_by(User.id).limit(501)))
        return {'items':[{'id':u.id,'username':u.username} for u in rows[:500]],'truncated':len(rows)>500}
    return run(db,load)


class Review(BaseModel):
    model_config=ConfigDict(extra='forbid')
    alert_key:str=Field(pattern=r'^rf-analytics:(device|link):[1-9][0-9]{0,9}$')
    observed_at:datetime
    operation:Literal['acknowledge','open_incident']
    @field_validator('observed_at')
    @classmethod
    def aware(cls,value):
        if value.tzinfo is None:raise ValueError('An aware source observation timestamp is required')
        return value


@router.post('/alerts/review')
def review(body:Review,db=Depends(get_db),user=Depends(require_operator)):
    def change():
        if not db.scalar(text('SELECT pg_try_advisory_xact_lock(910001)')):raise ValueError('Another alert review is in progress; retry after refreshing')
        kind,raw=body.alert_key.split(':')[1:];ident=int(raw)
        context=overview(db,**({'device_id':ident} if kind=='device' else {'link_id':ident}),limit=100)
        alert=next((a for a in context['alerts']+context['suppressed_alerts'] if a['key']==body.alert_key),None)
        if not alert:raise ValueError('Alert is no longer active in current stored evidence; refresh before reviewing')
        observed=datetime.fromisoformat(alert['observed_at'])
        if utc(body.observed_at)!=observed:raise ValueError('Alert evidence changed; refresh before reviewing')
        now=datetime.now(timezone.utc)
        row=db.get(RFAlertReview,body.alert_key)
        if row is None:row=RFAlertReview(alert_key=body.alert_key,observed_at=observed);db.add(row)
        row.observed_at=observed;row.acknowledged_at=now;row.user_id=user.id
        incident=None
        if body.operation=='open_incident':
            scope=Incident.device_id==ident if kind=='device' else Incident.link_id==ident
            incident=db.scalar(select(Incident).where(scope,Incident.status.in_(ACTIVE)).order_by(Incident.id).limit(1).with_for_update())
            if incident is None:
                if row.incident_id:
                    prior=db.get(Incident,row.incident_id)
                    ended=prior.closed_at or prior.resolved_at if prior else None
                    if ended and observed<=utc(ended):raise ValueError('Revalidate new evidence after the prior investigation before opening another incident')
                device=db.get(Device,ident) if kind=='device' else None
                link=db.get(NetworkLink,ident) if kind=='link' else None
                incident=Incident(title=f'RF investigation: {body.alert_key}',severity=alert['severity'],
                    device_id=device.id if device else None,site_id=device.wisp_site_id if device else link.a_site_id,
                    link_id=link.id if link else None,created_by=user.id,summary='Operator-reviewed RF evidence. Root cause unconfirmed. '+', '.join(alert['patterns']))
                db.add(incident);db.flush()
                db.add(AuditLog(user_id=user.id,action='incident.create',entity_type='incident',entity_id=str(incident.id),
                    details={'source_alert_key':body.alert_key,'observed_at':observed.isoformat(),'execution_enabled':False}))
            row.incident_id=incident.id
        db.add(AuditLog(user_id=user.id,action='rf.alert.'+body.operation,entity_type='rf_alert',entity_id=body.alert_key,
            details={'observed_at':observed.isoformat(),'incident_id':row.incident_id}))
        db.flush()
        return {'alert_key':body.alert_key,'acknowledged_at':row.acknowledged_at,'observed_at':row.observed_at,
            'incident':incident_out(incident) if incident else None,'execution_enabled':False}
    return run(db,change,write=True)


def redact(value):
    if isinstance(value,dict):return {k:('[REDACTED]' if re.search(r'(?i)password|token|secret|authorization|api.?key',k) else redact(v)) for k,v in value.items()}
    if isinstance(value,list):return [redact(v) for v in value]
    if isinstance(value,str):return re.sub(r'(?i)(password|token|secret|authorization|api.?key)\s*[:=]\s*\S+',r'\1=[REDACTED]',value)
    return value


@router.get('/audit')
def audit_history(entity_type:Literal['incident','rf_recommendation','rf_alert'],entity_id:str=Query(...,min_length=1,max_length=120),
        after_id:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=100),db=Depends(get_db),user=Depends(require_operator)):
    def load():
        rows=list(db.scalars(select(AuditLog).where(AuditLog.entity_type==entity_type,AuditLog.entity_id==entity_id,AuditLog.id>after_id)
            .order_by(AuditLog.id).limit(limit+1)))
        return {'items':[{'id':a.id,'action':a.action,'user_id':a.user_id,'at':a.created_at,'details':redact(a.details)} for a in rows[:limit]],
            'has_more':len(rows)>limit,'next_cursor':rows[limit-1].id if len(rows)>=limit else after_id}
    return run(db,load)


@router.get('/incidents/{ident}')
def investigation(ident:int=Path(...,ge=1),notes_after:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_operator)):
    def load():
        row=db.get(Incident,ident)
        if row is None:raise LookupError()
        notes=list(db.scalars(select(IncidentNote).where(IncidentNote.incident_id==ident,IncidentNote.id>notes_after).order_by(IncidentNote.id).limit(101)))
        return {'incident':incident_out(row),'notes':[{'id':n.id,'body':n.body,'user_id':n.user_id,'at':n.created_at} for n in notes[:100]],
            'notes_has_more':len(notes)>100,'next_notes_cursor':notes[99].id if len(notes)>=100 else notes_after}
    return run(db,load)
