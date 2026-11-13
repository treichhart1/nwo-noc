"""Explicit synthetic-only incident lifecycle rehearsal; no production worker hook."""
from datetime import datetime, timezone
from sqlalchemy import select, text
from app.core.config import settings
from app.models.entities import Device, Incident, AuditLog
from app.services.operational_readiness import snapshot


def reconcile(db, *, now=None):
    if not settings.offline_mode or settings.app_env!='staging':
        raise ValueError('Incident simulation requires offline staging')
    if any(source!='synthetic' for source in db.scalars(select(Device.source))):
        raise ValueError('Incident simulation requires exclusively synthetic inventory')
    now=now or datetime.now(timezone.utc)
    if db.get_bind().dialect.name=='postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(850086)'))
    view=snapshot(db,now=now)
    alerts={a['key']:a for a in view['alerts']}
    existing=list(db.scalars(select(Incident).where(Incident.summary.like('noc85-simulation:%'),Incident.status.not_in(('resolved','closed')))))
    known={i.summary.removeprefix('noc85-simulation:'):i for i in existing}
    created=closed=0
    for key,alert in alerts.items():
        if key in known:continue
        incident=Incident(title=f"SIMULATION: {alert['name']} {alert['kind']}",severity=alert['severity'],
            device_id=alert.get('device_id'),site_id=alert.get('site_id'),link_id=alert.get('link_id'),
            summary='noc85-simulation:'+key,started_at=now)
        db.add(incident);db.flush()
        db.add(AuditLog(action='incident.simulated_create',entity_type='incident',entity_id=str(incident.id),
                        details={'key':key,'simulation_only':True},created_at=now));created+=1
    for key,incident in known.items():
        # Missing, stale, suppressed or still-failing telemetry is never recovery.
        confirmed=(key.startswith('device:') and incident.device_id in view['healthy_device_ids']) or (
            key.startswith('microwave:') and incident.link_id in view['microwave_healthy_link_ids']) or (
            key.startswith('rf:') and incident.device_id in view['healthy_device_ids']
            and incident.device_id in view['rf_healthy_device_ids']) or (
            key.startswith('link:') and any(l['id']==incident.link_id and l['state']=='up' and l['capacity_known'] and not l['maintenance'] for l in view['links']))
        if key not in alerts and confirmed:
            incident.status='resolved';incident.revision+=1;incident.resolved_at=now;incident.resolution='Synthetic confirmed recovery; no remediation executed'
            db.add(AuditLog(action='incident.simulated_resolve',entity_type='incident',entity_id=str(incident.id),
                            details={'key':key,'simulation_only':True},created_at=now));closed+=1
    db.flush()
    return {'created':created,'resolved':closed,'simulation_only':True}
