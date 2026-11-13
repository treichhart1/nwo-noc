"""Synthetic unified operations, shared workflow and security regression tests."""
from datetime import timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient
from app.models.entities import (User,Device,NetworkLink,Sector,Incident,IncidentNote,AuditLog,RFAlertReview,RFRecommendation,RFAnalyticsPoint,TelemetrySample)
from app.core.database import get_db
from app.api.deps import current_user
from app.main import app
from app.services.rf_operations import overview,link_detail
from app.services.rf_analytics_projection import project_batch
from app.services.incident_workflow import apply_update
from app.services.rf_troubleshooting import evaluate
from test_rf_postgres_integration import postgres_db
from rf_analytics_fixtures import NOW,device,seed_radio,seed_microwave


def project(db):
    while project_batch(db,limit=5000)['processed']:pass


def technician(db,name='synthetic-tech',role='operator',active=True):
    u=User(username=name,email=name+'@synthetic.invalid',password_hash='not-a-login-hash',role=role,is_active=active)
    db.add(u);db.flush();return u


@pytest.fixture
def client(postgres_db):
    u=technician(postgres_db)
    app.dependency_overrides[get_db]=lambda:postgres_db
    app.dependency_overrides[current_user]=lambda:u
    with TestClient(app) as c:yield c
    app.dependency_overrides.clear()


def test_empty_snapshot_is_unknown_and_does_not_write(postgres_db):
    r=overview(postgres_db,now=NOW)
    assert r['summary']['state']=='unknown' and r['selected_devices']==0
    assert r['alerts']==r['inventory']==r['incidents']==[] and r['projection'] is None
    assert r['execution_enabled'] is False and postgres_db.scalar(select(func.count()).select_from(AuditLog))==0


@pytest.mark.parametrize('vendor',['Ubiquiti','Cambium'])
def test_site_vendor_search_scope_and_saved_recommendations(postgres_db,vendor):
    db=postgres_db;d=device(db,vendor=vendor);seed_radio(db,d,scenario='interference',profile='airmax-ac-mib-v1' if vendor=='Ubiquiti' else 'epmp-4600-mib-v1');project(db)
    evaluate(db,[d.id],user_id=None,now=NOW)
    db.add(Sector(name='Synthetic sector',site_id=d.wisp_site_id,device_id=d.id));db.flush()
    r=overview(db,now=NOW,site_id=d.wisp_site_id,q=d.name,vendor=vendor.casefold())
    assert r['selected_devices']==1 and r['alerts'] and r['recommendations']
    assert r['series'][0]['analysis']['classification']=='suspected_interference'
    assert r['sectors'][0]['health']=='degraded_observation' and r['summary']['rf_degraded_observations']==1
    assert r['dependencies']['impact'].startswith('Potential device')
    assert overview(db,now=NOW,q='no-such-device')['selected_devices']==0
    assert overview(db,now=NOW,vendor='aviat')['selected_devices']==0


def test_stale_invalid_missing_and_poll_failure_are_not_healthy(postgres_db):
    db=postgres_db;d=device(db);empty=device(db,external_id='empty');seed_radio(db,d);project(db)
    db.add(AuditLog(action='rf.read_only_poll',entity_type='device',entity_id=str(d.id),created_at=NOW,details={'result':'timeout','token':'never-display'}));db.flush()
    r=overview(db,now=NOW+timedelta(hours=2))
    rows={i['id']:i for i in r['inventory']}
    assert rows[d.id]['rf_freshness']==['stale'] and rows[empty.id]['rf_freshness']==['missing']
    assert rows[d.id]['last_poll_attempt']['result']=='timeout' and rows[d.id]['last_successful_poll_at'] is None
    assert 'never-display' not in str(r)
    db.add(TelemetrySample(device_id=d.id,sampled_at=NOW+timedelta(hours=3),raw_data={'rf_radio_v2':{'password':'canary'}}));db.flush();project(db)
    assert 'invalid' in overview(db,now=NOW+timedelta(hours=3))['inventory'][0]['rf_freshness']


def test_link_context_charts_and_unavailable_measurements(postgres_db):
    db=postgres_db;a,z,link=seed_microwave(db);project(db)
    r=link_detail(db,link.id,now=NOW)
    assert r['endpoints'][0]['buckets'] and r['context']['links'][0]['health']['rf_degraded_observations']==1
    s=r['endpoints'][0]['series'][0]
    assert s['inventory_link_matches'] is True and s['metric_status']['signal_dbm']=='fresh'
    assert s['metric_status']['quality_percent'] in {'missing','unsupported'}
    assert s['reported_alarms'] and r['read_only'] is True
    other=NetworkLink(name='Synthetic unrelated parallel link',a_site_id=a.wisp_site_id,z_site_id=z.wisp_site_id,a_device_id=a.id,z_device_id=z.id)
    db.add(other);db.flush()
    other_context=overview(db,link_id=other.id,now=NOW)
    assert not other_context['alerts'] and other_context['links'][0]['health']['rf_degraded_observations']==0
    assert other_context['links'][0]['health']['rf_unknown']==2


def test_unsupported_radio_metric_is_distinct_from_missing(postgres_db):
    from app.services.radio_history import stage_radio
    from radio_fixtures import observation
    db=postgres_db;a=device(db,external_id='a');z=device(db,external_id='z')
    link=NetworkLink(name='Synthetic radio link',a_site_id=a.wisp_site_id,z_site_id=z.wisp_site_id,a_device_id=a.id,z_device_id=z.id);db.add(link);db.flush()
    obs=observation('epmp-4600-mib-v1',at=NOW)
    values=obs.model_dump();values['measurements']['noise_floor_dbm']=None;values['unavailable']['noise_floor_dbm']='unsupported';values['original_values'].pop('noise_floor_dbm',None);values['original_units'].pop('noise_floor_dbm',None)
    from app.services.radio_observation import RadioObservation
    stage_radio(db,a,RadioObservation.model_validate(values));project(db)
    s=link_detail(db,link.id,now=NOW)['endpoints'][0]['series'][0]
    assert s['latest']['noise_floor_dbm'] is None and s['metric_status']['noise_floor_dbm']=='unsupported'
    assert s['metric_status']['signal_dbm']=='fresh'


def test_pagination_large_incident_list_no_duplicate_investigation_model(postgres_db,client):
    db=postgres_db;d=device(db)
    for i in range(130):db.add(Incident(title=f'Synthetic issue {i}',device_id=d.id,site_id=d.wisp_site_id,status='open'))
    db.flush()
    a=overview(db,now=NOW);b=overview(db,now=NOW,incident_offset=25)
    assert a['incident_total']==130 and len(a['incidents'])==25 and a['incident_has_more']
    assert not set(i['id'] for i in a['incidents']) & set(i['id'] for i in b['incidents'])
    assert overview(db,now=NOW,incident_status='closed')['incident_total']==0
    existing=client.get('/api/incidents?limit=1000');assert existing.status_code==200
    assert len(existing.json())==130 and existing.json()[0]['device_name']==d.name


def test_assignment_notes_findings_closure_revision_and_redacted_audit(client,postgres_db):
    db=postgres_db;u=technician(db,'assigned-tech');d=device(db);i=Incident(title='Synthetic RF investigation',device_id=d.id,site_id=d.wisp_site_id);db.add(i);db.flush()
    r=client.patch(f'/api/incidents/{i.id}',json={'expected_revision':1,'assigned_to_user_id':u.id,'status':'investigating','note':'<script>untrusted</script> visual inspection pending','findings':'Synthetic RF evidence reviewed'})
    assert r.status_code==200,r.text
    assert r.json()['revision']==2 and r.json()['assigned_to_user_id']==u.id
    stale=client.patch(f'/api/incidents/{i.id}',json={'expected_revision':1,'note':'must not be saved'})
    assert stale.status_code==409 and db.scalar(select(func.count()).select_from(IncidentNote))==1
    assert client.patch(f'/api/incidents/{i.id}',json={'status':'closed'}).status_code==409
    assert client.patch(f'/api/incidents/{i.id}',json={'status':'resolved','resolution':'Synthetic finding documented; no equipment actions'}).status_code==200
    assert client.patch(f'/api/incidents/{i.id}',json={'status':'closed'}).status_code==200
    detail=client.get(f'/api/rf/operations/incidents/{i.id}').json()
    assert detail['incident']['status']=='closed' and detail['notes'][0]['body'].startswith('<script>')
    assert overview(db,now=NOW)['incident_total']==0
    audit=client.get(f'/api/rf/operations/audit?entity_type=incident&entity_id={i.id}').json()
    assert len(audit['items'])==3 and audit['items'][0]['details']['note_added'] is True


@pytest.mark.parametrize('role,active',[('viewer',True),('operator',False),('unknown',True)])
def test_assignment_rejects_ineligible_users(client,postgres_db,role,active):
    db=postgres_db;u=technician(db,'ineligible',role,active);i=Incident(title='Synthetic issue');db.add(i);db.flush()
    assert client.patch(f'/api/incidents/{i.id}',json={'assigned_to_user_id':u.id}).status_code==409


def test_note_secret_validation_and_authorized_technicians_only(client,postgres_db):
    db=postgres_db;technician(db,'viewer','viewer');i=Incident(title='Synthetic investigation');db.add(i);db.flush()
    assert client.patch(f'/api/incidents/{i.id}',json={'note':'password=secret-canary'}).status_code==409
    payload=client.get('/api/rf/operations/technicians').json()
    assert all(u['username']!='viewer' for u in payload['items']) and 'password_hash' not in str(payload)


def test_complete_audit_and_note_cursor_pagination(client,postgres_db):
    db=postgres_db;i=Incident(title='Synthetic long history');db.add(i);db.flush()
    for n in range(150):
        db.add(IncidentNote(incident_id=i.id,body=f'Synthetic note {n}'))
        db.add(AuditLog(action='incident.update',entity_type='incident',entity_id=str(i.id),details={'index':n,'password':'redact-canary'}))
    db.flush()
    a=client.get(f'/api/rf/operations/audit?entity_type=incident&entity_id={i.id}').json()
    b=client.get(f'/api/rf/operations/audit?entity_type=incident&entity_id={i.id}&after_id={a["next_cursor"]}').json()
    assert len(a['items'])==100 and len(b['items'])==50 and not b['has_more']
    assert 'redact-canary' not in str(a) and a['items'][0]['details']['password']=='[REDACTED]'
    first=client.get(f'/api/rf/operations/incidents/{i.id}').json()
    second=client.get(f'/api/rf/operations/incidents/{i.id}?notes_after={first["next_notes_cursor"]}').json()
    assert len(first['notes'])==100 and len(second['notes'])==50


def test_alert_acknowledgment_and_incident_reuse(client,postgres_db,monkeypatch):
    db=postgres_db;d=device(db);seed_radio(db,d,scenario='interference');project(db)
    from app.api import rf_operations_routes as routes
    real=routes.overview
    seen=[]
    def observed(*args,**kw):
        seen.append(kw.get('window'));return real(*args,**kw,now=NOW)
    monkeypatch.setattr(routes,'overview',observed)
    a=overview(db,now=NOW)['alerts'][0]
    body={'alert_key':a['key'],'observed_at':a['observed_at'],'operation':'acknowledge','window':'30d'}
    assert client.post('/api/rf/operations/alerts/review',json=body).status_code==200
    assert overview(db,now=NOW)['alerts'][0]['review']['new_evidence_since_review'] is False
    body['operation']='open_incident'
    first=client.post('/api/rf/operations/alerts/review',json=body);assert first.status_code==200,first.text
    second=client.post('/api/rf/operations/alerts/review',json=body)
    assert first.json()['incident']['id']==second.json()['incident']['id']
    assert db.scalar(select(func.count()).select_from(Incident))==1
    assert seen and set(seen)=={'30d'}
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.entity_type=='rf_alert'))==3
    body['observed_at']=(NOW-timedelta(minutes=3)).isoformat()
    assert client.post('/api/rf/operations/alerts/review',json=body).status_code==409


@pytest.mark.parametrize('role',['viewer','unknown'])
def test_rf_operations_read_and_write_authorization(postgres_db,role):
    app.dependency_overrides[get_db]=lambda:postgres_db;app.dependency_overrides[current_user]=lambda:SimpleNamespace(id=None,role=role)
    try:
        with TestClient(app) as c:
            for path in ('','/sites/1','/links/1','/technicians','/incidents/1','/audit?entity_type=incident&entity_id=1'):
                assert c.get('/api/rf/operations'+path).status_code==403
            assert c.post('/api/rf/operations/alerts/review',json={'alert_key':'rf-analytics:device:1','observed_at':NOW.isoformat(),'operation':'acknowledge'}).status_code==403
            assert c.patch('/api/incidents/1',json={'status':'investigating'}).status_code==403
    finally:app.dependency_overrides.clear()


def test_unauthenticated_and_invalid_bounds():
    with TestClient(app) as c:
        assert c.get('/api/rf/operations').status_code==401


def test_database_failure_static_error_and_recovery(client,monkeypatch):
    from app.api import rf_operations_routes as routes
    real=routes.overview
    monkeypatch.setattr(routes,'overview',lambda *a,**kw:(_ for _ in ()).throw(OperationalError('query',{},Exception('private-db-canary'))))
    bad=client.get('/api/rf/operations');assert bad.status_code==503 and 'private-db-canary' not in bad.text
    monkeypatch.setattr(routes,'overview',real)
    assert client.get('/api/rf/operations').status_code==200
    assert client.get('/api/rf/operations?limit=101').status_code==422
    assert client.get('/api/rf/operations?window=90d').status_code==422
    assert client.get('/api/rf/operations/sites/999999').status_code==404
