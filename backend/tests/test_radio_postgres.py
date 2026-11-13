import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select, func
from fastapi.testclient import TestClient
from app.models.entities import Device, TelemetrySample, Incident, AuditLog, MaintenanceWindow
from app.services.radio_history import stage_radio, radio_history
from app.services.vendor_rf_observations import latest_observation
from app.services.rf_diagnostics import get_rf_diagnostics
from app.services.operational_readiness import snapshot
from app.services.incident_simulation import reconcile
from app.radio_poll import poll, PollConfig, private_json
from app.integrations.read_only import IntegrationFailure
from app.core.config import settings, Settings
from app.api.deps import current_user
from app.core.database import get_db
from app.main import app
from test_rf_postgres_integration import postgres_db
from radio_fixtures import observation, mapping, policy, credentials, raw, NOW


def device(db, vendor='Ubiquiti', **changes):
    d=Device(**(dict(source='synthetic',external_id='noc87-radio',name='Synthetic radio',vendor=vendor,
        authorized=True,enabled=True,ip_address='192.0.2.87',monitor_enabled=True,monitor_status='up',
        monitor_last_checked_at=NOW,monitor_last_up_at=NOW,monitor_consecutive_failures=0)|changes))
    db.add(d);db.flush();return d


@pytest.mark.parametrize('profile',['airmax-ac-mib-v1','airmax-legacy-mib-v1','epmp-4600-mib-v1','epmp-legacy-mib-v1'])
def test_history_roundtrip_dedupe_and_legacy_diagnostics(postgres_db,profile):
    db=postgres_db;d=device(db,'Cambium' if profile.startswith('epmp') else 'Ubiquiti')
    o=observation(profile);row=stage_radio(db,d,o)
    replay=o.model_copy(update={'received_at':NOW+timedelta(seconds=1)})
    assert stage_radio(db,d,replay).id==row.id
    assert db.scalar(select(func.count()).select_from(TelemetrySample))==1
    db.flush();db.expire_all()
    report=radio_history(db,d.id,now=NOW)
    assert report['samples'][0]['original_values']['frequency_mhz']==5800000000
    assert get_rf_diagnostics(db,d.id,now=NOW)['rf_sample_count']==1
    assert latest_observation(db,d.id,now=NOW)['observation']['schema_version']=='rf_radio_v2'
    assert radio_history(db,d.id,now=NOW+timedelta(hours=1))['diagnostics']['freshness']['status']=='stale'


def test_conflicts_vendor_mismatch_and_invalid_history_never_leak(postgres_db):
    db=postgres_db;d=device(db);stage_radio(db,d,observation())
    with pytest.raises(ValueError,match='Conflicting'):stage_radio(db,d,observation(snr_db=5))
    with pytest.raises(ValueError,match='vendor'):stage_radio(db,d,observation('epmp-4600-mib-v1'))
    db.add(TelemetrySample(device_id=d.id,sampled_at=NOW+timedelta(seconds=1),raw_data={'rf_radio_v2':{'password':'canary'}}));db.flush()
    result=radio_history(db,d.id,now=NOW+timedelta(seconds=2))
    assert result['invalid_samples']==1 and result['diagnostics']['state']=='unknown' and 'canary' not in str(result)


def test_status_only_not_numeric_freshness_and_future_is_unknown(postgres_db):
    from app.integrations.radio_snmp import RadioCollector
    db=postgres_db;d=device(db);m=mapping();only={m.fields['radio_status'].oid:2}
    o=RadioCollector(m,policy()).normalize(only,observed_at=NOW,received_at=NOW)
    row=stage_radio(db,d,o)
    assert 'rf_metrics_v1' not in row.raw_data and get_rf_diagnostics(db,d.id,now=NOW)['rf_sample_count']==0
    assert latest_observation(db,d.id,now=NOW)['observation']['radio_status']=='down'
    future=observation(at=NOW+timedelta(hours=1));stage_radio(db,d,future)
    assert radio_history(db,d.id,now=NOW)['diagnostics']['state']=='unknown'


def test_alert_dedup_incident_correlation_maintenance_and_confirmed_recovery(postgres_db,monkeypatch):
    db=postgres_db;monkeypatch.setattr(settings,'offline_mode',True);monkeypatch.setattr(settings,'app_env','staging')
    d=device(db)
    for i in range(3):stage_radio(db,d,observation(at=NOW-timedelta(seconds=30-i*10),snr_db=5))
    alerts=snapshot(db,now=NOW)['alerts'];assert len(alerts)==1 and alerts[0]['key']==f'rf:{d.id}'
    assert reconcile(db,now=NOW)['created']==1 and reconcile(db,now=NOW)['created']==0
    assert db.scalar(select(func.count()).select_from(Incident))==1
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=='incident.simulated_create'))==1
    # A missing measurement cannot resolve the open RF incident.
    stage_radio(db,d,observation(at=NOW,snr_db=None))
    assert reconcile(db,now=NOW)['resolved']==0
    for i in (1,2,3):stage_radio(db,d,observation(at=NOW+timedelta(seconds=i)))
    assert reconcile(db,now=NOW+timedelta(seconds=4))['resolved']==1
    parent=device(db,external_id='noc87-upstream',monitor_status='down');d.parent_device_id=parent.id
    for i in (5,6,7):stage_radio(db,d,observation(at=NOW+timedelta(seconds=i),snr_db=5))
    db.flush();view=snapshot(db,now=NOW+timedelta(seconds=8))
    assert not any(a['key']==f'rf:{d.id}' for a in view['alerts']) and view['root_outages']==1
    db.add(MaintenanceWindow(title='Synthetic maintenance',starts_at=NOW-timedelta(seconds=1),ends_at=NOW+timedelta(hours=1)))
    db.flush();assert snapshot(db,now=NOW+timedelta(seconds=8))['alerts']==[]


def test_explicit_poll_failure_recovery_pacing_audit_and_defaults(postgres_db,monkeypatch):
    db=postgres_db;d=device(db);m=mapping()
    config=PollConfig(device_id=d.id,mapping=m,policy=policy(),credentials_file='/synthetic-unused')
    transport=AsyncMock();transport.get.return_value=raw(m)
    monkeypatch.setattr(settings,'rf_snmp_enabled',False)
    with pytest.raises(IntegrationFailure,match='disabled'):asyncio.run(poll(db,config,credentials(),transport=transport))
    transport.get.assert_not_called()
    monkeypatch.setattr(settings,'rf_snmp_enabled',True);monkeypatch.setattr(settings,'integrations_admin_approved',True);monkeypatch.setattr(settings,'offline_mode',False)
    transport.get.side_effect=IntegrationFailure('authentication_failed')
    assert asyncio.run(poll(db,config,credentials(),transport=transport))['result']=='authentication_failed'
    assert radio_history(db,d.id,now=NOW)['last_attempt']['result']=='authentication_failed'
    with pytest.raises(IntegrationFailure,match='rate_limited'):asyncio.run(poll(db,config,credentials(),transport=transport))
    # Explicitly move the synthetic audit attempt back; no wall-clock sleeps.
    attempt=db.scalar(select(AuditLog).where(AuditLog.action=='rf.read_only_poll'));attempt.created_at-=timedelta(seconds=10);db.flush()
    transport.get.side_effect=None;transport.get.return_value=raw(m)
    assert asyncio.run(poll(db,config,credentials(),transport=transport))['staged']==1
    assert 'synthetic-auth-key' not in str(radio_history(db,d.id))


def test_authenticated_read_only_history_api(postgres_db):
    db=postgres_db;d=device(db);stage_radio(db,d,observation())
    assert TestClient(app).get(f'/api/devices/{d.id}/rf-radio-history').status_code==401
    app.dependency_overrides[get_db]=lambda:db
    app.dependency_overrides[current_user]=lambda:SimpleNamespace(role='viewer')
    try:
        client=TestClient(app)
        assert client.get(f'/api/devices/{d.id}/rf-radio-history').status_code==403
        app.dependency_overrides[current_user]=lambda:SimpleNamespace(role='operator')
        assert client.get(f'/api/devices/{d.id}/rf-radio-history').json()['samples'][0]['profile']=='airmax-ac-mib-v1'
        assert client.get('/api/devices/99999999/rf-radio-history').status_code==404
        assert client.get(f'/api/devices/{d.id}/rf-radio-history?limit=1001').status_code==422
        assert client.post(f'/api/devices/{d.id}/rf-radio-history',json={}).status_code==405
    finally:app.dependency_overrides.clear()


def test_poll_overlap_is_skipped_without_device_requests(postgres_db,monkeypatch):
    from sqlalchemy import text
    db=postgres_db;d=device(db)
    monkeypatch.setattr(settings,'rf_snmp_enabled',True);monkeypatch.setattr(settings,'offline_mode',False)
    monkeypatch.setattr(settings,'integrations_admin_approved',True)
    config=PollConfig(device_id=d.id,mapping=mapping(),policy=policy(),credentials_file='/unused')
    transport=AsyncMock()
    with db.get_bind().engine.connect() as other:
        other.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':871000+d.id})
        with pytest.raises(IntegrationFailure,match='overlap_skipped'):
            asyncio.run(poll(db,config,credentials(),transport=transport))
        transport.get.assert_not_called()


def test_private_configuration_permissions_symlinks_and_offline_rejection(tmp_path):
    path=tmp_path/'secret.json';path.write_text('{"username":"canary"}');path.chmod(0o600)
    assert private_json(str(path))=={'username':'canary'}
    path.chmod(0o644)
    with pytest.raises(IntegrationFailure,match='configuration_invalid'):private_json(str(path))
    path.chmod(0o600);link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(IntegrationFailure):private_json(str(link))
    with pytest.raises(ValueError):Settings(rf_snmp_enabled=True,offline_mode=True)
