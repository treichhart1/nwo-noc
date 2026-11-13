"""Authenticated human recommendations; no device actions or AI network endpoint."""
import re
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Query,Path
from pydantic import BaseModel,Field,field_validator,ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.api.deps import require_operator
from app.core.database import get_db
from app.models.entities import RFRecommendation as Recommendation,RFRecommendationNote as Note,AuditLog
from app.services.rf_troubleshooting import evaluate,transition,serialize
from app.services.rf_explanation import explain,LocalOllamaProvider
from app.services.rf_troubleshooting import audit
from app.core.config import settings

router=APIRouter(prefix='/api/rf/troubleshooting',tags=['RF troubleshooting'])
Status=Literal['new','reviewed','acknowledged','investigating','resolved','closed','dismissed']
class Evaluation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    device_ids:list[int]=Field(min_length=1,max_length=10)
    @field_validator('device_ids')
    @classmethod
    def valid_ids(cls,values):
        if len(set(values))!=len(values) or any(v<1 for v in values):raise ValueError('Distinct positive device IDs required')
        return values
class Workflow(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status:Status
    note:str|None=Field(default=None,max_length=4000)
    resolution:str|None=Field(default=None,max_length=4000)
    @field_validator('note','resolution')
    @classmethod
    def plain_text(cls,value):
        if value is None:return None
        value=value.strip()
        if re.search(r'(?i)(password|token|secret|authorization|api[_ -]?key)\s*[:=]|-----BEGIN .*PRIVATE KEY',value):
            raise ValueError('Do not include credentials in technician notes')
        if any(ord(c)<32 and c not in '\n\t' for c in value):raise ValueError('Control characters not allowed')
        return value or None


def protected(db,operation,*,write=False):
    try:
        result=operation()
        if write:db.commit()
        return result
    except LookupError:
        db.rollback();raise HTTPException(404,'Recommendation or device not found') from None
    except ValueError as exc:
        db.rollback();raise HTTPException(409,str(exc)) from None
    except SQLAlchemyError:
        db.rollback();raise HTTPException(503,'Troubleshooting database unavailable; retry after checking migration and database health. No equipment actions were executed.') from None


def get_row(db,ident,lock=False):
    query=select(Recommendation).where(Recommendation.id==ident)
    row=db.scalar(query.with_for_update() if lock else query)
    if row is None:raise LookupError()
    return row


@router.get('')
def listing(status:Status|None=None,offset:int=Query(0,ge=0,le=10000),limit:int=Query(25,ge=1,le=100),
            db=Depends(get_db),user=Depends(require_operator)):
    def load():
        query=select(Recommendation)
        if status:query=query.where(Recommendation.status==status)
        rows=list(db.scalars(query.order_by(Recommendation.updated_at.desc(),Recommendation.id.desc()).offset(offset).limit(limit+1)))
        return {'items':[serialize(r) for r in rows[:limit]],'has_more':len(rows)>limit,
            'execution_enabled':False,'ai_provider':settings.rf_explanation_provider}
    return protected(db,load)


@router.post('/evaluate')
def evaluate_devices(body:Evaluation,db=Depends(get_db),user=Depends(require_operator)):
    return protected(db,lambda:{'items':[serialize(r) for r in evaluate(db,body.device_ids,user_id=user.id)],'execution_enabled':False},write=True)


@router.get('/{ident}')
def detail(ident:int=Path(...,ge=1),db=Depends(get_db),user=Depends(require_operator)):
    def load():
        row=get_row(db,ident)
        notes=list(db.scalars(select(Note).where(Note.recommendation_id==ident).order_by(Note.id.desc()).limit(101)))
        history=list(db.scalars(select(AuditLog).where(AuditLog.entity_type=='rf_recommendation',AuditLog.entity_id==str(ident))
            .order_by(AuditLog.id.desc()).limit(101)))
        return serialize(row)|{'notes':[{'id':n.id,'body':n.body,'episode':n.episode,'user_id':n.user_id,'at':n.created_at} for n in reversed(notes[:100])],
            'audit':[{'action':a.action,'details':a.details,'user_id':a.user_id,'at':a.created_at} for a in reversed(history[:100])],
            'history_truncated':len(notes)>100 or len(history)>100}
    return protected(db,load)


@router.patch('/{ident}')
def workflow(body:Workflow,ident:int=Path(...,ge=1),db=Depends(get_db),user=Depends(require_operator)):
    def change():
        row=get_row(db,ident,True)
        transition(db,row,user_id=user.id,**body.model_dump())
        return serialize(row)
    return protected(db,change,write=True)


@router.post('/{ident}/explanation')
def explanation(ident:int=Path(...,ge=1),db=Depends(get_db),user=Depends(require_operator)):
    # Only approved loopback explanations. No equipment access or free-form prose.
    def load():
        row=get_row(db,ident)
        enabled=settings.rf_explanation_provider=='local_ollama' and settings.rf_explanation_admin_approved and not settings.offline_mode
        provider=LocalOllamaProvider(settings.rf_explanation_model) if enabled else None
        result=explain(row.evidence,enabled=enabled,provider=provider)
        audit(db,user.id,row,'explanation',{'result':result['status']})
        return result
    return protected(db,load,write=True)
