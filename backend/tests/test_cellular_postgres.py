from datetime import timedelta
import pytest
from sqlalchemy import select,func,text
from app.models.entities import User,Device,TelemetrySample
from app.cellular.models import Dataset,SiteObservation
from app.cellular.service import (preview,import_dataset,layers,correlate,record_evidence,device_history)
from app.cellular.routes import native_preview,NativeReview
from app.ipam.models import VRF,Assignment
from app.ipam import discovery,service as ipam
from app.integrations.mikrotik_advanced import AdvancedSnapshot
from app.services.monitoring_collectors import RouterSnapshot,Envelope,stage
from test_mikrotik_advanced import dataset,evidence,NOW
from test_rf_postgres_integration import postgres_db


def actor_device(db):
    user=User(username='noc134-test',email='noc134@example.invalid',password_hash='unused-synthetic-fixture',role='admin',is_active=True)
    d=Device(source='synthetic',external_id='noc134-device',name='Synthetic cellular CPE',vendor='MikroTik',enabled=True)
    db.add_all([user,d]);db.flush();return user,d


def test_atomic_dataset_dedupe_attribution_and_bounded_layers(postgres_db):
    db=postgres_db;u,d=actor_device(db);b=dataset();b.preview_digest=preview(b,now=NOW)['digest']
    r=import_dataset(db,b,u.id,now=NOW)
    assert import_dataset(db,b,u.id,now=NOW)['duplicate']
    assert db.scalar(select(func.count()).select_from(SiteObservation))==1
    result=layers(db,west=-85,south=40,east=-83,north=42,now=NOW)
    assert result['attributions'][0]['license']=='CC-BY-SA-4.0'
    assert result['attributions'][0]['license_url']=='https://creativecommons.org/licenses/by-sa/4.0/'
    assert result['items'][0]['physical_tower_confirmed'] is False
    from app.cellular.service import qualification_advisory
    q=qualification_advisory(db,41.1,-84.2,'lte',now=NOW)
    assert q['survey_required'] and not q['service_guaranteed'] and len(q['candidates'])==1
    assert qualification_advisory(db,41.1,-84.2,'5g',now=NOW)['candidates']==[]
    assert not layers(db,west=-85,south=40,east=-83,north=42,mnc=99,now=NOW)['items']
    assert not layers(db,west=-85,south=40,east=-83,north=42,maximum_age_days=1,now=NOW)['items']
    with pytest.raises(ValueError):layers(db,west=-180,south=-90,east=180,north=90)
    with pytest.raises(ValueError):import_dataset(db,dataset(preview_digest='a'*64),u.id,now=NOW)


def test_cell_correlation_ambiguity_technology_and_no_coverage_promise(postgres_db):
    db=postgres_db;u,d=actor_device(db)
    for i,lon in enumerate(['-84.2','-84.3']):
        b=dataset(version='synthetic-'+str(i),csv_text=dataset().csv_text.replace('-84.2',lon));b.preview_digest=preview(b,now=NOW)['digest'];import_dataset(db,b,u.id,now=NOW)
    result=correlate(db,evidence(device_id=d.id),latitude=41,longitude=-84,now=NOW)
    assert result['status']=='ambiguous' and not result['confirmed_serving_tower']
    assert result['service_qualification']=='manual_survey_required'
    assert len(result['candidates'])==2 and result['candidates'][0]['distance_m']>0
    nr=evidence(device_id=d.id,technology='5G_SA',cell_identity_technology='NR')
    assert correlate(db,nr,now=NOW)['status']=='unknown'
    assert correlate(db,evidence(device_id=d.id),now=NOW+timedelta(hours=1))['status']=='unknown'


def test_recorded_history_dedupe_privacy_and_unknown_freshness(postgres_db):
    db=postgres_db;u,d=actor_device(db);e=evidence(device_id=d.id,registration='registered')
    row=record_evidence(db,e,u.id,now=NOW)
    assert record_evidence(db,e,u.id,now=NOW).id==row.id
    changed=evidence(device_id=d.id,observed_at=NOW+timedelta(seconds=60),cell='12346')
    record_evidence(db,changed,u.id,now=changed.observed_at)
    result=device_history(db,d.id,now=changed.observed_at)
    assert len(result['identity_changes'])==1 and result['samples'][0]['source_kind']=='synthetic'
    # The recorded-evidence writer uses the shared device lock until commit.
    with db.get_bind().engine.connect() as other:
        assert not other.execute(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':861000+d.id}).scalar()
    assert 'rf_metrics_v1' not in row.raw_data
    assert device_history(db,d.id,now=NOW+timedelta(hours=1))['registration']=='unknown'
    assert d.monitor_status=='unknown'
    from app.cellular.service import map_context
    d.latitude=41.1;d.longitude=-84.2;db.flush()
    context=map_context(db,now=changed.observed_at)
    assert context['devices'][0]['coordinate_source']=='existing_inventory'
    assert context['devices'][0]['source_kind']=='synthetic'
    assert not context['service_guaranteed']
    assert map_context(db,band='B99',now=changed.observed_at)['devices']==[]
    with pytest.raises(ValueError):record_evidence(db,e.model_copy(update={'cell':'9'}),u.id,now=NOW)


def test_dataset_and_evidence_transaction_rollback(postgres_db):
    db=postgres_db;u,d=actor_device(db);b=dataset();b.preview_digest=preview(b,now=NOW)['digest']
    with pytest.raises(RuntimeError):
        with db.begin_nested():
            import_dataset(db,b,u.id,now=NOW);record_evidence(db,evidence(device_id=d.id),u.id,now=NOW)
            raise RuntimeError('synthetic interruption')
    assert db.scalar(select(func.count()).select_from(Dataset))==0
    assert not device_history(db,d.id,now=NOW)['samples']


def test_native_durable_snapshot_roundtrip_and_ipam_domain_review(postgres_db,monkeypatch):
    import app.cellular.routes as routes
    from datetime import datetime,timezone
    db=postgres_db;u,d=actor_device(db);now=datetime.now(timezone.utc)
    red=VRF(name='red');blue=VRF(name='blue');db.add_all([red,blue]);db.flush()
    advanced=AdvancedSnapshot(routeros_major=7,resources={
        'addresses':[{'address':'192.0.2.1/24','interface':'ether1'},{'address':'192.0.2.1/24','interface':'ether2'}],
        'vrfs7':[{'name':'red','interfaces':'ether1'},{'name':'blue','interfaces':'ether2'}]})
    env=Envelope(device_id=d.id,format='mikrotik',sampled_at=now,payload=RouterSnapshot(advanced=advanced,routeros_version='7.20',uptime_seconds=100).model_dump(mode='json'))
    row=stage(db,d,env);assert stage(db,d,env).id==row.id
    assert row.raw_data['mikrotik_v1']['advanced']['resources']['vrfs7'][0]['name']=='red'
    b=NativeReview(device_id=d.id,sample_id=row.id,vrf_id=red.id,routing_domain='red',reason='Reviewed synthetic red routing domain')
    obj,report=native_preview(db,b)
    assert len(report['observations'])==1 and report['observations'][0]['evidence']['interface']=='ether1'
    discovery.ingest(db,obj,u.id)
    b.vrf_id=blue.id;b.routing_domain='blue'
    obj,report=native_preview(db,b);discovery.ingest(db,obj,u.id)
    assert ipam.reconcile(db,red.id)['items'][0]['classification']=='observed_unverified'
    assert ipam.reconcile(db,blue.id)['items'][0]['classification']=='observed_unverified'
    assert db.scalar(select(func.count()).select_from(Assignment))==0


def test_missing_vrf_evidence_does_not_guess_ipam_context(postgres_db):
    from datetime import datetime,timezone
    db=postgres_db;u,d=actor_device(db)
    a=AdvancedSnapshot(routeros_major=7,resources={'addresses':[{'address':'192.0.2.1/24','interface':'ether1'}]})
    row=stage(db,d,Envelope(device_id=d.id,format='mikrotik',sampled_at=datetime.now(timezone.utc),payload=RouterSnapshot(advanced=a,routeros_version='7.20').model_dump(mode='json')))
    with pytest.raises(ValueError,match='VRF'):
        native_preview(db,NativeReview(device_id=d.id,sample_id=row.id,vrf_id=1,reason='No unverified routing domain assignment'))
