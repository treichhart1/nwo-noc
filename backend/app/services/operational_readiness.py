"""Read-only operational correlation. No polling or remediation is performed."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, and_, or_
from app.models.entities import Device, Site, NetworkLink, MaintenanceWindow, TelemetrySample, Incident
from app.services.rf_degradation import evaluate_rf_degradation
from app.services.rf_metrics import normalize_rf_metrics


def snapshot(db, *, now=None):
    now=now or datetime.now(timezone.utc)
    devices=list(db.scalars(select(Device).where(Device.enabled.is_(True))))
    sites=list(db.scalars(select(Site)))
    links=list(db.scalars(select(NetworkLink)))
    windows=list(db.scalars(select(MaintenanceWindow).where(
        MaintenanceWindow.status.in_(['scheduled','active']),MaintenanceWindow.starts_at<=now,MaintenanceWindow.ends_at>now)))
    active=list(db.scalars(select(Incident).where(Incident.status.not_in(('resolved','closed')))))
    ranked=select(TelemetrySample.id.label('sample_id'),func.row_number().over(
        partition_by=TelemetrySample.device_id,order_by=(TelemetrySample.sampled_at.desc(),TelemetrySample.id.desc())).label('rn')
    ).where(or_(and_(TelemetrySample.sampled_at>=now-timedelta(minutes=15),TelemetrySample.sampled_at<=now),
        and_(TelemetrySample.sampled_at>=now-timedelta(days=7),TelemetrySample.raw_data['rf_microwave_v1'].is_not(None)))).subquery()
    samples=list(db.scalars(select(TelemetrySample).join(ranked,ranked.c.sample_id==TelemetrySample.id)
        .where(ranked.c.rn<=41).order_by(TelemetrySample.sampled_at,TelemetrySample.id)))
    history={};by_id={d.id:d for d in devices}
    for sample in samples: history.setdefault(sample.device_id,[]).append(sample)
    monitored=[d for d in devices if d.monitor_enabled]
    fresh={d.id for d in monitored if d.monitor_last_checked_at and now-timedelta(minutes=15)<=d.monitor_last_checked_at<=now}
    down={d.id for d in monitored if d.id in fresh and d.monitor_status=='down'}
    up={d.id for d in monitored if d.id in fresh and d.monitor_status=='up' and d.monitor_consecutive_failures==0}
    maintenance_sites={w.site_id for w in windows if w.site_id}
    maintenance_links={w.link_id for w in windows if w.link_id}
    global_maintenance=any(w.site_id is None and w.link_id is None for w in windows)
    maintained={d.id for d in devices if global_maintenance or d.wisp_site_id in maintenance_sites}
    for link in links:
        if link.id in maintenance_links:
            # Directed A→Z topology: maintenance never hides the shared upstream POP.
            maintained.update(i for i in (link.z_device_id,) if i)
    suppressed={};cycles=set()
    for device_id in sorted(down):
        d=by_id[device_id]; seen={device_id}; parent=d.parent_device_id
        while parent in by_id:
            if parent in seen:
                cycles.update(seen);break
            seen.add(parent)
            if parent in down or parent in maintained:
                suppressed[device_id]=parent;break
            parent=by_id[parent].parent_device_id
    # A topology cycle must never suppress every member's outage.
    for device_id in list(suppressed):
        if device_id not in suppressed:
            continue
        seen={device_id};parent=suppressed[device_id]
        while parent in suppressed and parent not in seen:
            seen.add(parent);parent=suppressed[parent]
        if parent in seen:
            cycles.update(seen);suppressed.pop(min(seen),None)
    alerts=[];rf_degraded=[];rf_healthy=[];missing=[];microwave_records=[]
    for d in devices:
        all_rows=history.get(d.id,[])
        rows=all_rows[-3:]
        if not rows: missing.append(d.id);continue
        if any(isinstance(s.raw_data,dict) and 'rf_microwave_v1' in s.raw_data for s in rows):
            from app.services.microwave_history import parse_microwave_row,validate_association
            from app.services.microwave_diagnostics import analyze_microwave
            from app.services.microwave_policy import MicrowaveThresholds
            from app.core.config import settings
            observations=[];link_id=None
            for row in all_rows:
                try:
                    observation=parse_microwave_row(row);association=row.raw_data['microwave_link_id']
                    matched_link=next((l for l in links if l.id==association),None)
                    validate_association(d,matched_link,observation.profile)
                    if association!=link_id:observations=[]
                    link_id=association;observations.append(observation)
                except (ValueError,KeyError,TypeError):observations=[];link_id=None
            report=analyze_microwave(observations,now=now,thresholds=MicrowaveThresholds(**settings.rf_microwave_thresholds))
            if observations:
                microwave_records.append({'device_id':d.id,'link_id':link_id,'report':report,'observation':observations[-1]})
            if report['state']=='unknown':missing.append(d.id)
            elif report['state']=='healthy':rf_healthy.append(d.id)
            else:rf_degraded.append(d.id)
            continue
        if any(isinstance(s.raw_data,dict) and 'rf_radio_v2' in s.raw_data for s in rows):
            from app.services.radio_history import analyze_radio, RadioThresholds
            from app.services.radio_observation import RadioObservation
            from app.core.config import settings
            observations=[];invalid=False
            for row in all_rows:
                try:
                    observation=RadioObservation.model_validate((row.raw_data or {})['rf_radio_v2'])
                    if observation.observed_at!=row.sampled_at:raise ValueError()
                    observations.append(observation)
                except (KeyError,ValueError,TypeError):
                    # A missing/invalid sample breaks the consecutive history.
                    observations=[];invalid=True
                else:invalid=False
            try:report=analyze_radio(observations,now=now,thresholds=RadioThresholds(**settings.rf_radio_thresholds))
            except ValueError:report={'state':'unknown','reasons':[]}
            if invalid or report['state']=='unknown':missing.append(d.id)
            if not invalid and report['state']=='healthy':rf_healthy.append(d.id)
            if not invalid and report['state']=='degraded':
                rf_degraded.append(d.id)
                parent=d.parent_device_id;seen={d.id};upstream=False
                while parent in by_id and parent not in seen:
                    seen.add(parent)
                    if parent in down or parent in maintained:upstream=True;break
                    parent=by_id[parent].parent_device_id
                if d.id in up and d.id not in maintained and not upstream:
                    alerts.append(dict(key=f'rf:{d.id}',kind='rf_degraded',severity='warning',device_id=d.id,
                        site_id=d.wisp_site_id,name=d.name,observed_at=rows[-1].sampled_at.isoformat(),
                        reasons=report['reasons'],action='Review sustained radio telemetry; upstream cause is unconfirmed and remediation is simulation-only'))
            continue
        metrics=[normalize_rf_metrics((s.raw_data or {}).get('rf_metrics_v1',{}))
                 if isinstance(s.raw_data,dict) and isinstance(s.raw_data.get('rf_metrics_v1'),dict) else {} for s in rows]
        if not any(metrics): missing.append(d.id)
        if len(rows)==3 and all(all(k in m for k in ('rssi_dbm','snr_db','ccq_percent')) for m in metrics) and not evaluate_rf_degradation(metrics)['degraded']:
            rf_healthy.append(d.id)
        if len(rows)==3 and evaluate_rf_degradation(metrics)['degraded']:
            rf_degraded.append(d.id)
            if d.id in up and d.id not in maintained and d.id not in suppressed:
                alerts.append(dict(key=f'rf:{d.id}',kind='rf_degraded',severity='warning',device_id=d.id,
                    site_id=d.wisp_site_id,name=d.name,observed_at=rows[-1].sampled_at.isoformat(),
                    action='Review sustained RSSI/SNR/CCQ degradation; remediation is simulation-only'))
    from app.services.microwave_topology import correlate_microwave
    microwave,microwave_alerts,microwave_impacted,microwave_healthy_links=correlate_microwave(microwave_records,devices,links,
        down=down,up=up,maintained=maintained,maintenance_links=maintenance_links,maintenance_sites=maintenance_sites,global_maintenance=global_maintenance)
    alerts=[a for a in alerts if a.get('device_id') not in microwave_impacted]+microwave_alerts
    roots=down-set(suppressed)-maintained-microwave_impacted
    for device_id in sorted(roots):
        d=by_id[device_id]
        alerts.append(dict(key=f'device:{d.id}',kind='device_down',severity='critical',device_id=d.id,
            site_id=d.wisp_site_id,name=d.name,observed_at=(d.monitor_last_down_at or d.monitor_last_checked_at).isoformat(),
            action='Check upstream reachability and site power before investigating downstream radios'))
    link_states=[]
    for link in links:
        endpoints=[i for i in (link.a_device_id,link.z_device_id) if i]
        state='unknown' if len(endpoints)!=2 or any(i not in fresh for i in endpoints) else 'down' if any(i in down for i in endpoints) else 'up'
        microwave_view=next((m for m in microwave if m['id']==link.id),None)
        if microwave_view and microwave_view['state'] in {'outage','degraded'}:state='down' if microwave_view['state']=='outage' else 'degraded'
        if state=='up' and any(i in rf_degraded for i in endpoints):state='degraded'
        capacity_degraded=False;capacity_known=False
        if state=='up' and link.capacity_mbps and link.capacity_mbps>0:
            capacity_known=True
            for device_id in endpoints:
                rows=history.get(device_id,[])[-3:]
                rates=[normalize_rf_metrics((r.raw_data or {}).get('rf_metrics_v1',{})).get('tx_rate_mbps')
                       if isinstance(r.raw_data,dict) and isinstance(r.raw_data.get('rf_metrics_v1'),dict) else None for r in rows]
                capacity_known = capacity_known and len(rates)==3 and all(rate is not None for rate in rates)
                if len(rates)==3 and all(rate is not None and rate<link.capacity_mbps*.5 for rate in rates):capacity_degraded=True
            if capacity_degraded:state='degraded'
        in_maintenance=global_maintenance or link.id in maintenance_links or link.a_site_id in maintenance_sites or link.z_site_id in maintenance_sites
        link_states.append(dict(id=link.id,name=link.name,state=state,maintenance=in_maintenance,capacity_degraded=capacity_degraded,capacity_known=capacity_known))
        if capacity_degraded and not in_maintenance and microwave_view is None:
            alerts.append(dict(key=f'link:{link.id}',kind='capacity_degraded',severity='warning',link_id=link.id,
                site_id=link.z_site_id,name=link.name,observed_at=now.isoformat(),
                action='Review three consecutive PHY rates below 50% of configured backhaul capacity'))
    site_states=[]
    for site in sites:
        members=[d.id for d in monitored if d.wisp_site_id==site.id]
        state='unknown' if not members else 'down' if all(i in down for i in members) else 'degraded' if any(i in down or i in rf_degraded for i in members) else 'up' if all(i in up for i in members) else 'unknown'
        site_states.append(dict(id=site.id,name=site.name,state=state,maintenance=global_maintenance or site.id in maintenance_sites))
    successful=[d.monitor_last_up_at for d in monitored if d.monitor_last_up_at and d.monitor_last_up_at<=now]
    return dict(generated_at=now.isoformat(),correlation_confidence='probable_not_confirmed',mode='read_only',execution_enabled=False,
        total_devices=len(devices),synthetic_devices=sum(d.source=='synthetic' for d in devices),monitored_devices=len(monitored),fresh_up=len(up),fresh_down=len(down),
        stale_checks=sum(d.id not in fresh and d.monitor_last_checked_at is not None for d in monitored),
        missing_checks=sum(d.monitor_last_checked_at is None for d in monitored),
        rf_degraded=len(rf_degraded),rf_missing_or_stale=len(missing),
        last_successful_check=max(successful).isoformat() if successful else None,
        active_incidents=len(active),root_outages=len(roots),suppressed_downstream=len(set(suppressed)|(down & microwave_impacted)),
        microwave=microwave,microwave_healthy_link_ids=sorted(microwave_healthy_links),
        maintenance_suppressed=len(down & maintained),topology_cycles=sorted(cycles),
        alerts=sorted(alerts,key=lambda a:a['key']),sites=site_states,links=link_states,
        rf_healthy_device_ids=sorted(rf_healthy),healthy_device_ids=sorted(up),down_device_ids=sorted(down),rf_degraded_device_ids=sorted(rf_degraded))
