"""Durable, fenced PostgreSQL polling with private replayable normalized outbox."""
import time,uuid
from collections import deque
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from sqlalchemy import select,text,or_,and_
from sqlalchemy.exc import SQLAlchemyError
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device,MonitoringJob as Job,MonitoringAttempt as Attempt,MonitoringReceipt as Receipt,AuditLog
from app.services.monitoring_policy import JobSpec,ReliabilityPolicy,backoff
from app.services.monitoring_collectors import collect,stage,Envelope
from app.services import monitoring_spool as spool
from app.services.monitoring_alerts import process,update_alert,vectors
from app.integrations.read_only import IntegrationFailure
from app.services.rf_analytics_queries import utc

CODES={'success','timeout','unavailable','invalid_response','disabled','configuration_invalid','endpoint_denied','rate_limited','overlap_skipped','collector_failed','spool_unavailable','stale_telemetry','no_telemetry','duplicate_telemetry','lease_expired'}


def audit(db,action,key,now,details=None):db.add(AuditLog(action='monitoring.'+action,entity_type='monitoring_job',entity_id=key,created_at=now,details=details or {}))


def enroll(db,spec,*,now=None):
    spec=JobSpec.model_validate(spec) if isinstance(spec,dict) else spec;now=now or datetime.now(timezone.utc)
    d=db.get(Device,spec.device_id)
    if d is None or not d.enabled:raise ValueError('Enabled inventory device required')
    if spec.kind=='synthetic' and not(d.source=='synthetic' and settings.offline_mode and settings.app_env=='staging' and settings.synthetic_monitoring_enabled):raise ValueError('Synthetic polling requires isolated synthetic staging')
    if spec.kind!='synthetic' and (settings.offline_mode or not settings.integrations_admin_approved):raise ValueError('Live read-only enrollment requires explicit administrator configuration')
    key=f'{spec.kind}:{d.id}';row=db.get(Job,key)
    if row is None:
        row=Job(key=key,device_id=d.id,kind=spec.kind,next_due_at=now,updated_at=now);db.add(row)
    row.enabled=True;row.interval_seconds=spec.interval_seconds;row.timeout_seconds=spec.timeout_seconds;row.lease_seconds=spec.lease_seconds;row.fixture=spec.fixture.model_dump()
    db.flush();audit(db,'job_configured',key,now,{'kind':spec.kind,'interval_seconds':spec.interval_seconds,'read_only':True});return row


def configured(db):
    if settings.offline_mode:return
    from app.radio_poll import private_json
    entries=private_json(settings.rf_scheduler_manifest_file,max_bytes=262144) if settings.rf_scheduler_manifest_file else []
    if not isinstance(entries,list) or len(entries)>1000:raise ValueError('Private monitoring manifest must contain at most 1000 explicit entries')
    specs=[]
    for entry in entries:
        if not isinstance(entry,dict) or set(entry)-{'kind','device_id','config_file','interval_seconds','timeout_seconds','lease_seconds'}:raise ValueError('Invalid monitoring manifest')
        if entry.get('kind') not in {'radio','microwave'}:raise ValueError('Only explicit read-only RF configurations are allowed')
        specs.append(JobSpec(**{k:v for k,v in entry.items() if k!='config_file'}))
    if settings.mikrotik_enabled and settings.mikrotik_device_id:specs.append(JobSpec(kind='mikrotik',device_id=settings.mikrotik_device_id,interval_seconds=max(60,settings.mikrotik_sync_interval_seconds),timeout_seconds=40))
    wanted={f'{s.kind}:{s.device_id}' for s in specs}
    for row in db.scalars(select(Job).where(Job.kind!='synthetic')):
        if row.key not in wanted:row.enabled=False
    for s in specs:
        key=f'{s.kind}:{s.device_id}';row=db.get(Job,key)
        if row is None or row.interval_seconds!=s.interval_seconds or row.timeout_seconds!=s.timeout_seconds or row.lease_seconds!=s.lease_seconds or not row.enabled:enroll(db,s)


def claim(db,worker_id,*,now=None,limit=32):
    now=now or datetime.now(timezone.utc)
    rows=list(db.scalars(select(Job).where(Job.enabled.is_(True),Job.next_due_at<=now,
        or_(Job.state!='leased',Job.lease_until<=now)).order_by(Job.next_due_at,Job.key).limit(min(100,limit)).with_for_update(skip_locked=True)))
    claims=[]
    for row in rows:
        if row.state=='leased' and row.lease_token:
            prior=db.get(Attempt,row.lease_token)
            if prior and prior.result=='running':prior.result='abandoned';prior.finished_at=now
            audit(db,'lease_recovered',row.key,now,{'prior_token':row.lease_token})
        row.slot_at=row.slot_at or now;row.lease_token=str(uuid.uuid4());row.lease_until=now+timedelta(seconds=row.lease_seconds);row.state='leased';row.updated_at=now
        db.add(Attempt(token=row.lease_token,job_key=row.key,worker_id=worker_id,started_at=now))
        claims.append((row.key,row.lease_token))
    db.flush();return claims


def execute(db,key,token,*,now=None,collector=collect,spool_dir=None,crash_after_spool=False):
    frozen=now is not None
    now=now or datetime.now(timezone.utc);root=spool_dir or settings.rf_scheduler_spool_dir;policy=ReliabilityPolicy(**settings.rf_reliability_policy)
    row=db.scalar(select(Job).where(Job.key==key).with_for_update(skip_locked=True))
    if row is None or row.state!='leased' or row.lease_token!=token:return {'result':'fenced','network_requests':0}
    if not row.enabled:return {'result':'disabled','network_requests':0}
    if row.lease_until is None or utc(row.lease_until)<now:return fail(db,row,'lease_expired',now,policy)
    if not db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':871000+row.device_id}):return fail(db,row,'overlap_skipped',now,policy)
    if row.kind=='mikrotik' and not db.scalar(text('SELECT pg_try_advisory_xact_lock(860002)')):return fail(db,row,'overlap_skipped',now,policy)
    existing=db.scalar(select(Receipt).where(Receipt.job_key==key,Receipt.slot_at==row.slot_at))
    if existing:
        # Receipt and job advancement commit atomically; this also tolerates a
        # manual retry after an uncertain client acknowledgment.
        advance(row,now,'success',policy);return {'result':'duplicate_receipt','sample_id':existing.sample_id}
    started=time.perf_counter();replayed=False
    try:
        delivery=spool.pending(root,key,utc(row.slot_at));replayed=delivery is not None
        if delivery is None:
            try:observation=Envelope.model_validate(collector(db,row,now))
            except (SQLAlchemyError,IntegrationFailure):raise
            except Exception:raise IntegrationFailure('sync_failed') from None
            if observation.device_id!=row.device_id:raise IntegrationFailure('endpoint_denied')
            delivery=spool.put(root,spool.Delivery(job_key=key,slot_at=utc(row.slot_at),observation=observation))
        if crash_after_spool:raise SystemExit('Simulated worker interruption after durable handoff')
        with db.begin_nested():
            sample=stage(db,db.get(Device,row.device_id),delivery.observation)
            if sample is None:raise IntegrationFailure('invalid_response')
            completed=now if frozen else datetime.now(timezone.utc)
            age=(completed-delivery.observation.sampled_at).total_seconds()
            sample_vectors=vectors(sample)
            usable=any(any(v is not None for k,v in point['metrics'].items() if k!='uptime') or point.get('ethernet_status') in {'up','down'} or point.get('radio_status')=='down' for point in sample_vectors)
            code='stale_telemetry' if not 0<=age<=policy.stale_seconds else 'success' if usable else 'no_telemetry'
            duplicate=db.scalar(select(Receipt.id).where(Receipt.job_key==key,Receipt.sample_id==sample.id).limit(1))
            if duplicate:code='duplicate_telemetry'
            receipt=Receipt(job_key=key,slot_at=row.slot_at,sample_id=sample.id,accepted_at=completed,replayed=replayed);db.add(receipt)
            process(db,row.device_id,now=completed,policy=policy)
            if code=='success':
                row.successes+=1;row.failures=0;row.last_success_at=completed;row.last_valid_sample_at=delivery.observation.sampled_at
            else:row.failures+=1;row.successes=0
            event=update_alert(db,row.device_id,'collector','collection_failure','clear' if code=='success' and row.successes>=policy.clear_samples else 'unknown' if code=='success' else 'active' if row.failures>=policy.consecutive else 'unknown',
                now=completed,category='monitoring',severity='warning',evidence={'code':code,'root_cause':'collection_or_data_quality_not_RF_outage'})
            attempt=db.get(Attempt,token);attempt.result=code;attempt.finished_at=completed;attempt.duration_ms=(time.perf_counter()-started)*1000
            if replayed:audit(db,'spool_replayed',key,completed,{'sample_id':sample.id})
            if code=='success' and event=='cleared':audit(db,'collector_recovered',key,completed,{'validated_successes':row.successes,'sample_id':sample.id})
            slot=utc(row.slot_at);advance(row,completed,code,policy);row.slot_at=None
            db.flush()
            # Caller commits BEFORE acknowledging the spool. A crash here leaves
            # replayable data and a unique receipt, never a lost queue slot.
            return {'result':code,'sample_id':sample.id,'spool_key':key,'spool_slot':slot,'replayed':replayed}
    except IntegrationFailure as exc:return fail(db,row,exc.code,now,policy,duration=(time.perf_counter()-started)*1000)
    except (ValueError,OSError,TimeoutError):return fail(db,row,'spool_unavailable' if isinstance(__import__('sys').exc_info()[1],OSError) else 'invalid_response',now,policy)


def advance(row,now,code,policy):
    row.state='ready' if code=='success' else 'backoff';row.last_error=None if code=='success' else code
    row.next_due_at=now+timedelta(seconds=row.interval_seconds if code=='success' else max(row.interval_seconds if row.kind!='synthetic' else 0,backoff(row.failures,policy)))
    row.lease_token=None;row.lease_until=None;row.updated_at=now


def fail(db,row,code,now,policy,duration=None):
    code=code if code in CODES else 'collector_failed';token=row.lease_token;row.failures+=1;row.successes=0
    attempt=db.get(Attempt,token)
    if attempt:attempt.result=code;attempt.finished_at=now;attempt.duration_ms=duration
    audit(db,'collector_failed',row.key,now,{'code':code,'consecutive_failures':row.failures})
    process(db,row.device_id,now=now,policy=policy)
    update_alert(db,row.device_id,'collector','collection_failure','active' if row.failures>=policy.consecutive else 'unknown',now=now,category='monitoring',
        evidence={'code':code,'root_cause':'collection_failure_not_RF_outage'})
    advance(row,now,code,policy);db.flush();return {'result':code}


def run_batch(*,worker_id,session_factory=SessionLocal,collector=collect,progress=None,now=None,spool_dir=None):
    if not settings.rf_scheduler_enabled:return {'enabled':False,'claimed':0}
    root=spool_dir or settings.rf_scheduler_spool_dir
    with session_factory() as db:
        spool.reconcile(root,db);configured(db);jobs=claim(db,worker_id,now=now,limit=settings.rf_scheduler_batch_size);db.commit()
    def one(pair):
        with session_factory() as db:
            try:
                result=execute(db,*pair,now=now,collector=collector,spool_dir=root);db.commit()
                if result.get('spool_slot'):spool.acknowledge(root,result['spool_key'],result['spool_slot'])
                return result['result']
            except Exception:
                db.rollback();return 'database_or_delivery_failure' # Static; never log source/configuration/credentials.
    outcomes=[]
    with ThreadPoolExecutor(max_workers=settings.rf_scheduler_concurrency) as pool:
        pending={pool.submit(one,pair) for pair in jobs}
        while pending:
            done,pending=wait(pending,timeout=2,return_when=FIRST_COMPLETED)
            outcomes.extend(f.result() for f in done)
            if progress:progress()
    return {'enabled':True,'claimed':len(jobs),'success':outcomes.count('success'),'results':outcomes}


class Runner:
    """Persistent bounded executor: one slow collector never stalls the fleet."""
    def __init__(self,worker_id,*,session_factory=SessionLocal,collector=collect,spool_dir=None):
        self.worker_id=worker_id;self.factory=session_factory;self.collector=collector;self.root=spool_dir or settings.rf_scheduler_spool_dir
        self.pool=ThreadPoolExecutor(max_workers=settings.rf_scheduler_concurrency);self.pending={};self.completed=deque(maxlen=1000)
    def one(self,pair):
        with self.factory() as db:
            try:
                result=execute(db,*pair,collector=self.collector,spool_dir=self.root);db.commit()
                if result.get('spool_slot'):spool.acknowledge(self.root,result['spool_key'],result['spool_slot'])
                return result['result']
            except Exception:
                db.rollback();return 'database_or_delivery_failure'
    def tick(self):
        if not settings.rf_scheduler_enabled:return 0
        for future in list(self.pending):
            if future.done():
                try:self.completed.append(future.result())
                except Exception:self.completed.append('collector_failed')
                del self.pending[future]
        capacity=settings.rf_scheduler_concurrency-len(self.pending)
        if capacity<=0:return 0
        with self.factory() as db:
            spool.reconcile(self.root,db);configured(db)
            claimed=claim(db,self.worker_id,limit=min(capacity,settings.rf_scheduler_batch_size));db.commit()
        for pair in claimed:self.pending[self.pool.submit(self.one,pair)]=pair
        return len(claimed)
    def close(self):self.pool.shutdown(wait=True)
