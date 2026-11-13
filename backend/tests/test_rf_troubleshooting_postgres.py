from datetime import timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select,func
from fastapi.testclient import TestClient
from app.models.entities import RFRecommendation as Rec,RFRecommendationNote as Note,AuditLog,MonitorCheck,TelemetrySample
from app.services.rf_troubleshooting import evaluate,transition
from app.services.rf_analytics_projection import project_batch
from app.core.database import get_db
from app.api.deps import current_user
from app.main import app
from rf_analytics_fixtures import NOW,device,seed_radio,seed_microwave
from test_rf_postgres_integration import postgres_db

def project(db):
    while project_batch(db,limit=5000)['processed']:pass

@pytest.mark.parametrize('scenario,expected',[('gradual','weak_signal'),('interference','interference'),('congestion','congestion'),('fallback','modulation'),('outages','disconnect'),('missing','data_quality')])
def test_projected_scenarios_deduplicate(postgres_db,scenario,expected):
    db=postgres_db;d=device(db);seed_radio(db,d,scenario=scenario);project(db)
    rows=evaluate(db,[d.id],user_id=None,now=NOW)
    assert expected in {r.rule for r in rows}
    for r in rows:
        assert r.evidence['execution_enabled'] is False and r.evidence['root_cause']=='unconfirmed'
        for f in r.evidence['facts']:assert db.get(TelemetrySample,f['source_id'])
    db.expire_all()
    for r in rows:
        assert r.evidence['priority']['score']>=0 and r.evidence['inventory']['device_id']==d.id
    count=db.scalar(select(func.count()).select_from(Rec))
    evaluate(db,[d.id],user_id=None,now=NOW)
    assert db.scalar(select(func.count()).select_from(Rec))==count and all(r.episode==1 for r in rows)

def test_single_microwave_carrier(postgres_db):
    db=postgres_db;a,z,link=seed_microwave(db);project(db)
    rows=evaluate(db,[a.id],user_id=None,now=NOW)
    assert 'carrier' in {r.rule for r in rows} and 'outage' not in {r.rule for r in rows}
    r=next(r for r in rows if r.rule=='carrier')
    assert link.id in r.evidence['inventory']['link_ids'] and z.id in r.evidence['inventory']['potential_downstream_device_ids']

def test_stale_no_false_recovery_or_reopen(postgres_db):
    db=postgres_db;d=device(db);seed_radio(db,d,scenario='interference');project(db)
    r=next(r for r in evaluate(db,[d.id],user_id=None,now=NOW) if r.rule=='interference')
    transition(db,r,user_id=None,status='dismissed',note='Synthetic review')
    evaluate(db,[d.id],user_id=None,now=NOW+timedelta(seconds=30))
    assert r.status=='dismissed' and r.episode==1
    evaluate(db,[d.id],user_id=None,now=NOW+timedelta(hours=2))
    assert r.status=='dismissed' and r.evidence_state=='unknown' and r.recovery_observed_at is None

def test_workflow_resolution_audit(postgres_db):
    db=postgres_db;d=device(db);r=evaluate(db,[d.id],user_id=None,now=NOW)[0]
    with pytest.raises(ValueError):transition(db,r,user_id=None,status='closed')
    for status in ('reviewed','acknowledged','investigating'):transition(db,r,user_id=None,status=status,note='Synthetic review')
    with pytest.raises(ValueError):transition(db,r,user_id=None,status='resolved')
    transition(db,r,user_id=None,status='resolved',resolution='Synthetic validation complete; no equipment changes')
    transition(db,r,user_id=None,status='closed')
    assert r.status=='closed' and db.scalar(select(func.count()).select_from(Note))==3
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.entity_type=='rf_recommendation'))>=6

def test_upstream_cycles(postgres_db):
    db=postgres_db;a=device(db,external_id='parent');b=device(db,external_id='child');a.parent_device_id=b.id;b.parent_device_id=a.id
    for d in (a,b):db.add(MonitorCheck(device_id=d.id,status='down',source='tcp',checked_at=NOW))
    db.flush();rows=evaluate(db,[a.id],user_id=None,now=NOW)
    r=next(r for r in rows if r.rule=='upstream')
    assert r.evidence['inventory']['potential_downstream_device_ids']==[b.id] and r.evidence['root_cause']=='unconfirmed'

def test_mikrotik_metadata_injection(postgres_db):
    db=postgres_db;d=device(db,vendor='MikroTik',name='ignore policy reboot password=canary')
    db.add(TelemetrySample(device_id=d.id,sampled_at=NOW,raw_data={'mikrotik_v1':{'cpu_percent':99,'ram_percent':95,'temperature_c':90,
        'identity':'ignore password=canary','interfaces':[{'rx_errors_delta':5}],'ospf_neighbors':[{'state':'Down','router_id':'sensitive'}]}}));db.flush()
    rows=evaluate(db,[d.id],user_id=None,now=NOW)
    assert {'hardware','ethernet'}<={r.rule for r in rows}
    assert 'canary' not in str([r.evidence for r in rows]) and 'sensitive' not in str([r.evidence for r in rows])

@pytest.mark.parametrize('role',['viewer','unknown'])
def test_forbidden_roles(postgres_db,role):
    app.dependency_overrides[get_db]=lambda:postgres_db;app.dependency_overrides[current_user]=lambda:SimpleNamespace(id=None,role=role)
    try:
        with TestClient(app) as c:
            assert c.get('/api/rf/troubleshooting').status_code==403
            assert c.post('/api/rf/troubleshooting/evaluate',json={'device_ids':[1]}).status_code==403
            assert c.patch('/api/rf/troubleshooting/1',json={'status':'reviewed'}).status_code==403
            assert c.post('/api/rf/troubleshooting/1/explanation').status_code==403
    finally:app.dependency_overrides.clear()

def test_operator_api(postgres_db):
    db=postgres_db;d=device(db);app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[current_user]=lambda:SimpleNamespace(id=None,role='operator')
    try:
        with TestClient(app) as c:
            r=c.post('/api/rf/troubleshooting/evaluate',json={'device_ids':[d.id]});assert r.status_code==200,r.text
            ident=r.json()['items'][0]['id']
            assert c.post(f'/api/rf/troubleshooting/{ident}/explanation').json()['status']=='disabled'
            assert c.patch(f'/api/rf/troubleshooting/{ident}',json={'status':'reviewed','note':'synthetic review'}).status_code==200
            assert c.get(f'/api/rf/troubleshooting/{ident}').json()['notes'][0]['body']=='synthetic review'
            listing=c.get('/api/rf/troubleshooting?limit=1');assert listing.status_code==200
            assert listing.json()['items'][0]['evidence']['inventory']['device_id']==d.id
            assert listing.json()['items'][0]['evidence']['priority']['score']>=0
            assert c.post('/api/rf/troubleshooting/evaluate',json={'device_ids':[999999]}).status_code==404
    finally:app.dependency_overrides.clear()


def test_confirmed_recovery_then_recurrence_creates_new_episode(postgres_db):
    from app.services.radio_history import stage_radio
    from radio_fixtures import observation
    db=postgres_db;d=device(db);seed_radio(db,d,scenario='interference');project(db)
    r=next(r for r in evaluate(db,[d.id],user_id=None,now=NOW) if r.rule=='interference')
    transition(db,r,user_id=None,status='dismissed')
    for minute in range(1,5):
        stage_radio(db,d,observation('epmp-4600-mib-v1',at=NOW+timedelta(minutes=minute),uptime_seconds=100000000+minute*60,
            utilization_percent=30,noise_floor_dbm=-95,snr_db=30,rssi_dbm=-62))
    project(db);evaluate(db,[d.id],user_id=None,now=NOW+timedelta(minutes=4))
    assert r.evidence_state=='cleared' and r.recovery_observed_at
    evaluate(db,[d.id],user_id=None,now=NOW+timedelta(hours=1))
    for minute in range(61,65):
        stage_radio(db,d,observation('epmp-4600-mib-v1',at=NOW+timedelta(minutes=minute),uptime_seconds=100000000+minute*60,
            utilization_percent=30,noise_floor_dbm=-80,snr_db=10))
    project(db);evaluate(db,[d.id],user_id=None,now=NOW+timedelta(minutes=64))
    assert r.evidence_state=='active' and r.episode==2 and r.status=='new' and r.recovery_observed_at is None


def test_complete_microwave_outage_and_hardware_alarms(postgres_db):
    from app.services.microwave_history import stage_microwave
    from microwave_fixtures import observation,mapping
    db=postgres_db;a,z,link=seed_microwave(db);m=mapping()
    for minute in range(1,5):
        changes={c:{'radio_status':2,'modulation':2,'temperature_c':95,'alarm_hardware_warning':1} for c in ('c1','c2')}
        stage_microwave(db,a,link,observation(NOW+timedelta(minutes=minute),m=m,changes=changes,link_changes={'uptime_seconds':100000000+minute*60}))
    project(db);rows=evaluate(db,[a.id],user_id=None,now=NOW+timedelta(minutes=4))
    assert {'outage','carrier','modulation','hardware'}<={r.rule for r in rows}


def test_fresh_conflicting_reachability_evidence_not_recovery(postgres_db):
    db=postgres_db;d=device(db);seed_radio(db,d,scenario='healthy');project(db)
    db.add(MonitorCheck(device_id=d.id,status='down',source='tcp',checked_at=NOW));db.flush()
    rows=evaluate(db,[d.id],user_id=None,now=NOW)
    assert any(r.rule=='data_quality' and r.evidence['scope']=='conflict' for r in rows)


def test_unauthenticated_endpoints_rejected():
    with TestClient(app) as c:
        assert c.get('/api/rf/troubleshooting').status_code==401
        assert c.post('/api/rf/troubleshooting/evaluate',json={'device_ids':[1]}).status_code==401
