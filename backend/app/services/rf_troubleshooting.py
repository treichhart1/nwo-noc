"""Bounded explicit evaluation of stored evidence. Never imports a collector."""
from datetime import datetime,timezone
from copy import deepcopy
from sqlalchemy import select,text,or_
from app.models.entities import (Device,NetworkLink,Incident,TelemetrySample,MonitorCheck,AuditLog,
    RFRecommendation as Recommendation,RFRecommendationNote as Note)
from app.services.rf_analytics_queries import analytics_detail,utc
from app.services.rf_analytics_policy import AnalyticsPolicy
from app.services.microwave_history import microwave_history
from app.services.rf_troubleshooting_rules import (candidate,series_rules,priority,MICRO_RULES,finite)
from app.core.config import settings

TERMINAL={'closed','dismissed','resolved'}
TRANSITIONS={'new':{'reviewed','acknowledged','investigating','dismissed'},
 'reviewed':{'acknowledged','investigating','resolved','dismissed'},
 'acknowledged':{'investigating','resolved','dismissed'},'investigating':{'resolved','dismissed'},
 'resolved':{'closed','investigating'},'closed':set(),'dismissed':set()}


def audit(db,user_id,row,action,details=None):
    db.add(AuditLog(user_id=user_id,action='rf.troubleshooting.'+action,entity_type='rf_recommendation',
        entity_id=str(row.id),details={'episode':row.episode,**(details or {})}))


def fact(source,identifier,at,metric,value):
    return {'id':f'{source}:{identifier}:{metric}','source':source,'source_id':identifier,
        'at':utc(at).isoformat(),'metric':metric,'value':value}


def descendants(devices,root):
    found=set();front={root}
    while front:
        next_ids={d.id for d in devices if d.parent_device_id in front}-{root}-found
        found.update(next_ids);front=next_ids
    return sorted(found)


def fresh_check(check,now):
    return bool(check and check.status in {'up','down'} and 0<=(now-utc(check.checked_at)).total_seconds()<=900)


def evidence_for_device(db,device,devices,checks,now):
    """Return candidates plus explicitly healthy scopes; absence is never recovery."""
    report=analytics_detail(db,device.id,now=now,policy=AnalyticsPolicy(**settings.rf_analytics_policy))
    candidates=[];healthy=set()
    for s in report['series']:
        scope=s['series_key'];a=s['analysis']
        refs=s['source_references']
        facts=[fact('telemetry_samples',refs['latest_sample_id'],datetime.fromisoformat(s['last_sampled_at']),k,v)
            for k,v in s['latest'].items() if finite(v) or k=='radio_state' and v in {'up','down','degraded'}]
        history={'baseline':s['baseline'],'deviations':a['evidence'],'patterns':a['patterns'],
            'source_references':refs,'mapping_digest':s['mapping_digest'],'window':'24h',
            'series_key':scope,'last_sampled_at':s['last_sampled_at'],
            'context':[s['mapping_digest'],s['latest'].get('frequency_mhz'),s['latest'].get('bandwidth_mhz'),s['projected_site_id'],s['link_id']]}
        for rule in series_rules(s,temperature_warning_c=settings.rf_troubleshooting_temperature_warning_c):
            candidates.append(candidate(rule,scope=scope,facts=facts,history=history,
                confidence=a['confidence']['level'],missing=a['missing_expected_metrics'] or None))
        if a['state']=='observed_stable':
            healthy.update((scope,r,tuple(history['context'])) for r in ('weak_signal','low_snr','interference','modulation','congestion','data_quality'))
            if s['latest'].get('radio_state')=='up':healthy.add((scope,'disconnect',tuple(history['context'])))
            temp=s['latest'].get('temperature_c')
            if finite(temp) and temp<=settings.rf_troubleshooting_temperature_warning_c-5:healthy.add((scope,'hardware',tuple(history['context'])))
    if not report['series'] and (device.vendor or '').lower()!='mikrotik':
        candidates.append(candidate('data_quality',scope='radio',facts=[],missing=['Projected RF history and current, supported measurements']))
    if (device.vendor or '').lower() in {'racom','aviat'}:
        micro=microwave_history(db,device.id,now=now);diag=micro['diagnostics']
        if micro['association_valid'] and diag['freshness']['status']=='fresh':
            sample=db.scalar(select(TelemetrySample).where(TelemetrySample.device_id==device.id,
                TelemetrySample.raw_data['rf_microwave_v1'].is_not(None)).order_by(TelemetrySample.sampled_at.desc(),TelemetrySample.id.desc()).limit(1))
            # Typed parser in microwave_history validated these values; never use originals.
            latest=micro['samples'][-1] if micro['samples'] else {}
            mfacts=[]
            for key,c in latest.get('carriers',{}).items():
                for metric,value in c['measurements'].items():
                    if finite(value):mfacts.append(fact('telemetry_samples',sample.id,sample.sampled_at,f'{key}.{metric}',value))
                for alarm,state in c.get('alarms',{}).items():
                    if state in {'active','clear'}:mfacts.append(fact('telemetry_samples',sample.id,sample.sampled_at,f'{key}.alarm_{alarm}',state))
                if c['radio_status'] in {'up','down','degraded'}:mfacts.append(fact('telemetry_samples',sample.id,sample.sampled_at,f'{key}.radio_status',c['radio_status']))
            for key,c in diag['carriers'].items():
                for reason in c['reasons']:
                    if reason in MICRO_RULES:candidates.append(candidate(MICRO_RULES[reason],scope='microwave:'+key,facts=mfacts,
                        history={'conditions':c['conditions'],'reported_alarm_names':sorted(c['reported_alarms']),'link_ids':[l['id'] for l in micro['topology']]},confidence='medium'))
                if c['state']=='healthy':healthy.update(('microwave:'+key,r) for r in MICRO_RULES.values())
            if diag['rf_state']=='down':candidates.append(candidate('outage',scope='microwave',facts=mfacts,confidence='medium'))
            if diag['ethernet_state'] in {'down','conflicting'}:
                ef=fact('telemetry_samples',sample.id,sample.sampled_at,'ethernet_state',diag['ethernet_state'])
                candidates.append(candidate('ethernet' if diag['ethernet_state']=='down' else 'data_quality',scope='ethernet',facts=[ef],confidence='medium'))
            if diag['state']=='healthy':healthy.update({('microwave','outage'),('microwave','data_quality'),('ethernet','ethernet'),('ethernet','data_quality')})
        else:candidates.append(candidate('data_quality',scope='microwave',facts=[],missing=['Fresh validated microwave evidence and valid link association']))
    check=checks.get(device.id)
    if (device.vendor or '').lower()=='mikrotik':
        row=db.scalar(select(TelemetrySample).where(TelemetrySample.device_id==device.id,
            TelemetrySample.raw_data['mikrotik_v1'].is_not(None)).order_by(TelemetrySample.sampled_at.desc(),TelemetrySample.id.desc()).limit(1))
        mfacts=[];rules=set()
        if row and 0<=(now-utc(row.sampled_at)).total_seconds()<=900:
            raw=row.raw_data['mikrotik_v1']
            if isinstance(raw,dict):
                for k,threshold in [('cpu_percent',90),('ram_percent',90),('temperature_c',settings.rf_troubleshooting_temperature_warning_c)]:
                    v=raw.get(k)
                    if finite(v) and 0<=v<=200:
                        mfacts.append(fact('telemetry_samples',row.id,row.sampled_at,k,v))
                        if v>threshold:rules.add('hardware')
                interfaces=raw.get('interfaces',[])
                for interface in interfaces[:256] if isinstance(interfaces,list) else []:
                    if not isinstance(interface,dict):continue
                    # Validated deltas, never cumulative counters, support network investigation.
                    for k in ('rx_errors_delta','tx_errors_delta','rx_drops_delta','tx_drops_delta'):
                        v=interface.get(k)
                        if finite(v) and v>=0:
                            mfacts.append(fact('telemetry_samples',row.id,row.sampled_at,k,v))
                            if v>0 and not raw.get('counter_reset'):rules.add('ethernet')
                neighbors=raw.get('ospf_neighbors',[])
                if isinstance(neighbors,list):
                    states=[n.get('state') for n in neighbors[:256] if isinstance(n,dict)]
                    if states and all(v in {'Full','full','Down','down','Init','init','2-Way','2-way'} for v in states):
                        count=sum(v.lower()!='full' for v in states)
                        mfacts.append(fact('telemetry_samples',row.id,row.sampled_at,'ospf_non_full_neighbors',count))
                        if count:rules.add('ethernet')
            for rule in rules:candidates.append(candidate(rule,scope='network',facts=mfacts,confidence='low',
                missing=['Consecutive corroborating network samples and model-specific limits']))
        else:candidates.append(candidate('data_quality',scope='network',facts=[],missing=['Fresh MikroTik read-only stored measurements']))
    child_ids=descendants(devices,device.id)
    offline=[i for i in child_ids if fresh_check(checks.get(i),now) and checks[i].status=='down']
    if fresh_check(check,now) and check.status=='down' and offline:
        facts=[fact('monitor_checks',checks[i].id,checks[i].checked_at,'status','down') for i in [device.id,*offline]]
        candidates.append(candidate('upstream',scope='network',facts=facts,confidence='medium',
            history={'simultaneously_observed_offline_device_ids':offline},missing=['Independent upstream/power confirmation; shared monitoring failure excluded']))
    elif fresh_check(check,now) and check.status=='up' and child_ids and all(fresh_check(checks.get(i),now) and checks[i].status=='up' for i in child_ids):
        healthy.add(('network','upstream'))
    # Fresh disagreement remains explicit, never erased or presented as RF root cause.
    radio_up=any(s['analysis']['freshness']=='fresh' and s['latest'].get('radio_state')=='up' for s in report['series'])
    if radio_up and fresh_check(check,now) and check.status=='up':healthy.add(('conflict','data_quality'))
    if radio_up and fresh_check(check,now) and check.status=='down':
        candidates.append(candidate('data_quality',scope='conflict',facts=[fact('monitor_checks',check.id,check.checked_at,'status',check.status)],
            missing=['Explain fresh radio-up versus independent reachability-down observations']))
        healthy=set()
    unique={(c['scope'],c['rule']):c for c in candidates}
    return list(unique.values()),healthy,child_ids


def evaluate(db,ids,*,user_id,now=None):
    if not 1<=len(ids)<=10 or len(set(ids))!=len(ids):raise ValueError('Select 1–10 distinct devices')
    if db.get_bind().dialect.name!='postgresql':raise ValueError('PostgreSQL required')
    if not db.scalar(text('SELECT pg_try_advisory_xact_lock(900001)')):raise ValueError('Another evaluation is in progress; retry later')
    db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
    now=now or datetime.now(timezone.utc)
    devices=list(db.scalars(select(Device).order_by(Device.id).limit(10001)))
    if len(devices)>10000:raise ValueError('Topology exceeds evaluation bound; narrow inventory before evaluation')
    by_id={d.id:d for d in devices}
    if any(i not in by_id for i in ids):raise LookupError('Device not found')
    # DISTINCT ON: indexed local histories; no live polling or integration state writes.
    checks={c.device_id:c for c in db.scalars(select(MonitorCheck).where(MonitorCheck.source.in_(['tcp','synthetic'])).distinct(MonitorCheck.device_id)
        .order_by(MonitorCheck.device_id,MonitorCheck.checked_at.desc(),MonitorCheck.id.desc()))}
    links=list(db.scalars(select(NetworkLink).limit(10001)))
    if len(links)>10000:raise ValueError('Topology exceeds evaluation bound')
    output=[]
    for ident in ids:
        d=by_id[ident];items,healthy,child_ids=evidence_for_device(db,d,devices,checks,now)
        linked=[l.id for l in links if ident in {l.a_device_id,l.z_device_id}]
        incidents=list(db.scalars(select(Incident.id).where(Incident.status.not_in(['resolved','closed']),
            or_(Incident.device_id==ident,Incident.link_id.in_(linked))).limit(101)))
        existing={r.problem_key:r for r in db.scalars(select(Recommendation).where(Recommendation.device_id==ident).with_for_update())}
        active=set()
        for item in items:
            key=f"device:{ident}:{item['scope']}:{item['rule']}";active.add(key);row=existing.get(key)
            if row is None:
                row=Recommendation(problem_key=key,device_id=ident,rule=item['rule'],evidence={},
                    first_seen_at=now,episode_started_at=now,updated_at=now);db.add(row);db.flush();audit(db,user_id,row,'created')
            elif row.recovery_observed_at:
                row.episode+=1;row.status='new';row.resolution=None;row.episode_started_at=now
                row.recovery_observed_at=None;audit(db,user_id,row,'recurred')
            age=max(0,(now-utc(row.episode_started_at)).total_seconds())
            item['inventory']={'device_id':ident,'site_id':d.wisp_site_id,'link_ids':linked,
                'potential_downstream_device_ids':child_ids,'active_incident_ids':incidents[:100],
                'incident_results_truncated':len(incidents)>100}
            item['priority']=priority(item['rule'],duration_seconds=age,recurrences=row.episode-1,
                downstream_devices=len(child_ids),confidence=item['confidence'],incident_ids=incidents)
            row.evidence=deepcopy(item);row.evidence_state='active';row.updated_at=now
            audit(db,user_id,row,'evaluated',{'rule':row.rule,'evidence_state':'active'});output.append(row)
        for key,row in existing.items():
            if key in active:continue
            scope=row.evidence['scope']
            # Only corroborated full-scope health may clear; missing data isn't recovery.
            context=row.evidence.get('history',{}).get('context')
            cleared=(scope,row.rule,tuple(context)) in healthy if context else (scope,row.rule) in healthy
            if row.rule=='data_quality' and scope=='radio' and not context:
                cleared=any(h[0]==scope and h[1]=='data_quality' for h in healthy)
            state='cleared' if cleared else 'unknown'
            if row.evidence_state!=state:
                row.evidence_state=state;row.updated_at=now
                if cleared:row.recovery_observed_at=now
                audit(db,user_id,row,'evidence_changed',{'evidence_state':state})
            output.append(row)
    db.flush();return output


def transition(db,row,*,user_id,status,note=None,resolution=None):
    if status!=row.status and status not in TRANSITIONS[row.status]:raise ValueError('Unsupported workflow transition')
    if status in {'resolved','closed'} and not (resolution or row.resolution):raise ValueError('Resolution documentation required')
    before=row.status;row.status=status;row.updated_at=datetime.now(timezone.utc)
    if resolution:row.resolution=resolution
    if note:db.add(Note(recommendation_id=row.id,user_id=user_id,episode=row.episode,body=note))
    audit(db,user_id,row,'workflow',{'from':before,'to':status,'note_added':bool(note),'resolution_updated':bool(resolution)})
    db.flush()


def serialize(row):
    return {k:getattr(row,k) for k in ('id','problem_key','device_id','rule','status','evidence_state','episode',
        'evidence','resolution','first_seen_at','episode_started_at','updated_at','recovery_observed_at')}
