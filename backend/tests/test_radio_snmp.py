import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from app.integrations.radio_snmp import RadioCollector, PysnmpGET, MIBMapping, SNMPCredentials, CAPABILITIES
from app.integrations.read_only import IntegrationFailure
from app.services.radio_observation import RadioObservation
from app.services.radio_history import analyze_radio, counter_delta, RadioThresholds
from radio_fixtures import mapping, policy, credentials, raw, observation, NOW


@pytest.mark.parametrize('profile', list(CAPABILITIES))
def test_profile_units_originals_and_firmware_capabilities(profile):
    o=observation(profile)
    assert o.vendor==('ubiquiti' if profile.startswith('airmax') else 'cambium')
    assert o.measurements.rssi_dbm==-60 and o.measurements.frequency_mhz==5800 and o.measurements.uptime_seconds==1000
    assert o.original_values['frequency_mhz']==5800000000 and o.original_units['frequency_mhz']=='hz'
    assert (o.gps_sync=='locked')==(profile=='airmax-ac-mib-v1')
    assert (o.measurements.mcs_index==9)==(profile=='epmp-4600-mib-v1')
    assert o.measurements.subscriber_count==(15 if profile.startswith('epmp') else None)
    if profile=='airmax-legacy-mib-v1': assert o.unavailable['link_capacity_mbps']=='unsupported'


@pytest.mark.parametrize('bad',[True,False,'password-canary',None,float('nan'),float('inf'),-9999,{},[],2**2000])
def test_malformed_numeric_is_invalid_without_raw_secret(bad):
    m=mapping();o=RadioCollector(m,policy()).normalize(raw(m,rssi_dbm=bad),observed_at=NOW,received_at=NOW)
    assert o.measurements.rssi_dbm is None and o.unavailable['rssi_dbm']=='invalid'
    assert 'rssi_dbm' not in o.original_values and 'canary' not in o.model_dump_json()


@pytest.mark.parametrize('field',['gps_sync','radio_status','modulation'])
def test_unknown_enum_and_secret_strings_not_retained(field):
    m=mapping('epmp-4600-mib-v1' if field=='modulation' else 'airmax-ac-mib-v1')
    o=RadioCollector(m,policy()).normalize(raw(m,**{field:'secret-canary'}),observed_at=NOW,received_at=NOW)
    assert getattr(o,field) is None and o.unavailable[field]=='invalid' and 'canary' not in o.model_dump_json()


def test_missing_unsupported_invalid_are_distinct_without_inferred_snr():
    m=mapping(omit={'snr_db'});v=raw(m);v.pop(m.fields['noise_floor_dbm'].oid);v[m.fields['rssi_dbm'].oid]='unsupported'
    o=RadioCollector(m,policy()).normalize(v,observed_at=NOW,received_at=NOW)
    assert o.unavailable['snr_db']=='unsupported' and o.unavailable['noise_floor_dbm']=='missing'
    assert o.unavailable['rssi_dbm']=='unsupported' and o.measurements.snr_db is None


@pytest.mark.parametrize('change',[{'enabled':False},{'admin_approved':False},{'mapping_validated':False},
    {'address':'radio.example.invalid'},{'address':'192.0.2.88'},
    {'address':'127.0.0.1','allowed_addresses':('127.0.0.1',)}, {'address':'224.0.0.1','allowed_addresses':('224.0.0.1',)}])
def test_policy_rejects_before_transport(change):
    transport=AsyncMock();collector=RadioCollector(mapping(),policy(**change),transport=transport)
    with pytest.raises(IntegrationFailure): asyncio.run(collector.collect(credentials()))
    transport.get.assert_not_called()


@pytest.mark.parametrize('code',['authentication_failed','unsupported','invalid_response','timeout','unavailable'])
def test_safe_poll_failures_and_bounded_retries(code):
    transport=AsyncMock();transport.get.side_effect=IntegrationFailure(code)
    collector=RadioCollector(mapping(),policy(),transport=transport,sleep=AsyncMock())
    with pytest.raises(IntegrationFailure,match=code): asyncio.run(collector.collect(credentials()))
    assert transport.get.call_count==(2 if code in {'timeout','unavailable'} else 1)


def test_timeout_recovery_exact_oid_allowlist_and_pacing():
    m=mapping();transport=AsyncMock();transport.get.side_effect=[TimeoutError('secret-canary'),raw(m)]
    collector=RadioCollector(m,policy(),transport=transport,sleep=AsyncMock())
    o=asyncio.run(collector.collect(credentials()))
    assert o.source_kind=='snmpv3_read_only' and o.timestamp_basis=='collector_receipt' and o.observed_at==o.received_at
    assert transport.get.call_count==2 and transport.get.call_args.args[0]=='192.0.2.87'
    assert set(transport.get.call_args.args[2])=={f.oid for f in m.fields.values()}
    with pytest.raises(IntegrationFailure,match='rate_limited'): asyncio.run(collector.collect(credentials()))
    assert transport.get.call_count==2


def test_private_credentials_and_invalid_mapping():
    assert 'synthetic' not in repr(credentials())
    for change in [{'profile':'invented'},{'mib_sha256':'bad'}]:
        with pytest.raises(ValidationError): MIBMapping.model_validate(mapping().model_dump()|change)
    data=mapping().model_dump();data['fields']['rssi_dbm']['unit']='hz'
    with pytest.raises(ValidationError): MIBMapping.model_validate(data)
    data=mapping().model_dump();data['fields']['password']={'oid':'1.3.6.1.4.1.32473.1.0','unit':'count'}
    with pytest.raises(ValidationError): MIBMapping.model_validate(data)
    with pytest.raises(ValidationError): SNMPCredentials(username='user',auth_key='short',priv_key='short')


def test_closed_schema_provenance_and_timestamp():
    data=observation().model_dump()
    for change in [{'vendor':'cambium'},{'observed_at':NOW.replace(tzinfo=None)}, {'password':'secret'},
                   {'unavailable':{}},{'original_values':{'password':'canary'}}]:
        with pytest.raises(ValidationError): RadioObservation.model_validate(data|change)


@pytest.mark.parametrize('field,value,reason',[('rssi_dbm',-85,'weak_rssi'),('snr_db',5,'low_snr'),
    ('noise_floor_dbm',-70,'high_noise'),('link_capacity_mbps',50000000,'low_capacity'),
    ('ccq_percent',40,'poor_quality'),('radio_status',2,'disconnected')])
def test_consecutive_degradation_and_recovery(field,value,reason):
    samples=[observation(at=NOW-timedelta(seconds=40-i*10),**{field:value}) for i in range(3)]
    assert reason not in analyze_radio(samples[:2],now=NOW)['reasons']
    assert reason in analyze_radio(samples,now=NOW)['reasons']
    samples.extend(observation(at=NOW+timedelta(seconds=i)) for i in (1,2,3))
    assert analyze_radio(samples,now=NOW+timedelta(seconds=4))['state']=='healthy'


def test_missing_stale_gaps_and_restarts_never_create_false_recovery():
    samples=[observation(at=NOW-timedelta(seconds=30-i*10),snr_db=5) for i in range(3)]
    assert analyze_radio(samples,now=NOW+timedelta(hours=1))['reasons']==[]
    missing=observation(at=NOW,snr_db=None)
    after=observation(at=NOW+timedelta(seconds=1),snr_db=5)
    assert analyze_radio(samples[:2]+[missing,after],now=after.observed_at)['reasons']==[]
    assert analyze_radio([missing],now=NOW)['state']=='unknown'
    gap=observation(at=NOW+timedelta(hours=1),snr_db=5)
    assert analyze_radio(samples+[gap],now=gap.observed_at)['reasons']==[]
    assert analyze_radio(samples+[observation(at=NOW,uptime_seconds=100,snr_db=5)],now=NOW)['reasons']==[]


def test_signal_noise_capacity_trends_and_intermittent_link():
    samples=[observation(at=NOW-timedelta(seconds=40))]+[observation(at=NOW-timedelta(seconds=30-i*10),
        rssi_dbm=-70,noise_floor_dbm=-88,link_capacity_mbps=250000000) for i in range(3)]
    assert {'signal_degradation','increasing_noise','capacity_degradation'}<=set(analyze_radio(samples,now=NOW)['reasons'])
    intermittent=[observation(at=NOW-timedelta(seconds=60-i*10),radio_status=s) for i,s in enumerate([1,2,1,2,1,2])]
    assert 'intermittent_link' in analyze_radio(intermittent,now=NOW)['reasons']
    intermittent.extend(observation(at=NOW+timedelta(seconds=i)) for i in (1,2,3))
    assert analyze_radio(intermittent,now=NOW+timedelta(seconds=4))['state']=='healthy'
    with pytest.raises(ValueError): analyze_radio(samples[::-1],now=NOW)
    with pytest.raises(ValidationError): RadioThresholds(consecutive_samples=1)


def test_counter_reset_restart_and_exact_64bit():
    a=observation(tx_errors=2**60+3)
    b=observation(at=NOW+timedelta(seconds=10),tx_errors=2**60+8,uptime_seconds=101000)
    assert counter_delta(a,b)['deltas']['tx_errors']==5
    reset=observation(at=NOW+timedelta(seconds=10),tx_errors=1,uptime_seconds=101000)
    assert counter_delta(a,reset)['state']=='counter_reset' and 'tx_errors' not in counter_delta(a,reset)['deltas']
    reboot=observation(at=NOW+timedelta(seconds=10),uptime_seconds=1)
    assert counter_delta(a,reboot)['state']=='device_restart' and counter_delta(a,reboot)['deltas']=={}
    assert counter_delta(a,observation(at=NOW+timedelta(seconds=10),uptime_seconds=None))['state']=='uptime_unknown'
    assert counter_delta(a,a)['state']=='out_of_order'


def test_pysnmp_wire_seam_authpriv_get_no_raw_strings(monkeypatch):
    from pysnmp.hlapi.v3arch import asyncio as snmp
    from pysnmp.proto.rfc1905 import NoSuchInstance
    from pysnmp.proto.rfc1902 import Counter64, OctetString, Integer32
    oids=[v.oid for v in mapping().fields.values()][:4]
    get=AsyncMock(return_value=(None,0,0,[(oids[0],Counter64(2**60+3)),(oids[1],NoSuchInstance('')),(oids[2],OctetString('password-canary')),(oids[3],Integer32(-60))]))
    monkeypatch.setattr(snmp,'get_cmd',get)
    monkeypatch.setattr(snmp.UdpTransportTarget,'create',AsyncMock(return_value=object()))
    assert asyncio.run(PysnmpGET().get('192.0.2.87',credentials(),oids,1))=={oids[0]:2**60+3,oids[1]:'unsupported',oids[2]:None,oids[3]:-60}
    assert get.call_args.args[1].security_level=='authPriv'
    assert not hasattr(PysnmpGET(),'set') and not hasattr(PysnmpGET(),'walk')
