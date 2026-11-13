"""Filtered batch analytics and probable topology context, without incident writes."""
from datetime import datetime,timezone
from sqlalchemy import select
from app.models.entities import Device,Site,NetworkLink,Incident,RFAnalyticsCheckpoint,RFAnalyticsPoint as Point
from app.services.rf_analytics_policy import AnalyticsPolicy,WINDOWS
from app.services.rf_analytics_queries import frame_for,current_history,baseline_stats,series_reports,recurring_periods


def correlate_reports(reports,devices,links,incidents,recurrence):
    by_id={d.id:d for d in devices};children={}
    for d in devices:children.setdefault(d.parent_device_id,[]).append(d.id)
    alerts={}
    link_by_id={l.id:l for l in links}
    for report in reports:
        analysis=report['analysis']
        if not analysis['patterns']:continue
        link_id=report['link_id']
        associated=link_by_id.get(link_id)
        if not associated or report['device_id'] not in {associated.a_device_id,associated.z_device_id}:link_id=None
        key=f'rf-analytics:link:{link_id}' if link_id else f'rf-analytics:device:{report["device_id"]}'
        alert=alerts.setdefault(key,{'key':key,'link_id':link_id,'device_id':None if link_id else report['device_id'],
            'source_device_ids':[],'series':[],'patterns':[],'classifications':[],'evidence':[],
            'severity':'warning','root_cause':'unconfirmed','execution_enabled':False,'related_incident_ids':[]})
        alert['source_device_ids'].append(report['device_id']);alert['series'].append(report['series_key'])
        alert['patterns'].extend(analysis['patterns']);alert['classifications'].append(analysis['classification'])
        alert['evidence'].append({'series_key':report['series_key'],'device_id':report['device_id'],'measurements':analysis['evidence']})
    suppressed=[];primary=[]
    for alert in alerts.values():
        affected=set();link=link_by_id.get(alert['link_id'])
        if link:
            pending=[link.z_device_id]
            while pending:
                node=pending.pop()
                if node in affected or node not in by_id:continue
                affected.add(node);pending.extend(children.get(node,[]))
        alert['potentially_affected_device_ids']=sorted(affected)
        alert['patterns']=sorted(set(alert['patterns']));alert['series']=sorted(set(alert['series']))
        alert['source_device_ids']=sorted(set(alert['source_device_ids']));alert['classifications']=sorted(set(alert['classifications']))
        alert['related_incident_ids']=sorted(i.id for i in incidents if alert['link_id'] and i.link_id==alert['link_id'] or alert['device_id'] and i.device_id==alert['device_id'])
        participating=[r for r in reports if (r['link_id']==alert['link_id'] if alert['link_id'] else r['device_id']==alert['device_id'])]
        if participating and all('radio_disconnected' in r['analysis']['patterns'] for r in participating):alert['severity']='critical'
    for alert in alerts.values():
        source=set(alert['source_device_ids']);upstream=next((a for a in alerts.values() if a is not alert and a['link_id']
            and source and source<=set(a['potentially_affected_device_ids'])
            and not (alert['link_id'] and set(a['source_device_ids'])<=set(alert['potentially_affected_device_ids']))
            and any(p in a['patterns'] for p in ('signal_decline','weak_signal','radio_disconnected','noise_snr_pattern','capacity_reduction'))),None)
        if upstream:
            suppressed.append(alert|{'suppressed_by':upstream['key'],'reason':'probable_upstream_RF_context_not_confirmed_root_cause'})
        else:primary.append(alert)
    towers={}
    for item in recurrence:
        device=by_id.get(item['device_id'])
        if device and device.wisp_site_id:towers.setdefault((device.wisp_site_id,item['hour']),set()).add(device.id)
    recurring_sites=[{'site_id':site,'hour':hour,'device_ids':sorted(ids),'root_cause':'unconfirmed'} for (site,hour),ids in towers.items() if len(ids)>=2]
    return primary,suppressed,recurring_sites


def analytics_overview(db,*,window='24h',site_id=None,vendor=None,device_id=None,link_id=None,offset=0,limit=100,now=None,policy=None):
    if window not in WINDOWS or not 1<=limit<=100 or not 0<=offset<=10000:raise ValueError('Invalid analytics filter')
    cfg=policy or AnalyticsPolicy();now=now or datetime.now(timezone.utc)
    selection=select(Device).where(Device.enabled.is_(True))
    if site_id is not None:selection=selection.where(Device.wisp_site_id==site_id)
    if vendor:selection=selection.where(Device.id.in_(select(Point.device_id).where(Point.vendor==vendor)))
    if device_id is not None:selection=selection.where(Device.id==device_id)
    links=list(db.scalars(select(NetworkLink)))
    if link_id is not None:
        link=next((l for l in links if l.id==link_id),None)
        selection=selection.where(Device.id.in_([link.a_device_id,link.z_device_id] if link else []))
    chosen=list(db.scalars(selection.order_by(Device.id).offset(offset).limit(limit+1)));more=len(chosen)>limit;chosen=chosen[:limit]
    ids=[d.id for d in chosen]
    # Topology is current inventory, not a claim about historical parentage.
    topology=list(db.scalars(select(Device).where(Device.enabled.is_(True))))
    reports=[];recurrence=[]
    if ids:
        frame=frame_for(ids,now,WINDOWS[window][0],cfg.max_gap_seconds)
        baseline=baseline_stats(db,frame,now,window,cfg);history=current_history(db,frame,ids)
        reports=series_reports(history,baseline,now,cfg)
        if vendor:reports=[r for r in reports if r['vendor']==vendor]
        if link_id is not None:reports=[r for r in reports if r['link_id']==link_id]
        if window in {'7d','30d'}:recurrence=recurring_periods(db,frame,cfg)
    incidents=list(db.scalars(select(Incident).where(Incident.status.not_in(('resolved','closed')))))
    alerts,suppressed,towers=correlate_reports(reports,topology,links,incidents,recurrence)
    checkpoint=db.get(RFAnalyticsCheckpoint,'rf-v1')
    by_device={d.id:d for d in chosen}
    for report in reports:
        d=by_device[report['device_id']];report.update(device_name=d.name,site_id=d.wisp_site_id)
    return {'window':window,'timezone':cfg.analysis_timezone,'generated_at':now.isoformat(),'series':reports,
        'policy':cfg.model_dump(),
        'selected_devices':len(chosen),'devices_without_projected_RF':[d.id for d in chosen if not any(r['device_id']==d.id for r in reports)],
        'alerts':alerts,'suppressed_alerts':suppressed,'recurring_periods':recurrence,'recurring_sites':towers,
        'offset':offset,'limit':limit,'has_more':more,'projection':{'last_source_id':checkpoint.last_source_id,'updated_at':checkpoint.updated_at.isoformat()} if checkpoint else None,
        'execution_enabled':False,'read_only':True,'root_cause':'unconfirmed','incident_policy':'link existing incidents; no automatic incident creation',
        'coverage_scope':'selected page; upstream findings outside filters/page may be absent'}
