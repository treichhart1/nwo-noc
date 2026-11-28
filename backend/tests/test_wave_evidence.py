from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.services.wave_evidence import (MODELS, Metrics, Observation, Station, Weather, analyze,
    compatibility, projection, require_native_contract, stage_wave, weather_advisory)
from app.api.deps import current_user
from app.core.config import settings
from app.main import app

NOW = datetime(2026, 11, 28, tzinfo=timezone.utc)


def observation(**changes):
    return Observation(**(dict(model='Wave AP', observed_at=NOW, role='ap', availability='up') | changes))


@pytest.mark.parametrize('model', MODELS)
def test_model_inventory_is_not_native_certification(model):
    assert observation(model=model).model == model
    with pytest.raises(ValueError, match='blocked'):
        require_native_contract(model, '4.1.0')
    assert compatibility()['verified_firmware_contracts'] == []


@pytest.mark.parametrize('changes', [dict(model='unknown'), dict(firmware='4.1.0'), dict(source_kind='snmpv3'),
    dict(metrics={'rssi_dbm':float('nan')}), dict(metrics={'tx_bytes':True}), dict(metrics={'signal':-60}),
    dict(oid='1.3.6.1.4.1.41112'), dict(metrics={'tx_mcs':-1}), dict(metrics={'frequency_mhz':float('inf')}),
    dict(role='ptp'), dict(active_path='backup'), dict(backup_state='unsupported', backup_rssi_dbm=-70),
    dict(stations=[dict(device_id=1,state='up'),dict(device_id=1,state='down')]),
    dict(station_table_complete=True,connected_station_count=1)])
def test_malformed_or_unverified_contract_rejected(changes):
    with pytest.raises(ValidationError):
        observation(**changes)


def test_missing_not_zero_and_capacity_not_throughput():
    o=observation(metrics=Metrics(tx_capacity_mbps=1000,tx_throughput_mbps=20))
    p=projection(o)
    assert p['rf_metrics_v1']=={}
    assert p['directional_capacity']['tx_capacity_mbps']==1000
    assert p['internet_capacity_mbps'] is None
    assert 'rssi_dbm' in p['unsupported']
    assert analyze(None,o,now=NOW)['availability']=='up'
    assert analyze(None,o,now=NOW+timedelta(hours=1))['availability']=='unknown'


def test_backup_transition_is_degradation_not_outage_or_restoration():
    a=observation(active_path='primary',primary_state='up')
    b=observation(observed_at=NOW+timedelta(seconds=60),active_path='backup',backup_state='up')
    result=analyze(a,b,now=b.observed_at)
    assert result['availability']=='up' and result['backup_transition']=='primary_to_backup'
    assert result['recommendations'][0]['finding']=='operational_on_backup'
    assert not result['recovery_verified']
    c=observation(observed_at=NOW+timedelta(seconds=120),active_path='backup',backup_state='up')
    assert analyze(b,c,now=c.observed_at)['observed_backup_seconds']==60


def test_incomplete_subscriber_table_never_disconnects():
    a=observation(stations=[Station(device_id=4,state='up')],station_table_complete=True)
    b=observation(observed_at=NOW+timedelta(seconds=60))
    assert analyze(a,b,now=b.observed_at)['subscriber_events']==[]
    b=observation(observed_at=b.observed_at,station_table_complete=True)
    assert analyze(a,b,now=b.observed_at)['subscriber_events'][0]['state']=='absent'
    assert analyze(a,b,now=b.observed_at)['availability']=='up'
    c=observation(observed_at=b.observed_at,stations=[Station(device_id=4,state='down')])
    assert analyze(a,c,now=c.observed_at)['subscriber_events'][0]['state']=='down'


@pytest.mark.parametrize('uptime,counter,state', [(160,2000000,'continuous'),(10,2000000,'restart'),(160,1,'reset_or_wrap'),(None,2000000,'uptime_unknown')])
def test_counter_rates_require_continuity(uptime,counter,state):
    a=observation(metrics=Metrics(tx_bytes=1000000,uptime_seconds=100))
    b=observation(observed_at=NOW+timedelta(seconds=60),metrics=Metrics(tx_bytes=counter,uptime_seconds=uptime))
    result=analyze(a,b,now=b.observed_at)
    assert result['counter_state']==state
    assert bool(result['rates_mbps'])==(state=='continuous')


def test_weather_provenance_alignment_and_no_causality():
    o=observation(metrics=Metrics(rssi_dbm=-85))
    w=Weather(source_reference='operator-weather-1',observed_at=NOW,rainfall_mm_hour=15)
    assert weather_advisory(o,w)['conclusion']=='correlation_only_cause_unverified'
    assert weather_advisory(o,w.model_copy(update={'observed_at':NOW-timedelta(hours=1)}))['rainfall_mm_hour'] is None
    assert 'cause unknown' in analyze(None,o,now=NOW)['recommendations'][0]['advice']


def test_stage_requires_explicit_offline_authorization():
    with pytest.raises(ValueError,match='authorization'):
        stage_wave(None,1,observation(),now=NOW)


def test_gateway_private_authorization_and_no_native_activation(monkeypatch):
    client=TestClient(app)
    assert client.get('/api/rf/wave/compatibility').status_code==401
    app.dependency_overrides[current_user]=lambda:SimpleNamespace(id=1,role='viewer')
    try:
        assert client.get('/api/rf/wave/compatibility').status_code==403
        app.dependency_overrides[current_user]=lambda:SimpleNamespace(id=1,role='operator')
        assert client.get('/api/rf/wave/compatibility').json()['native_collection_enabled'] is False
        assert client.post('/api/rf/wave/devices/1/synthetic',json=observation().model_dump(mode='json')).status_code==403
        app.dependency_overrides[current_user]=lambda:SimpleNamespace(id=1,role='admin')
        monkeypatch.setattr(settings,'offline_mode',False)
        assert client.post('/api/rf/wave/devices/1/synthetic',json=observation().model_dump(mode='json')).status_code==403
        assert client.post('/api/rf/wave/native/enable',json={}).status_code==404
        assert client.post('/api/rf/wave/devices/1/synthetic',content=b'x'*262145).status_code==413
    finally:
        app.dependency_overrides.clear()
