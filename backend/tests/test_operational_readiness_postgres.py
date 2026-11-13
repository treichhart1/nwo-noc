"""Synthetic WISP monitoring and incident rehearsal on disposable PostgreSQL."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func, event
from sqlalchemy.orm import Session
from app.models.entities import Device, MonitorEvent, MaintenanceWindow, Incident, AuditLog, TelemetrySample
from app.services import monitoring
from app.services.synthetic_wisp import seed_network, VENDORS
from app.services.operational_readiness import snapshot
from app.services.incident_simulation import reconcile
from test_rf_postgres_integration import postgres_db

NOW=datetime(2026,11,12,20,tzinfo=timezone.utc)


def inventory(db, size=250): return seed_network(db,devices=size,now=NOW)


def test_inventory_reproducible_relationships_and_safety(postgres_db):
    net=inventory(postgres_db,400)
    assert len(net['sites'])==40 and len(net['devices'])==400
    assert {d.vendor for d in net['devices']}==set(VENDORS)
    assert len({d.ip_address for d in net['devices']})==400
    assert all(d.source=='synthetic' and d.monitor_host.startswith('198.18.') for d in net['devices'])
    assert sum(d.parent_device_id is not None for d in net['devices'])>350
    with pytest.raises(ValueError): inventory(postgres_db)


def test_initial_success_is_not_recovery_and_two_failures_required(postgres_db):
    db=postgres_db;net=inventory(db);d=net['devices'][0]
    d.monitor_status='unknown';d.monitor_last_checked_at=None;db.flush()
    monitoring.run_due_checks(db,now=NOW,probe=lambda *a:(True,2,None))
    assert db.scalar(select(func.count()).select_from(MonitorEvent))==0
    failing=lambda *a:(False,None,'Synthetic timeout')
    monitoring.run_due_checks(db,now=NOW+timedelta(seconds=15),probe=failing)
    assert db.get(Device,d.id).monitor_status=='up'
    monitoring.run_due_checks(db,now=NOW+timedelta(seconds=30),probe=failing)
    first_down=db.get(Device,d.id).monitor_last_down_at
    assert first_down==NOW+timedelta(seconds=30)
    monitoring.run_due_checks(db,now=NOW+timedelta(seconds=45),probe=failing)
    assert db.get(Device,d.id).monitor_last_down_at==first_down
    assert db.scalar(select(func.count()).select_from(MonitorEvent))==250
    monitoring.run_due_checks(db,now=NOW+timedelta(seconds=60),probe=lambda *a:(True,3,None))
    monitoring.run_due_checks(db,now=NOW+timedelta(seconds=75),probe=lambda *a:(True,3,None))
    assert db.scalar(select(func.count()).select_from(MonitorEvent))==500


def test_parent_correlation_maintenance_and_stale_are_not_recovery(postgres_db,monkeypatch):
    db=postgres_db;net=inventory(db);root=net['routers'][0];child=net['routers'][1]
    for d in net['devices']:
        if d.id==root.id or d.wisp_site_id==child.wisp_site_id:
            d.monitor_status='down';d.monitor_consecutive_failures=2;d.monitor_last_down_at=NOW
    db.flush();view=snapshot(db,now=NOW)
    assert view['root_outages']==1 and view['suppressed_downstream']>=6
    assert sum(s['state']=='down' for s in view['sites'])==1
    assert any(l['state']=='down' for l in view['links'])
    monkeypatch.setattr('app.services.incident_simulation.settings',SimpleNamespace(offline_mode=True,app_env='staging'))
    assert reconcile(db,now=NOW)['created']==1
    assert reconcile(db,now=NOW)['created']==0
    db.add(MaintenanceWindow(title='Synthetic site maintenance',site_id=root.wisp_site_id,
        starts_at=NOW-timedelta(minutes=1),ends_at=NOW+timedelta(minutes=1)))
    db.flush();assert snapshot(db,now=NOW)['root_outages']==0
    assert reconcile(db,now=NOW)['resolved']==0
    assert reconcile(db,now=NOW+timedelta(minutes=20))['resolved']==0
    for d in net['devices']:
        d.monitor_status='up';d.monitor_consecutive_failures=0;d.monitor_last_checked_at=NOW+timedelta(minutes=21)
    db.flush();assert reconcile(db,now=NOW+timedelta(minutes=21))['resolved']==1
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action.like('incident.simulated_%')))==2


def test_rf_capacity_missing_and_invalid_samples(postgres_db):
    db=postgres_db;net=inventory(db);d=net['routers'][1]
    rows=list(db.scalars(select(TelemetrySample).where(TelemetrySample.device_id==d.id)))
    for row in rows:row.raw_data={'rf_metrics_v1':dict(rssi_dbm=-90,snr_db=8,ccq_percent=40,tx_rate_mbps=50)}
    db.flush();view=snapshot(db,now=NOW)
    assert view['rf_degraded']==1
    assert any(a['key']==f'rf:{d.id}' for a in view['alerts'])
    for row in rows:row.raw_data={'rf_metrics_v1':dict(rssi_dbm=-60,snr_db=30,ccq_percent=95,tx_rate_mbps=50)}
    db.flush();view=snapshot(db,now=NOW)
    assert any(a['kind']=='capacity_degraded' for a in view['alerts'])
    rows[1].raw_data={};db.flush()
    assert not any(a['kind']=='capacity_degraded' for a in snapshot(db,now=NOW)['alerts'])
    stale=snapshot(db,now=NOW+timedelta(minutes=16))
    assert stale['stale_checks']==250 and stale['fresh_down']==0 and stale['rf_missing_or_stale']==250


def test_concurrent_worker_skips_locked_batch_and_due_poll(postgres_db):
    db=postgres_db;inventory(db)
    assert monitoring.run_due_checks(db,now=NOW,probe=lambda *a:pytest.fail('not due'))['checked']==0
    with db.get_bind().engine.connect() as connection:
        with Session(connection) as second:
            assert monitoring.run_due_checks(second,now=NOW)['overlap_skipped'] is True


def test_timeout_is_bounded_and_failure_recovers(postgres_db):
    db=postgres_db;net=inventory(db);d=net['devices'][0];d.monitor_timeout_seconds=999;db.flush()
    seen=[]
    def timeout(host,port,seconds):
        seen.append(seconds);raise TimeoutError('Synthetic timeout')
    result=monitoring.run_due_checks(db,now=NOW+timedelta(seconds=15),probe=timeout)
    assert result['checked']==250 and max(seen)==10
    result=monitoring.run_due_checks(db,now=NOW+timedelta(seconds=30),probe=lambda *a:(True,1,None))
    assert result['checked']==250
    assert db.scalar(select(func.count()).select_from(MonitorEvent))==0


@pytest.mark.parametrize('size',[250,500,1000])
def test_snapshot_query_budget_and_batch_polling(postgres_db,size):
    db=postgres_db;inventory(db,size);queries=[]
    def count(conn,cursor,statement,*args):
        if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
    bind=db.get_bind();event.listen(bind,'before_cursor_execute',count)
    try:
        view=snapshot(db,now=NOW)
        assert len(queries)<=6
        queries.clear()
        result=monitoring.run_due_checks(db,now=NOW+timedelta(seconds=15),probe=lambda *a:(True,2,None))
        assert result['checked']==size
        assert len(queries)<=3, f'Polling SELECT count scaled with inventory: {len(queries)}'
    finally:event.remove(bind,'before_cursor_execute',count)
    assert view['fresh_up']==size and view['total_devices']==size


def test_operator_incident_lifecycle_and_audit(postgres_db):
    from starlette.requests import Request
    from app.api.routes import create_incident, update_incident
    from app.models.entities import User
    from app.schemas.common import IncidentCreate, IncidentUpdate
    db=postgres_db
    user=User(username='synthetic-operator',email='operator@example.invalid',role='operator',password_hash='not-a-login-credential')
    db.add(user);db.flush()
    req=Request({'type':'http','client':('127.0.0.1',12345)})
    item=create_incident(IncidentCreate(title='Synthetic outage'),req,db,user)
    acknowledged=update_incident(item.id,IncidentUpdate(status='acknowledged'),req,db,user)
    assert acknowledged.acknowledged_at is not None
    resolved=update_incident(item.id,IncidentUpdate(status='resolved'),req,db,user)
    assert resolved.resolved_at is not None
    reopened=update_incident(item.id,IncidentUpdate(status='open'),req,db,user)
    assert reopened.resolved_at is None
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.user_id==user.id))==4
    with pytest.raises(ValueError):IncidentUpdate(status='invalid-state')


def test_topology_cycle_cannot_hide_every_outage(postgres_db):
    db=postgres_db;net=inventory(db)
    a,b=net['routers'][:2]
    a.parent_device_id=b.id;b.parent_device_id=a.id
    for d in (a,b):d.monitor_status='down';d.monitor_consecutive_failures=2
    db.flush();view=snapshot(db,now=NOW)
    assert view['root_outages']>=1 and view['topology_cycles']


def test_simulation_refuses_non_synthetic_and_live_modes(postgres_db,monkeypatch):
    db=postgres_db;net=inventory(db)
    with pytest.raises(ValueError):reconcile(db,now=NOW)
    monkeypatch.setattr('app.services.incident_simulation.settings',SimpleNamespace(offline_mode=True,app_env='staging'))
    net['devices'][0].source='uisp';db.flush()
    with pytest.raises(ValueError):reconcile(db,now=NOW)


def test_link_maintenance_does_not_hide_shared_pop_failure(postgres_db):
    from app.models.entities import NetworkLink
    db=postgres_db;net=inventory(db);root=net['routers'][0];child=net['routers'][1]
    root.monitor_status=child.monitor_status='down'
    link=db.scalar(select(NetworkLink).where(NetworkLink.z_device_id==child.id))
    db.add(MaintenanceWindow(title='Synthetic directed link work',link_id=link.id,
        starts_at=NOW-timedelta(minutes=1),ends_at=NOW+timedelta(minutes=1)))
    db.flush();view=snapshot(db,now=NOW)
    assert any(a['key']==f'device:{root.id}' for a in view['alerts'])
    assert not any(a['key']==f'device:{child.id}' for a in view['alerts'])


def test_rf_incident_requires_valid_healthy_samples_to_close(postgres_db,monkeypatch):
    db=postgres_db;net=inventory(db);device=net['devices'][0]
    monkeypatch.setattr('app.services.incident_simulation.settings',SimpleNamespace(offline_mode=True,app_env='staging'))
    rows=list(db.scalars(select(TelemetrySample).where(TelemetrySample.device_id==device.id)))
    for row in rows:row.raw_data={'rf_metrics_v1':dict(rssi_dbm=-90,snr_db=8,ccq_percent=40)}
    db.flush();assert reconcile(db,now=NOW)['created']==1
    for row in rows:row.raw_data={}
    db.flush();assert reconcile(db,now=NOW)['resolved']==0
    for row in rows:row.raw_data={'rf_metrics_v1':dict(rssi_dbm=-60,snr_db=30,ccq_percent=95)}
    db.flush();assert reconcile(db,now=NOW)['resolved']==1


def test_batch_failure_rolls_back_without_duplicate_transitions(postgres_db,monkeypatch):
    db=postgres_db;inventory(db)
    with pytest.raises(RuntimeError):
        monitoring.run_due_checks(db,now=NOW+timedelta(seconds=15),probe=lambda *a:(_ for _ in ()).throw(RuntimeError('Synthetic worker interruption')))
    # No application state was applied before all probe results completed.
    assert db.scalar(select(func.count()).select_from(MonitorEvent))==0
    assert monitoring.run_due_checks(db,now=NOW+timedelta(seconds=15),probe=lambda *a:(True,2,None))['checked']==250


def test_future_checks_are_unknown_and_are_polled_again(postgres_db):
    db=postgres_db;net=inventory(db);d=net['devices'][0]
    d.monitor_last_checked_at=d.monitor_last_up_at=NOW+timedelta(hours=1)
    for row in db.scalars(select(TelemetrySample).where(TelemetrySample.device_id==d.id)):
        row.sampled_at=NOW+timedelta(hours=1)
    db.flush();view=snapshot(db,now=NOW)
    assert view['fresh_up']==249 and view['root_outages']==0
    assert view['stale_checks']==1 and view['rf_missing_or_stale']==1
    assert view['last_successful_check']==NOW.isoformat()
    assert monitoring.run_due_checks(db,now=NOW,probe=lambda *a:(True,1,None))['checked']==1


def test_alert_center_uses_correlated_monitoring_and_never_raw_secrets(postgres_db):
    from app.api.routes import alerts
    db=postgres_db;now=datetime.now(timezone.utc);net=seed_network(db,devices=250,now=now)
    for d in net['routers'][:2]:d.monitor_status='down';d.monitor_consecutive_failures=2;d.monitor_last_down_at=now
    net['routers'][0].raw_data={'password':'must-not-leak'}
    db.flush()
    first=alerts(limit=100,db=db,user=SimpleNamespace(role='admin'))
    second=alerts(limit=100,db=db,user=SimpleNamespace(role='admin'))
    assert len(first)==len(second)==1 and first[0].severity=='critical'
    assert first[0].id==second[0].id and first[0].created_at==second[0].created_at
    assert 'must-not-leak' not in first[0].model_dump_json()
