from datetime import timedelta
from sqlalchemy import select,func
import pytest
from fastapi.testclient import TestClient
from app.models.entities import Device,TelemetrySample
from app.services.vendor_rf_observations import stage_observation,latest_observation
from app.services.rf_diagnostics import get_rf_diagnostics
from app.integrations.vendor_rf import VendorRFCollector
from app.api.deps import current_user
from app.core.database import get_db
from app.main import app
from test_rf_postgres_integration import postgres_db
from test_vendor_rf_foundation import NOW,PAYLOADS


def device(db,vendor='Cambium'):
    result=Device(source='synthetic',external_id='vendor-radio',name='Synthetic RF radio',vendor=vendor)
    db.add(result);db.flush();return result


@pytest.mark.parametrize('vendor',['ubiquiti','cambium','racom','aviat'])
def test_persist_round_trip_legacy_diagnostics_and_deduplication(postgres_db,vendor):
    db=postgres_db;d=device(db,vendor)
    collector=VendorRFCollector(vendor)
    observation=collector.normalize(PAYLOADS[vendor],observed_at=NOW,received_at=NOW)
    first=stage_observation(db,d,observation)
    repeated=collector.normalize(PAYLOADS[vendor],observed_at=NOW,received_at=NOW+timedelta(seconds=2))
    assert stage_observation(db,d,repeated).id==first.id
    assert db.scalar(select(func.count()).select_from(TelemetrySample))==1
    view=latest_observation(db,d.id,now=NOW)
    assert view['freshness']['status']=='fresh' and view['observation']['measurements']['link_capacity_mbps']==500
    assert get_rf_diagnostics(db,d.id,now=NOW)['rf_sample_count']==1
    assert latest_observation(db,d.id,now=NOW+timedelta(minutes=16))['freshness']['status']=='stale'


def test_status_only_and_empty_observations_never_make_rf_metrics_fresh(postgres_db):
    db=postgres_db;d=device(db,'Ubiquiti');collector=VendorRFCollector('ubiquiti')
    empty=collector.normalize({'radio':{}},observed_at=NOW,received_at=NOW)
    assert stage_observation(db,d,empty) is None
    down=collector.normalize({'radio':{'radio_status':'down'}},observed_at=NOW,received_at=NOW)
    stage_observation(db,d,down)
    assert latest_observation(db,d.id,now=NOW)['observation']['radio_status']=='down'
    assert get_rf_diagnostics(db,d.id,now=NOW)['freshness']['status']=='missing'


def test_conflicting_same_time_wrong_vendor_and_future_are_detected(postgres_db):
    db=postgres_db;d=device(db,'Ubiquiti');collector=VendorRFCollector('ubiquiti')
    observation=collector.normalize(PAYLOADS['ubiquiti'],observed_at=NOW,received_at=NOW)
    stage_observation(db,d,observation)
    changed={'radio':dict(PAYLOADS['ubiquiti']['radio'],rssi_dbm=-90)}
    with pytest.raises(ValueError):stage_observation(db,d,collector.normalize(changed,observed_at=NOW,received_at=NOW))
    with pytest.raises(ValueError):stage_observation(db,d,VendorRFCollector('aviat').normalize(PAYLOADS['aviat'],observed_at=NOW,received_at=NOW))
    future=collector.normalize(PAYLOADS['ubiquiti'],observed_at=NOW+timedelta(hours=1),received_at=NOW)
    stage_observation(db,d,future)
    assert latest_observation(db,d.id,now=NOW)['freshness']['status']=='future'


def test_legacy_fallback_and_invalid_payload_return_no_secrets(postgres_db):
    db=postgres_db;d=device(db)
    db.add(TelemetrySample(device_id=d.id,sampled_at=NOW,raw_data={'rf_metrics_v1':{'rssi_dbm':-65,'password':'canary'}}));db.flush()
    view=latest_observation(db,d.id,now=NOW)
    assert view['observation']['vendor']=='unknown' and view['observation']['received_at'] is None
    assert 'canary' not in str(view)
    db.add(TelemetrySample(device_id=d.id,sampled_at=NOW+timedelta(seconds=1),raw_data={'rf_observation_v1':{'password':'canary'}}));db.flush()
    view=latest_observation(db,d.id,now=NOW)
    assert not view['available'] and view['reason']=='invalid_observation' and 'canary' not in str(view)


def test_authenticated_api_and_read_only_profiles(postgres_db):
    db=postgres_db;d=device(db)
    stage_observation(db,d,VendorRFCollector('cambium').normalize(PAYLOADS['cambium'],observed_at=NOW,received_at=NOW))
    app.dependency_overrides[get_db]=lambda:db
    app.dependency_overrides[current_user]=lambda:type('Operator',(),{'role':'operator'})()
    try:
        client=TestClient(app)
        response=client.get(f'/api/devices/{d.id}/rf-observation')
        assert response.status_code==200 and response.json()['observation']['radio_status']=='up'
        profiles=client.get('/api/rf/vendor-profiles').json()
        assert len(profiles['profiles'])==4 and profiles['execution_enabled'] is False
        assert all(not p['enabled'] for p in profiles['profiles'])
    finally:app.dependency_overrides.clear()
