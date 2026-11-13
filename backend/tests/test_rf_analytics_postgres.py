from datetime import timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select,func,text
from fastapi.testclient import TestClient
from app.models.entities import RFAnalyticsPoint as Point,RFAnalyticsCheckpoint,TelemetrySample,Incident,AuditLog
from app.services.rf_analytics_projection import project_batch
from app.services.rf_analytics_queries import analytics_detail,frame_for,current_history
from app.services.rf_analytics_overview import analytics_overview
from app.rf_analytics_retention import retention
from app.core.config import settings
from app.core.database import get_db
from app.api.deps import current_user
from app.main import app
from app.services.radio_history import stage_radio
from test_rf_postgres_integration import postgres_db
from radio_fixtures import observation
from rf_analytics_fixtures import NOW,device,seed_radio,seed_microwave


def project(db):
    total=0
    while True:
        result=project_batch(db,limit=5000);total+=result['points']
        if result['processed']==0:return total


@pytest.mark.parametrize('scenario,classification',[('healthy','no_sustained_pattern'),('interference','suspected_interference'),
    ('congestion','likely_congestion'),('gradual','rf_degradation'),('sudden','rf_degradation'),('fallback','suspected_interference'),('outages','rf_degradation')])
def test_historical_scenarios_comparisons_buckets_and_evidence(postgres_db,scenario,classification):
    db=postgres_db;d=device(db);count=seed_radio(db,d,scenario=scenario)
    assert project(db)==count
    report=analytics_detail(db,d.id,now=NOW)
    series=report['series'][0]
    assert series['analysis']['classification']==classification
    assert report['execution_enabled'] is False and report['accuracy']=='not_validated_against_real_world_interference'
    assert set(report['comparisons'])=={'1h','24h','7d','30d'} and report['buckets']
    assert series['baseline']['signal_dbm']['ready']
    if scenario=='healthy':assert series['analysis']['score']['value']==100
    assert project(db)==0 and db.scalar(select(func.count()).select_from(Point))==count


def test_invalid_markers_future_stale_and_baseline_cold_start(postgres_db):
    db=postgres_db;d=device(db);seed_radio(db,d);project(db)
    db.add(TelemetrySample(device_id=d.id,sampled_at=NOW+timedelta(seconds=1),raw_data={'rf_radio_v2':{'password':'secret-canary'}}));db.flush();project(db)
    report=analytics_detail(db,d.id,now=NOW+timedelta(seconds=1))
    assert report['series'][0]['analysis']['freshness']=='invalid' and 'canary' not in str(report)
    assert report['series'][0]['analysis']['score']['value'] is None
    assert analytics_detail(db,d.id,now=NOW+timedelta(hours=2))['series'][0]['analysis']['freshness']=='stale'
    stage_radio(db,d,observation('epmp-4600-mib-v1',at=NOW+timedelta(hours=3)))
    project(db);assert analytics_detail(db,d.id,now=NOW)['series'][0]['analysis']['freshness']=='future'


def test_micro_carrier_aggregation_filters_and_related_incidents(postgres_db):
    db=postgres_db;a,z,link=seed_microwave(db);project(db)
    db.add(Incident(title='Synthetic existing backhaul incident',link_id=link.id,summary='Synthetic operator incident'));db.flush()
    overview=analytics_overview(db,now=NOW,link_id=link.id,vendor='racom',site_id=a.wisp_site_id)
    assert len(overview['series'])==2 and len(overview['alerts'])==1
    alert=overview['alerts'][0]
    assert alert['key']==f'rf-analytics:link:{link.id}' and z.id in alert['potentially_affected_device_ids']
    assert len(alert['related_incident_ids'])==1
    assert db.scalar(select(func.count()).select_from(Incident))==1  # Analysis never creates duplicates.
    assert analytics_overview(db,now=NOW,vendor='ubiquiti')['series']==[]
    assert analytics_overview(db,now=NOW,offset=100)['series']==[]


def test_recurring_windows_use_observed_days_and_local_time(postgres_db):
    db=postgres_db;d=device(db);seed_radio(db,d,scenario='recurring');project(db)
    detail=analytics_detail(db,d.id,window='30d',now=NOW)
    periods=[p for p in detail['recurring_periods'] if p['hour']==20]
    assert periods and periods[0]['adverse_days']==5 and periods[0]['observed_days']==7
    assert periods[0]['timezone']=='America/New_York' and periods[0]['root_cause']=='unconfirmed'


def test_sql_counter_rates_exact_reset_restart_and_duplicate_timestamps(postgres_db):
    db=postgres_db;d=device(db,vendor='Ubiquiti')
    values=[(0,2**60+3,100000),(10,2**60+8,101000),(20,1,102000),(30,2,100)]
    for seconds,count,uptime in values:stage_radio(db,d,observation(at=NOW+timedelta(seconds=seconds),tx_errors=count,uptime_seconds=uptime))
    project(db)
    frame=frame_for([d.id],NOW+timedelta(seconds=30),3600)
    history=current_history(db,frame,[d.id])[(d.id,'radio')]
    assert history[1]['tx_errors_per_second']==.5 and history[2]['tx_errors_per_second'] is None and history[3]['tx_errors_per_second'] is None
    # Different source IDs at one timestamp cannot manufacture consecutive samples.
    row=db.scalar(select(Point).where(Point.device_id==d.id).order_by(Point.id.desc()))
    sample=TelemetrySample(device_id=d.id,sampled_at=row.sampled_at,raw_data={'rf_metrics_v1':{'rssi_dbm':-60}});db.add(sample);db.flush();project(db)
    frame=frame_for([d.id],NOW+timedelta(seconds=30),3600)
    assert len(current_history(db,frame,[d.id])[(d.id,'radio')])==4


@pytest.mark.parametrize('states,expected',[([2,2,2,2],'down'),([1,2,1,2],'degraded')])
def test_historical_score_respects_observed_connection_states(postgres_db,states,expected):
    db=postgres_db;d=device(db)
    for i,state in enumerate(states):stage_radio(db,d,observation('epmp-4600-mib-v1',at=NOW+timedelta(seconds=i),radio_status=state))
    project(db);report=analytics_detail(db,d.id,now=NOW+timedelta(seconds=4))
    assert report['buckets'][-1]['radio_state']==expected and report['buckets'][-1]['health_score']['value']<100
    if expected=='down':assert report['buckets'][-1]['health_score']['value']<=20


def test_historical_bucket_score_uses_its_own_operating_context_baseline(postgres_db):
    db=postgres_db;d=device(db)
    for start,frequency,capacity in [(23,5800000000,100000000),(14,6000000000,500000000)]:
        for i in range(24):stage_radio(db,d,observation('epmp-4600-mib-v1',at=NOW-timedelta(hours=start)+timedelta(minutes=i*20),frequency_mhz=frequency,link_capacity_mbps=capacity))
    for i in range(6):stage_radio(db,d,observation('epmp-4600-mib-v1',at=NOW-timedelta(minutes=5-i),frequency_mhz=6000000000,link_capacity_mbps=500000000))
    project(db);report=analytics_detail(db,d.id,now=NOW)
    old=[b for b in report['buckets'] if b['frequency_mhz']==5800]
    current=[b for b in report['buckets'] if b['frequency_mhz']==6000]
    assert old and current and all(b['health_score']['value']==100 for b in old+current)


def test_projection_failure_isolation_and_independent_source_collection(postgres_db,monkeypatch):
    db=postgres_db;d=device(db,vendor='Ubiquiti')
    from app.services import rf_analytics_projection
    def failed(*args,**kwargs):raise RuntimeError('synthetic analytics failure')
    monkeypatch.setattr(rf_analytics_projection,'project_batch',failed)
    with pytest.raises(RuntimeError):rf_analytics_projection.project_batch(db)
    row=stage_radio(db,d,observation(at=NOW))
    assert row.id and db.scalar(select(func.count()).select_from(TelemetrySample))==1
    assert db.scalar(select(func.count()).select_from(Point))==0


def test_retention_dry_run_guard_batch_and_source_preservation(postgres_db,monkeypatch):
    db=postgres_db;d=device(db,vendor='Ubiquiti')
    for i in range(3):stage_radio(db,d,observation(at=NOW-timedelta(days=100,seconds=i)))
    project(db);monkeypatch.setattr(settings,'rf_analytics_retention_batch',2)
    assert retention(db,now=NOW)['eligible_batch']==2 and db.scalar(select(func.count()).select_from(Point))==3
    monkeypatch.setattr(settings,'rf_analytics_retention_enabled',False)
    with pytest.raises(ValueError):retention(db,apply=True,now=NOW)
    monkeypatch.setattr(settings,'rf_analytics_retention_enabled',True);monkeypatch.setattr(settings,'offline_mode',True);monkeypatch.setattr(settings,'app_env','staging')
    assert retention(db,apply=True,now=NOW)['deleted']==2
    assert db.scalar(select(func.count()).select_from(TelemetrySample))==3
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=='rf.analytics_retention'))==1


def test_projection_overlap_and_unknown_schema_checkpoint_progress(postgres_db):
    db=postgres_db;d=device(db)
    db.add(TelemetrySample(device_id=d.id,sampled_at=NOW,raw_data={'password':'secret-canary'}));db.flush()
    with db.get_bind().engine.connect() as other:
        other.execute(text('SELECT pg_advisory_xact_lock(890001)'))
        assert project_batch(db)['status']=='overlap_skipped'
    result=project_batch(db);assert result['skipped']==1 and result['points']==0
    assert project_batch(db)['status']=='current'


@pytest.mark.parametrize('schema',['legacy','observation_v1','radio_v2'])
def test_prior_RF_schema_projection_compatibility(postgres_db,schema):
    db=postgres_db;d=device(db,vendor='Ubiquiti')
    if schema=='legacy':
        from app.services.rf_persistence import store_rf_sample
        store_rf_sample(db,d,{'rssi_dbm':-60,'snr_db':30},sampled_at=NOW)
    elif schema=='observation_v1':
        from app.services.vendor_rf_observations import stage_observation
        from app.integrations.vendor_rf import VendorRFCollector
        stage_observation(db,d,VendorRFCollector('ubiquiti').normalize({'radio':{'rssi_dbm':-60,'snr_db':30}},observed_at=NOW,received_at=NOW))
    else:stage_radio(db,d,observation(at=NOW))
    assert project(db)==1
    point=db.scalar(select(Point));assert point.signal_dbm==-60 and point.snr_db==30
    if schema!='radio_v2':assert point.noise_floor_dbm is None


def test_invalid_fraction_rejects_baseline_and_corrupt_carriers_break_each_series(postgres_db):
    db=postgres_db;d=device(db)
    for i in range(24):
        when=NOW-timedelta(hours=23)+timedelta(minutes=i*50)
        if i%2:
            db.add(TelemetrySample(device_id=d.id,sampled_at=when,raw_data={'rf_radio_v2':{'token':'canary'}}));db.flush()
        else:stage_radio(db,d,observation('epmp-4600-mib-v1',at=when))
    stage_radio(db,d,observation('epmp-4600-mib-v1',at=NOW));project(db)
    report=analytics_detail(db,d.id,now=NOW)
    assert not report['series'][0]['baseline']['signal_dbm']['ready']
    assert report['series'][0]['baseline']['signal_dbm']['valid_fraction']==.5
    a,z,link=seed_microwave(db);project(db)
    db.add(TelemetrySample(device_id=a.id,sampled_at=NOW+timedelta(seconds=1),raw_data={'rf_microwave_v1':{'token':'canary'}}));db.flush();project(db)
    report=analytics_detail(db,a.id,now=NOW+timedelta(seconds=1))
    assert len(report['series'])==2 and all(s['analysis']['freshness']=='invalid' for s in report['series'])
    assert 'canary' not in str(report)


@pytest.mark.parametrize('size',[250,500,1000])
def test_analytics_scale_queries_are_bounded(postgres_db,monkeypatch,size):
    from app.rf_analytics_benchmark import measure
    monkeypatch.setattr(settings,'offline_mode',True);monkeypatch.setattr(settings,'app_env','staging')
    result=measure(postgres_db,devices=size,now=NOW)
    assert result['projection_points']==96*size and result['max_page_selects']<=9


def test_api_auth_bounds_sanitized_failure_and_no_write_routes(postgres_db,monkeypatch):
    db=postgres_db;d=device(db);seed_radio(db,d);project(db)
    assert TestClient(app).get('/api/rf/analytics/overview').status_code==401
    app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[current_user]=lambda:SimpleNamespace(role='viewer')
    try:
        client=TestClient(app);assert client.get('/api/rf/analytics/overview').status_code==403
        app.dependency_overrides[current_user]=lambda:SimpleNamespace(role='operator')
        assert client.get(f'/api/rf/analytics/devices/{d.id}').status_code==200
        assert client.get('/api/rf/analytics/overview?window=90d').status_code==422
        assert client.get('/api/rf/analytics/overview?limit=101').status_code==422
        assert client.get('/api/rf/analytics/devices/99999999').status_code==404
        assert client.post('/api/rf/analytics/overview',json={}).status_code==405
        from app.api import rf_analytics_routes
        monkeypatch.setattr(rf_analytics_routes,'analytics_detail',lambda *a,**k:(_ for _ in ()).throw(ValueError('secret-canary')))
        response=client.get(f'/api/rf/analytics/devices/{d.id}')
        assert response.status_code==503 and 'canary' not in response.text
    finally:app.dependency_overrides.clear()
