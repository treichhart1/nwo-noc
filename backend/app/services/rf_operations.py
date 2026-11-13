"""Bounded, batched RF operator read model. No polling, projection or incident writes."""
from collections import defaultdict,Counter
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,func,or_,and_
from app.models.entities import (Device,Site,NetworkLink,Sector,Incident,Integration,AuditLog,TelemetrySample,
    RFAnalyticsPoint as Point,RFRecommendation,RFAlertReview,RFAnalyticsCheckpoint)
from app.services.rf_analytics_policy import AnalyticsPolicy,WINDOWS,METRICS
from app.services.rf_analytics_queries import (utc,frame_for,current_history,baseline_stats,series_reports,analytics_detail)
from app.services.rf_analytics_overview import correlate_reports
from app.services.rf_troubleshooting import descendants,serialize as recommendation_out
from app.core.config import settings
from app.integrations.read_only import ERROR_CODES

ACTIVE=('open','acknowledged','investigating')


def bounded(db,model):
    rows=list(db.scalars(select(model).order_by(model.id).limit(10001)))
    if len(rows)>10000:raise ValueError('RF operations inventory exceeds 10,000-row bound; narrow the managed inventory')
    return rows


def freshness(at,now,valid=True,stale_after=900):
    if at is None:return 'missing'
    if not valid:return 'invalid'
    age=(now-utc(at)).total_seconds()
    return 'future' if age<0 else 'stale' if age>stale_after else 'fresh'


def point_state(point,now,cfg):
    fresh=freshness(point.sampled_at,now,point.valid,cfg.stale_after_seconds)
    if fresh!='fresh':return {'state':'unknown','freshness':fresh,'flags':[]}
    flags=[]
    for field,limit,name in [('signal_dbm',cfg.signal_low,'weak_signal'),('snr_db',cfg.score_snr_min,'low_snr'),
        ('capacity_mbps',cfg.score_capacity_min,'low_capacity'),('quality_percent',cfg.score_quality_min,'low_quality')]:
        value=getattr(point,field)
        if value is not None and value<limit:flags.append(name)
    if point.utilization_percent is not None and point.utilization_percent>=cfg.utilization_enter:flags.append('high_utilization')
    if point.radio_state in {'down','degraded'}:flags.append('reported_'+point.radio_state)
    known=point.radio_state in {'down','degraded'} or any(getattr(point,k) is not None for k in ('signal_dbm','snr_db','capacity_mbps'))
    return {'state':'degraded_observation' if flags else 'no_threshold_exception_observed' if known else 'unknown',
        'freshness':fresh,'flags':flags}


def inventory_context(db,now,cfg):
    devices=bounded(db,Device);devices=[d for d in devices if d.enabled]
    sites=bounded(db,Site);links=bounded(db,NetworkLink);sectors=bounded(db,Sector)
    ids=[d.id for d in devices]
    latest=list(db.scalars(select(Point).where(Point.device_id.in_(ids)).distinct(Point.device_id,Point.series_key)
        .order_by(Point.device_id,Point.series_key,Point.sampled_at.desc(),Point.id.desc()))) if ids else []
    states=defaultdict(list)
    for p in latest:states[p.device_id].append((p,point_state(p,now,cfg)))
    attempts=list(db.scalars(select(AuditLog).where(AuditLog.action.in_(('rf.read_only_poll','microwave.read_only_poll')),
        AuditLog.entity_type=='device',AuditLog.entity_id.in_([str(i) for i in ids]))
        .distinct(AuditLog.entity_id).order_by(AuditLog.entity_id,AuditLog.created_at.desc(),AuditLog.id.desc()))) if ids else []
    poll={int(a.entity_id):{'at':a.created_at.isoformat(),'result':(a.details or {}).get('result') if (a.details or {}).get('result') in ERROR_CODES|{'success'} else 'sync_failed'} for a in attempts}
    successful=dict(db.execute(select(AuditLog.entity_id,func.max(AuditLog.created_at)).where(
        AuditLog.action.in_(('rf.read_only_poll','microwave.read_only_poll')),AuditLog.entity_type=='device',
        AuditLog.entity_id.in_([str(i) for i in ids]),AuditLog.details['result'].as_string()=='success').group_by(AuditLog.entity_id)).all()) if ids else {}
    output={}
    for d in devices:
        rows=states[d.id];rf=[v for _,v in rows];counts=Counter(v['state'] for v in rf)
        state='degraded_observation' if counts['degraded_observation'] else 'unknown' if not rf or counts['unknown'] else 'no_threshold_exception_observed'
        monitor=freshness(d.monitor_last_checked_at,now) if d.monitor_enabled else 'disabled'
        reach=d.monitor_status if monitor=='fresh' and d.monitor_status in {'up','down'} else 'unknown'
        times=[p.sampled_at for p,_ in rows if p.valid and p.sampled_at<=now]
        output[d.id]={'id':d.id,'name':d.name,'vendor':d.vendor or 'unknown','site_id':d.wisp_site_id,'parent_device_id':d.parent_device_id,
            'reachability':reach,'monitor_freshness':monitor,'monitor_checked_at':d.monitor_last_checked_at,
            'rf_state':state,'rf_freshness':sorted(set(v['freshness'] for v in rf)) if rf else ['missing'],
            'flags':sorted({f for v in rf for f in v['flags']}),
            'series_observations':[{'series_key':p.series_key,'link_id':p.link_id,**v} for p,v in rows],
            'last_usable_telemetry_at':max(times).isoformat() if times else None,
            'last_poll_attempt':poll.get(d.id),'last_successful_poll_at':successful.get(str(d.id)),
            'partial_telemetry':any(v['state']=='unknown' for v in rf) or not rf}
    return devices,sites,links,sectors,output



def link_endpoint_state(row,link_id):
    selected=[s for s in row['series_observations'] if s['link_id'] in {None,link_id}]
    counts=Counter(s['state'] for s in selected)
    value='degraded_observation' if counts['degraded_observation'] else 'unknown' if not selected or counts['unknown'] else 'no_threshold_exception_observed'
    return row|{'rf_state':value}


def health(rows):
    counts=Counter(r['rf_state'] for r in rows);reach=Counter(r['reachability'] for r in rows)
    return {'devices':len(rows),'rf_degraded_observations':counts['degraded_observation'],'rf_unknown':counts['unknown'],
        'rf_no_exception_observed':counts['no_threshold_exception_observed'],'fresh_up':reach['up'],'fresh_down':reach['down'],
        'reachability_unknown':reach['unknown'],
        'state':'degraded_observations' if counts['degraded_observation'] or reach['down'] else 'unknown' if not rows or counts['unknown'] or reach['unknown'] else 'no_exception_observed',
        'meaning':'Latest measured exceptions and coverage, not confirmed root cause or sustained analytical alert count'}


def incident_query(*,device_ids,site_ids,link_ids,status=None,severity=None):
    query=select(Incident).where(or_(Incident.device_id.in_(device_ids),Incident.site_id.in_(site_ids),Incident.link_id.in_(link_ids)))
    if status=='active' or status is None:query=query.where(Incident.status.in_(ACTIVE))
    elif status!='all':query=query.where(Incident.status==status)
    if severity:query=query.where(Incident.severity==severity)
    return query


def incident_out(i):
    return {key:getattr(i,key) for key in ('id','title','status','severity','site_id','device_id','link_id',
        'assigned_to_user_id','findings','summary','root_cause','resolution','started_at','acknowledged_at','resolved_at','closed_at','revision','updated_at')}


def overview(db,*,now=None,window='24h',site_id=None,vendor=None,q='',link_id=None,severity=None,
        incident_status='active',offset=0,limit=50,incident_offset=0,device_id=None):
    if window not in WINDOWS or not 1<=limit<=100 or not 0<=offset<=10000 or not 0<=incident_offset<=10000:raise ValueError('Invalid RF operations window or page')
    now=now or datetime.now(timezone.utc);cfg=AnalyticsPolicy(**settings.rf_analytics_policy)
    devices,sites,links,sectors,state=inventory_context(db,now,cfg)
    if site_id is not None and not any(s.id==site_id for s in sites):raise LookupError('Site not found')
    if link_id is not None and not any(l.id==link_id for l in links):raise LookupError('Link not found')
    term=q.strip().casefold();matched_links={l.id for l in links if term and term in f'{l.id} {l.name}'.casefold()}
    endpoints={i for l in links if l.id in matched_links or l.id==link_id for i in (l.a_device_id,l.z_device_id) if i}
    selected=[d for d in devices if (device_id is None or d.id==device_id) and (site_id is None or d.wisp_site_id==site_id) and (not vendor or (d.vendor or '').casefold()==vendor)
        and (link_id is None or d.id in endpoints) and (not term or term in f'{d.id} {d.name}'.casefold() or d.id in endpoints)]
    ids={d.id for d in selected};site_ids={d.wisp_site_id for d in selected if d.wisp_site_id}
    if site_id:site_ids.add(site_id)
    related_links=[l for l in links if (link_id is None or l.id==link_id) and (l.a_device_id in ids or l.z_device_id in ids or (site_id and site_id in {l.a_site_id,l.z_site_id}))]
    link_ids={l.id for l in related_links}
    page=selected[offset:offset+limit];page_ids=[d.id for d in page]
    reports=[]
    if page_ids:
        frame=frame_for(page_ids,now,WINDOWS[window][0],cfg.max_gap_seconds)
        reports=series_reports(current_history(db,frame,page_ids),baseline_stats(db,frame,now,window,cfg),now,cfg)
    if link_id is not None:reports=[s for s in reports if s['link_id'] in {None,link_id}]
    query=incident_query(device_ids=ids,site_ids=site_ids,link_ids=link_ids,status=incident_status,severity=severity)
    incident_total=db.scalar(select(func.count()).select_from(query.subquery()))
    incidents=list(db.scalars(query.order_by(Incident.started_at.desc(),Incident.id.desc()).offset(incident_offset).limit(26)))
    # Correlation uses the selected page and bounded related incidents, never an unbounded incident list.
    alerts,suppressed,_=correlate_reports(reports,devices,related_links,incidents[:25],[])
    reviews={r.alert_key:r for r in db.scalars(select(RFAlertReview).where(RFAlertReview.alert_key.in_([a['key'] for a in alerts+suppressed])))} if alerts or suppressed else {}
    for a in alerts+suppressed:
        source=[s for s in reports if s['device_id'] in a['source_device_ids'] and s['series_key'] in a['series']]
        observed=max((datetime.fromisoformat(s['last_sampled_at']) for s in source),default=None)
        a['observed_at']=observed.isoformat() if observed else None
        review=reviews.get(a['key'])
        a['review']={'at':review.acknowledged_at,'user_id':review.user_id,'incident_id':review.incident_id,
            'observed_at':review.observed_at,'new_evidence_since_review':bool(observed and observed>utc(review.observed_at))} if review else None
        a['incident_links_complete']=incident_total<=25
    if severity:
        alerts=[a for a in alerts if a['severity']==severity];suppressed=[a for a in suppressed if a['severity']==severity]
    recs=list(db.scalars(select(RFRecommendation).where(RFRecommendation.device_id.in_(page_ids),RFRecommendation.status.not_in(('closed','dismissed','resolved')))
        .order_by(RFRecommendation.updated_at.desc(),RFRecommendation.id.desc()).limit(26))) if page_ids else []
    by_site=defaultdict(list)
    for i in ids:by_site[state[i]['site_id']].append(state[i])
    sites_out=[{'id':s.id,'name':s.name,'type':s.site_type,'inventory_status':s.status,'health':health(by_site[s.id])} for s in sites if s.id in site_ids]
    links_out=[]
    for l in related_links[:100]:
        rows=[link_endpoint_state(state[i],l.id) for i in {l.a_device_id,l.z_device_id} if i in state]
        links_out.append({'id':l.id,'name':l.name,'a_site_id':l.a_site_id,'z_site_id':l.z_site_id,
            'a_device_id':l.a_device_id,'z_device_id':l.z_device_id,'health':health(rows),
            'endpoint_coverage':len(rows),'scope':'current inventory endpoints; per-series association is shown in link detail'})
    sectors_out=[{'id':s.id,'name':s.name,'site_id':s.site_id,'device_id':s.device_id,'inventory_status':s.status,
        'health':state[s.device_id]['rf_state'] if s.device_id in state else 'unknown'} for s in sectors if s.site_id in site_ids][:100]
    integrations=list(db.scalars(select(Integration).order_by(Integration.id).limit(101)))
    checkpoint=db.get(RFAnalyticsCheckpoint,'rf-v1')
    dependencies=None
    if site_id:
        local={d.id for d in devices if d.wisp_site_id==site_id}
        downstream=set(i for d in devices if d.id in local for i in descendants(devices,d.id))-local
        upstream=sorted({d.parent_device_id for d in devices if d.id in local and d.parent_device_id and d.parent_device_id not in local})
        dependencies={'upstream_device_ids':upstream,'potential_downstream_device_ids':sorted(downstream),
            'probable_upstream_down_device_ids':[i for i in upstream if i in state and state[i]['reachability']=='down'],
            'impact':'Potential device dependencies, not subscribers or confirmed service loss; inventory may be incomplete',
            'redundancy':'Unknown until independently validated'}
    return {'generated_at':now.isoformat(),'timezone':cfg.analysis_timezone,'window':window,'read_only':True,'execution_enabled':False,
        'summary':health([state[i] for i in ids]),'selected_devices':len(selected),'inventory':[state[d.id] for d in page],
        'sites':sites_out,'site_options':[{'id':s.id,'name':s.name} for s in sites[:1000]],'site_options_truncated':len(sites)>1000,
        'links':links_out,'links_truncated':len(related_links)>100,'sectors':sectors_out,
        'sectors_truncated':sum(s.site_id in site_ids for s in sectors)>100,'dependencies':dependencies,
        'series':reports,'alerts':alerts,'suppressed_alerts':suppressed,
        'alert_scope':'Current device page only; severity filters analytical alerts on this page. Fleet summary reports latest observations.',
        'incidents':[incident_out(i) for i in incidents[:25]],'incident_total':incident_total,'incident_has_more':len(incidents)>25,'incident_offset':incident_offset,
        'recommendations':[recommendation_out(r) for r in recs[:25]],'recommendations_truncated':len(recs)>25,
        'integrations':[{'name':i.name,'state':i.status if i.status in {'connected','degraded','disabled','not_configured','failed'} else 'unknown',
            'last_attempt_at':i.last_attempt_at,'last_success_at':i.last_success_at,'error':i.last_error if i.last_error in ERROR_CODES else 'sync_failed' if i.last_error else None} for i in integrations[:100]],
        'projection':{'updated_at':checkpoint.updated_at,'last_source_id':checkpoint.last_source_id} if checkpoint else None,
        'offset':offset,'limit':limit,'has_more':offset+limit<len(selected),'source':'stored normalized data only; no equipment requests'}


def link_detail(db,link_id,*,window='24h',now=None):
    now=now or datetime.now(timezone.utc);link=db.get(NetworkLink,link_id)
    if link is None:raise LookupError('Link not found')
    summary=overview(db,link_id=link_id,window=window,now=now,limit=100)
    endpoints=[]
    for ident in sorted({i for i in (link.a_device_id,link.z_device_id) if i}):
        d=db.get(Device,ident)
        if d is None:continue
        detail=analytics_detail(db,ident,window=window,now=now,policy=AnalyticsPolicy(**settings.rf_analytics_policy))
        for s in detail['series']:
            provenance=metric_provenance(db,s)
            s['metric_status']={k:('unsupported' if provenance['unavailable'].get(k)=='unsupported' else
                'invalid' if s['analysis']['freshness']=='invalid' or provenance['unavailable'].get(k)=='invalid' else
                'missing' if s['latest'].get(k) is None else s['analysis']['freshness']) for k in METRICS}
            s['reported_alarms']=provenance['alarms'];s['inventory_link_matches']=s['link_id']==link_id if s['link_id'] else None
        endpoints.append(detail)
    return {'link':{'id':link.id,'name':link.name,'a_site_id':link.a_site_id,'z_site_id':link.z_site_id},
        'endpoints':endpoints,'context':summary,'execution_enabled':False,'read_only':True,
        'association_notice':'An inventory endpoint is not proof that every displayed device series belongs to this link. Explicit source associations are labelled.'}


def metric_provenance(db,series):
    """Expose only validated capability reasons/alarms; never raw payloads."""
    out={'unavailable':{},'alarms':{}}
    row=db.get(TelemetrySample,series['source_references']['latest_sample_id'])
    if row is None:return out
    raw=row.raw_data or {};aliases={'rssi_dbm':'signal_dbm','remote_rssi_dbm':'remote_signal_dbm','local_rsl_dbm':'signal_dbm',
        'remote_rsl_dbm':'remote_signal_dbm','channel_width_mhz':'bandwidth_mhz','link_capacity_mbps':'capacity_mbps',
        'modulation':'modulation_bits','ccq_percent':'quality_percent','tx_rate_mbps':'tx_phy_mbps','rx_rate_mbps':'rx_phy_mbps'}
    try:
        if 'rf_radio_v2' in raw:
            from app.services.radio_observation import RadioObservation
            obs=RadioObservation.model_validate(raw['rf_radio_v2']);unavailable=obs.unavailable
        elif 'rf_observation_v1' in raw:
            from app.services.rf_observation import NormalizedRFTelemetry
            obs=NormalizedRFTelemetry.model_validate(raw['rf_observation_v1']);unavailable=obs.unavailable
        elif 'rf_microwave_v1' in raw:
            from app.services.microwave_model import MicrowaveObservation
            obs=MicrowaveObservation.model_validate(raw['rf_microwave_v1']);key=series['series_key'].split(':',1)[-1]
            carrier=obs.carriers[key];unavailable=carrier.unavailable;out['alarms']=carrier.alarms
        else:return out
        if obs.observed_at!=utc(row.sampled_at):raise ValueError('Source timestamp mismatch')
        out['unavailable']={aliases.get(k,k):v for k,v in unavailable.items() if aliases.get(k,k) in METRICS}
    except (ValueError,KeyError,TypeError):out['unavailable']={k:'invalid' for k in METRICS}
    return out
