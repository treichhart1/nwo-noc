import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func,event
from app.models.entities import Device,Site,NetworkLink,TelemetrySample,Incident,AuditLog,MaintenanceWindow
from app.services.microwave_history import stage_microwave,microwave_history
from app.services.operational_readiness import snapshot
from app.services.incident_simulation import reconcile
from app.microwave_poll import MicrowavePollConfig,poll_microwave
from app.integrations.read_only import IntegrationFailure
from app.core.config import settings,Settings
from app.core.database import get_db
from app.api.deps import current_user
from app.main import app
from test_rf_postgres_integration import postgres_db
from microwave_fixtures import mapping,observation,raw,NOW
from app.services.microwave_model import PROFILES
from radio_fixtures import policy,credentials


def network(db,vendor='Racom'):
    a=Site(name='Synthetic microwave POP');z=Site(name='Synthetic microwave tower');db.add_all([a,z]);db.flush()
    source=Device(source='synthetic',external_id='noc88-a',name='Microwave A',vendor=vendor,wisp_site_id=a.id,
        ip_address='192.0.2.87',authorized=True,enabled=True,monitor_enabled=True,monitor_status='up',monitor_last_checked_at=NOW,monitor_last_up_at=NOW)
    target=Device(source='synthetic',external_id='noc88-z',name='Microwave Z',vendor=vendor,wisp_site_id=z.id,
        authorized=True,enabled=True,monitor_enabled=True,monitor_status='up',monitor_last_checked_at=NOW,monitor_last_up_at=NOW)
    db.add_all([source,target]);db.flush();target.parent_device_id=source.id
    child=Device(source='synthetic',external_id='noc88-child',name='Downstream switch',vendor='MikroTik',wisp_site_id=z.id,
        parent_device_id=target.id,enabled=True,monitor_enabled=True,monitor_status='down',monitor_last_checked_at=NOW)
    db.add(child)
    link=NetworkLink(name='Synthetic microwave backhaul',a_site_id=a.id,z_site_id=z.id,a_device_id=source.id,z_device_id=target.id,capacity_mbps=950)
    db.add(link);db.flush();return source,target,child,link


def stage_series(db,device,link,*,m=None,start=NOW,changes=None,n=6,uptime=100000):
    for i in range(n):stage_microwave(db,device,link,observation(start-timedelta(seconds=(n-1-i)*10),m=m,changes=changes,link_changes={'uptime_seconds':uptime+i*1000}))


@pytest.mark.parametrize('profile',list(PROFILES))
def test_history_roundtrip_originals_dedupe_topology_and_stale(postgres_db,profile):
    db=postgres_db;d,z,child,link=network(db,'Aviat' if profile.startswith('aviat') else 'Racom');m=mapping(profile)
    stage_series(db,d,link,m=m)
    o=observation(m=m,link_changes={'uptime_seconds':105000});replay=o.model_copy(update={'received_at':NOW+timedelta(seconds=2)})
    assert stage_microwave(db,d,link,replay).id==db.scalar(select(TelemetrySample).where(TelemetrySample.sampled_at==NOW)).id
    assert db.scalar(select(func.count()).select_from(TelemetrySample))==6
    report=microwave_history(db,d.id,now=NOW)
    assert report['diagnostics']['state']=='healthy' and report['association_valid']
    assert report['samples'][-1]['original_values']['c1.bandwidth_mhz']==80000
    assert report['topology'][0]['a_site']=='Synthetic microwave POP'
    assert microwave_history(db,d.id,now=NOW+timedelta(hours=1))['diagnostics']['freshness']['status']=='stale'


def test_conflict_wrong_vendor_wrong_link_invalid_rows_and_no_fake_projection(postgres_db):
    db=postgres_db;d,z,child,link=network(db);row=stage_microwave(db,d,link,observation())
    assert 'rf_metrics_v1' not in row.raw_data
    with pytest.raises(ValueError,match='Conflicting'):stage_microwave(db,d,link,observation(changes={'c1':{'local_rsl_dbm':-80}}))
    with pytest.raises(ValueError,match='vendor'):stage_microwave(db,d,link,observation(m=mapping('aviat-11ghz-mib-v1')))
    with pytest.raises(ValueError,match='association'):stage_microwave(db,child,link,observation())
    db.add(TelemetrySample(device_id=d.id,sampled_at=NOW+timedelta(seconds=1),raw_data={'rf_microwave_v1':{'password':'canary'}}));db.flush()
    report=microwave_history(db,d.id,now=NOW+timedelta(seconds=2))
    assert report['invalid_samples']==1 and report['diagnostics']['state']=='unknown' and 'canary' not in str(report)


def test_uint64_counter_browser_representation_is_lossless(postgres_db):
    db=postgres_db;d,z,child,link=network(db)
    stage_microwave(db,d,link,observation(changes={'c1':{'errors':2**60+3}}))
    sample=microwave_history(db,d.id,now=NOW)['samples'][0]
    assert sample['carriers']['c1']['errors_exact']==str(2**60+3)
    assert sample['original_display_values']['c1.errors']==str(2**60+3)


def test_outage_correlation_dedup_incidents_and_fresh_confirmed_recovery(postgres_db,monkeypatch):
    db=postgres_db;d,z,child,link=network(db)
    monkeypatch.setattr(settings,'offline_mode',True);monkeypatch.setattr(settings,'app_env','staging')
    stage_series(db,d,link,changes={'c1':{'radio_status':2},'c2':{'radio_status':2}})
    view=snapshot(db,now=NOW)
    assert [a['key'] for a in view['alerts']]==[f'microwave:{link.id}'] and view['microwave'][0]['state']=='outage'
    assert child.id in view['microwave'][0]['affected_device_ids'] and view['suppressed_downstream']==1
    assert reconcile(db,now=NOW)['created']==1 and reconcile(db,now=NOW)['created']==0
    assert db.scalar(select(func.count()).select_from(Incident))==1
    assert reconcile(db,now=NOW+timedelta(hours=1))['resolved']==0
    # Complete RF recovery alone does not recover stale/down reachability.
    stage_series(db,d,link,start=NOW+timedelta(seconds=60),uptime=106000)
    z.monitor_status='down';db.flush()
    assert reconcile(db,now=NOW+timedelta(seconds=60))['resolved']==0
    z.monitor_status='up';child.monitor_status='up';db.flush()
    # Both the original RF incident and the independently observed device outage recover.
    assert reconcile(db,now=NOW+timedelta(seconds=61))['resolved']==2
    incident=db.scalar(select(Incident).where(Incident.summary==f'noc85-simulation:microwave:{link.id}'))
    assert incident.status=='resolved'
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=='incident.simulated_resolve',AuditLog.entity_id==str(incident.id)))==1


def test_maintenance_upstream_and_conflicting_endpoint_reports(postgres_db):
    db=postgres_db;d,z,child,link=network(db)
    stage_series(db,d,link,changes={'c1':{'radio_status':2},'c2':{'radio_status':2}})
    stage_series(db,z,link)
    view=snapshot(db,now=NOW)
    assert view['microwave'][0]['conflicting_reports'] and view['microwave'][0]['state']=='degraded'
    assert any(a['key']==f'device:{child.id}' for a in view['alerts'])  # Keep independent evidence.
    d.monitor_status='down';db.flush()
    assert not any(a['key'].startswith('microwave:') for a in snapshot(db,now=NOW)['alerts'])
    db.add(MaintenanceWindow(title='Synthetic maintenance',link_id=link.id,starts_at=NOW-timedelta(seconds=1),ends_at=NOW+timedelta(hours=1)));db.flush()
    assert not any(a['key'].startswith('microwave:') for a in snapshot(db,now=NOW)['alerts'])


def test_stale_microwave_visible_in_batched_dashboard_and_query_budget(postgres_db):
    db=postgres_db;d,z,child,link=network(db);stage_series(db,d,link)
    queries=[]
    def count(_conn,_cur,statement,*args):
        if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
    bind=db.get_bind();event.listen(bind,'before_cursor_execute',count)
    try:view=snapshot(db,now=NOW+timedelta(hours=1))
    finally:event.remove(bind,'before_cursor_execute',count)
    assert len(queries)<=6 and view['microwave'][0]['freshness']=='stale'
    assert view['microwave'][0]['state']=='unknown' and not any(a['key'].startswith('microwave:') for a in view['alerts'])


def test_changed_link_association_does_not_report_healthy(postgres_db):
    db=postgres_db;d,z,child,link=network(db);stage_series(db,d,link)
    link.a_device_id=child.id;db.flush()
    report=microwave_history(db,d.id,now=NOW)
    assert not report['association_valid'] and report['diagnostics']['state']=='unknown'
    link.a_device_id=d.id;d.vendor='Ubiquiti';db.flush()
    assert not microwave_history(db,d.id,now=NOW)['association_valid']


def test_poll_disabled_failure_recovery_and_safe_audit(postgres_db,monkeypatch):
    db=postgres_db;d,z,child,link=network(db);m=mapping()
    cfg=MicrowavePollConfig(device_id=d.id,network_link_id=link.id,mapping=m,policy=policy(),credentials_file='/unused')
    t=AsyncMock();monkeypatch.setattr(settings,'rf_microwave_enabled',False)
    with pytest.raises(IntegrationFailure,match='disabled'):asyncio.run(poll_microwave(db,cfg,credentials(),transport=t))
    t.get.assert_not_called()
    monkeypatch.setattr(settings,'rf_microwave_enabled',True);monkeypatch.setattr(settings,'offline_mode',False);monkeypatch.setattr(settings,'integrations_admin_approved',True)
    t.get.side_effect=IntegrationFailure('authentication_failed')
    assert asyncio.run(poll_microwave(db,cfg,credentials(),transport=t))['result']=='authentication_failed'
    assert microwave_history(db,d.id)['last_attempt']['result']=='authentication_failed'
    with pytest.raises(IntegrationFailure,match='rate_limited'):asyncio.run(poll_microwave(db,cfg,credentials(),transport=t))
    attempt=db.scalar(select(AuditLog).where(AuditLog.action=='microwave.read_only_poll'));attempt.created_at-=timedelta(seconds=10);db.flush()
    values=raw(m)
    async def get(address,creds,oids,timeout):return {k:values[k] for k in oids}
    t.get.side_effect=get
    assert asyncio.run(poll_microwave(db,cfg,credentials(),transport=t))['staged']==1
    assert 'synthetic-auth-key' not in str(microwave_history(db,d.id))


def test_authenticated_bounded_read_only_api(postgres_db):
    db=postgres_db;d,z,child,link=network(db);stage_series(db,d,link)
    assert TestClient(app).get(f'/api/devices/{d.id}/microwave-history').status_code==401
    app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[current_user]=lambda:SimpleNamespace(role='viewer')
    try:
        client=TestClient(app);assert client.get(f'/api/devices/{d.id}/microwave-history').status_code==403
        app.dependency_overrides[current_user]=lambda:SimpleNamespace(role='operator')
        assert client.get(f'/api/devices/{d.id}/microwave-history').json()['samples'][-1]['vendor']=='racom'
        assert client.get('/api/devices/99999999/microwave-history').status_code==404
        assert client.get(f'/api/devices/{d.id}/microwave-history?limit=1001').status_code==422
        assert client.post(f'/api/devices/{d.id}/microwave-history',json={}).status_code==405
    finally:app.dependency_overrides.clear()


def test_overlap_and_wrong_association_reject_before_transport(postgres_db,monkeypatch):
    from sqlalchemy import text
    db=postgres_db;d,z,child,link=network(db);m=mapping()
    monkeypatch.setattr(settings,'rf_microwave_enabled',True);monkeypatch.setattr(settings,'offline_mode',False);monkeypatch.setattr(settings,'integrations_admin_approved',True)
    cfg=MicrowavePollConfig(device_id=d.id,network_link_id=link.id,mapping=m,policy=policy(),credentials_file='/unused');t=AsyncMock()
    with db.get_bind().engine.connect() as other:
        other.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':871000+d.id})
        with pytest.raises(IntegrationFailure,match='overlap_skipped'):asyncio.run(poll_microwave(db,cfg,credentials(),transport=t))
    link.a_device_id=child.id;db.flush()
    with pytest.raises(IntegrationFailure,match='configuration_invalid'):asyncio.run(poll_microwave(db,cfg,credentials(),transport=t))
    t.get.assert_not_called()


def test_offline_and_invalid_microwave_settings_rejected():
    with pytest.raises(ValueError):Settings(offline_mode=True,rf_microwave_enabled=True)
    with pytest.raises(ValueError):Settings(rf_microwave_thresholds={'rsl_enter_dbm':-60,'rsl_clear_dbm':-70})
