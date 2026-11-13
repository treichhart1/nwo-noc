"""Vendor-shaped explicit-unit contracts, never live vendor/device protocols."""
from datetime import datetime,timedelta,timezone
import httpx
import pytest
from pydantic import ValidationError
from app.integrations.vendor_rf import VendorRFCollector,profiles_status
from app.integrations.read_only import ReadClient,ReadPolicy,UISP_PATHS,IntegrationFailure
from app.services.rf_observation import RFMeasurements,NormalizedRFTelemetry

NOW=datetime(2026,11,13,12,tzinfo=timezone.utc)
PAYLOADS={
 'ubiquiti':{'radio':{'rssi_dbm':-65,'snr_db':30,'noise_floor_dbm':-95,'frequency_mhz':5800,'channel_width_mhz':40,'link_capacity_mbps':500,'radio_status':'up'}},
 'cambium':{'rf':{'rssiDbm':-65,'snrDb':30,'noiseFloorDbm':-95,'frequencyHz':5800000000,'channelWidthHz':40000000,'capacityBps':500000000,'linkState':'UP'}},
 'racom':{'radio':{'rxLevelDbm':-65,'snrDb':30,'noiseDbm':-95,'frequencyKhz':5800000,'bandwidthKhz':40000,'capacityKbps':500000,'state':'running'}},
 'aviat':{'link':{'rxPowerDbm':-65,'snrDb':30,'noiseFloorDbm':-95,'frequencyGhz':5.8,'channelWidthMhz':40,'capacityMbps':500,'operationalStatus':'inService'}},
}


@pytest.mark.parametrize('vendor',list(PAYLOADS))
def test_vendor_units_normalize_to_same_model(vendor):
    observation=VendorRFCollector(vendor).normalize(PAYLOADS[vendor],observed_at=NOW,received_at=NOW)
    assert observation.measurements.model_dump(exclude_none=True)==dict(rssi_dbm=-65,snr_db=30,noise_floor_dbm=-95,
        frequency_mhz=5800,channel_width_mhz=40,link_capacity_mbps=500)
    assert observation.radio_status=='up' and observation.vendor==vendor
    assert observation.source_kind=='synthetic_export'


@pytest.mark.parametrize('vendor',list(PAYLOADS))
def test_missing_values_are_not_derived_or_zero_filled(vendor):
    collector=VendorRFCollector(vendor)
    observation=collector.normalize({collector.profile.node:{}},observed_at=NOW,received_at=NOW)
    assert observation.measurements.model_dump(exclude_none=True)=={} and observation.radio_status is None
    assert set(observation.unavailable)==set(RFMeasurements.model_fields)|{'radio_status'}
    assert 'missing' in observation.unavailable.values()


@pytest.mark.parametrize('bad',[True,False,'invalid',float('nan'),float('inf'),-9999])
def test_bad_numeric_values_are_invalid_not_fabricated(bad):
    observation=VendorRFCollector('ubiquiti').normalize({'radio':{'rssi_dbm':bad,'snr_db':30,'noise_floor_dbm':-90}},observed_at=NOW)
    assert observation.measurements.rssi_dbm is None and observation.unavailable['rssi_dbm']=='invalid'
    assert observation.measurements.snr_db==30


def test_unknown_units_status_and_secret_fields_never_leak():
    observation=VendorRFCollector('ubiquiti').normalize({'radio':{'signal':-60,'frequency':5800000000,'radio_status':'password-canary','password':'password-canary','token':'token-canary'}},observed_at=NOW)
    assert observation.measurements.model_dump(exclude_none=True)=={} and observation.radio_status is None
    assert 'canary' not in observation.model_dump_json()


@pytest.mark.parametrize('vendor',['cambium','racom','aviat'])
def test_unverified_wire_collectors_fail_without_any_request(vendor):
    with pytest.raises(IntegrationFailure,match='unsupported'):
        VendorRFCollector(vendor).collect_from_uisp(None,'id')
    profile=next(p for p in profiles_status() if p['vendor']==vendor)
    assert profile['enabled'] is False and profile['direct_transport']=='not_configured'


def test_ubiquiti_collector_reuses_only_guarded_get_and_source_timestamp():
    requests=[]
    payload={'identification':{'id':'radio-1'},**PAYLOADS['ubiquiti']}
    payload['radio']=dict(payload['radio'],sampled_at=NOW.isoformat())
    def respond(req):requests.append(req);return httpx.Response(200,json=[payload])
    policy=ReadPolicy('https://uisp.example.invalid',('https://uisp.example.invalid',),UISP_PATHS,True,True)
    with ReadClient(policy,transport=httpx.MockTransport(respond)) as client:
        observation=VendorRFCollector('ubiquiti').collect_from_uisp(client,'radio-1',received_at=NOW)
    assert observation.source_kind=='uisp_read_only' and observation.observed_at==NOW
    assert len(requests)==1 and requests[0].method=='GET' and requests[0].url.path=='/nms/api/v2.1/devices'


def test_missing_or_naive_source_timestamp_is_rejected():
    for timestamp in [None,NOW.replace(tzinfo=None)]:
        with pytest.raises(IntegrationFailure,match='invalid_response'):
            VendorRFCollector('ubiquiti').normalize(PAYLOADS['ubiquiti'],observed_at=timestamp)


def test_strict_schema_vendor_mismatch_and_boolean_measurement():
    with pytest.raises(ValidationError):RFMeasurements(rssi_dbm=True)
    with pytest.raises(ValidationError):RFMeasurements(rssi_dbm=-65,password='canary')
    observation=VendorRFCollector('ubiquiti').normalize(PAYLOADS['ubiquiti'],observed_at=NOW)
    data=observation.model_dump();data['vendor']='aviat'
    with pytest.raises(ValidationError):NormalizedRFTelemetry(**data)


def test_timestamps_normalize_to_utc_without_guessing_measurements():
    plus_two=NOW.astimezone(timezone(timedelta(hours=2)))
    observation=VendorRFCollector('ubiquiti').normalize(PAYLOADS['ubiquiti'],observed_at=plus_two,received_at=plus_two)
    assert observation.observed_at.tzinfo==timezone.utc and observation.observed_at==NOW
