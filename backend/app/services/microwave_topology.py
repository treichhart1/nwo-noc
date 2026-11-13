"""Probable configured A->Z impact, never confirmed physical root cause."""
def correlate_microwave(records,devices,links,*,down,up,maintained,maintenance_links,maintenance_sites,global_maintenance):
    by_link={};by_device={d.id:d for d in devices}
    for record in records:by_link.setdefault(record['link_id'],[]).append(record)
    views=[];alerts=[];impacted=set();healthy_links=set()
    for link in links:
        members=by_link.get(link.id,[])
        if not members:continue
        states=[m['report']['state'] for m in members]
        known=[s for s in states if s!='unknown']
        conflict='healthy' in known and 'outage' in known
        state='outage' if known and all(s=='outage' for s in states) else 'degraded' if any(s in {'outage','degraded'} for s in known) else 'healthy' if states and all(s=='healthy' for s in states) else 'unknown'
        maintenance=global_maintenance or link.id in maintenance_links or link.a_site_id in maintenance_sites or link.z_site_id in maintenance_sites
        affected=set();frontier=[link.z_device_id] if link.z_device_id else []
        while frontier:
            current=frontier.pop()
            if current in affected or current not in by_device:continue
            affected.add(current);frontier.extend(d.id for d in devices if d.parent_device_id==current)
        upstream=False
        endpoint=by_device.get(link.a_device_id);seen=set()
        while endpoint and endpoint.parent_device_id in by_device and endpoint.id not in seen:
            seen.add(endpoint.id);parent=by_device[endpoint.parent_device_id]
            if parent.id in down or parent.id in maintained:upstream=True;break
            endpoint=parent
        if link.a_device_id in down or link.a_device_id in maintained:upstream=True
        reasons=sorted({r for m in members for r in m['report']['reasons']})
        if conflict:reasons.append('conflicting_endpoint_reports')
        latest=max(members,key=lambda m:m['observation'].observed_at)
        obs=latest['observation']
        rf_states={m['report']['rf_state'] for m in members}
        ethernet_states={m['report']['ethernet_state'] for m in members}
        view={'id':link.id,'name':link.name,'a_site_id':link.a_site_id,'z_site_id':link.z_site_id,
            'state':state,'rf_state':next(iter(rf_states)) if len(rf_states)==1 else 'mixed',
            'ethernet_state':next(iter(ethernet_states)) if len(ethernet_states)==1 else 'mixed',
            'maintenance':maintenance,'upstream_suppressed':upstream,'conflicting_reports':conflict,
            'affected_device_ids':sorted(affected),'reasons':reasons,'source_device_id':latest['device_id'],
            'last_successful_collection':obs.received_at.isoformat(),'freshness':latest['report']['freshness']['status'],
            'reported_capacity_mbps':obs.measurements.reported_capacity_mbps,
            'coverage':'single_endpoint' if len(members)==1 else 'multiple_endpoints',
            'endpoint_reports':[{'device_id':m['device_id'],'state':m['report']['state'],'rf_state':m['report']['rf_state'],
                                 'ethernet_state':m['report']['ethernet_state'],'freshness':m['report']['freshness']['status']} for m in members],
            'carrier_states':{k:c['state'] for k,c in latest['report']['carriers'].items()},
            'root_cause':'unconfirmed'}
        views.append(view)
        if state=='healthy' and not maintenance and not upstream and {link.a_device_id,link.z_device_id}<=up:healthy_links.add(link.id)
        if state in {'outage','degraded'} and not maintenance and not upstream:
            alerts.append({'key':f'microwave:{link.id}','kind':'microwave_backhaul','severity':'critical' if state=='outage' else 'warning',
                'link_id':link.id,'site_id':link.z_site_id,'name':link.name,'observed_at':obs.observed_at.isoformat(),
                'reasons':reasons,'action':'Review carrier RF, Ethernet and collection evidence separately; downstream impact is probable, root cause unconfirmed; no remediation executed'})
            # Only a sustained RF outage suppresses downstream reachability alerts.
            # Conflicting/partial carrier reports retain independent device evidence.
            if state=='outage' and not conflict:impacted.update(affected)
    return views,alerts,impacted,healthy_links
