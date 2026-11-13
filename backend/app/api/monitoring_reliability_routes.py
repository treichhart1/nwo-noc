from datetime import datetime,timezone
from fastapi import APIRouter,Depends,HTTPException,Query,Path
from sqlalchemy import select,text
from sqlalchemy.exc import SQLAlchemyError
from app.api.deps import require_operator,require_admin
from app.core.database import get_db
from app.core.config import settings
from app.models.entities import MonitoringAlert,Incident,AuditLog
from app.services.monitoring_health import health
from app.services.monitoring_scheduler import enroll
from app.services.monitoring_policy import JobSpec,ReliabilityPolicy
from app.services.rf_analytics_queries import utc
router=APIRouter(prefix='/api/monitoring/reliability',tags=['Monitoring reliability'])

def run(db,fn,write=False):
    try:
        if db.get_bind().dialect.name!='postgresql':raise ValueError('PostgreSQL required')
        db.execute(text("SET LOCAL statement_timeout = '5000ms'"));result=fn()
        if write:db.commit()
        return result
    except LookupError:
        db.rollback();raise HTTPException(404,'Monitoring record not found') from None
    except ValueError as exc:
        db.rollback();raise HTTPException(409,str(exc)) from None
    except SQLAlchemyError:
        db.rollback();raise HTTPException(503,'Monitoring database unavailable; schedules and private outbox are retained for recovery') from None

@router.get('')
def snapshot(offset:int=Query(0,ge=0,le=10000),limit:int=Query(50,ge=1,le=100),db=Depends(get_db),user=Depends(require_operator)):
    return run(db,lambda:health(db,offset=offset,limit=limit)|{'scheduler_enabled':settings.rf_scheduler_enabled})

@router.post('/synthetic-jobs')
def synthetic_job(body:JobSpec,db=Depends(get_db),user=Depends(require_admin)):
    def create():
        if body.kind!='synthetic' or not settings.offline_mode or settings.app_env!='staging' or not settings.synthetic_monitoring_enabled:raise ValueError('This API can configure synthetic staging jobs only; live configuration is private and administrator-managed')
        row=enroll(db,body)
        db.add(AuditLog(user_id=user.id,action='monitoring.operator_configured',entity_type='monitoring_job',entity_id=row.key,details={'synthetic':True}))
        return {'key':row.key,'execution_enabled':False}
    return run(db,create,True)

@router.post('/alerts/{ident}/incident')
def open_incident(ident:int=Path(...,ge=1),db=Depends(get_db),user=Depends(require_operator)):
    def create():
        if not db.scalar(text('SELECT pg_try_advisory_xact_lock(910002)')):raise ValueError('Another monitoring investigation is opening; retry')
        data=health(db);view=next((a for a in data['alerts'] if a['id']==ident),None)
        if view is None:raise LookupError()
        target=view['probable_parent_alert_id'] or ident
        row=db.scalar(select(MonitoringAlert).where(MonitoringAlert.id==target).with_for_update())
        if row is None or row.state!='active':raise ValueError('Only active confirmed-condition evidence can open an investigation; refresh')
        now=datetime.now(timezone.utc);observed=row.evidence.get('observed_at')
        if row.category!='monitoring' and (not observed or not 0<=(now-datetime.fromisoformat(observed)).total_seconds()<=ReliabilityPolicy(**settings.rf_reliability_policy).stale_seconds):raise ValueError('Telemetry is stale or missing; investigate collection health first')
        incident=db.scalar(select(Incident).where(Incident.device_id==row.device_id,Incident.status.in_(('open','acknowledged','investigating'))).order_by(Incident.id).limit(1))
        if incident is None:
            prior=db.get(Incident,row.incident_id) if row.incident_id else None
            ended=prior.closed_at or prior.resolved_at if prior else None
            if ended and (datetime.fromisoformat(observed) if observed else row.updated_at)<=utc(ended):raise ValueError('New evidence is required after the prior investigation closure')
            incident=Incident(title='Monitoring investigation: '+row.rule,severity=row.severity,device_id=row.device_id,
                summary='Operator-opened '+row.category+' evidence; root cause unconfirmed. No equipment action executed.',created_by=user.id)
            db.add(incident);db.flush()
        row.incident_id=incident.id
        original=db.get(MonitoringAlert,ident);original.incident_id=incident.id
        db.add(AuditLog(user_id=user.id,action='incident.monitoring_context',entity_type='incident',entity_id=str(incident.id),details={'monitoring_alert_id':ident,'probable_root_alert_id':target,'category':row.category,'root_cause':'unconfirmed'}))
        return {'incident_id':incident.id,'execution_enabled':False}
    return run(db,create,True)
