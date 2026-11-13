import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from app.integrations.microwave_snmp import MicrowaveCollector,MicrowaveMapping
from app.integrations.radio_snmp import PysnmpGET
from app.integrations.read_only import IntegrationFailure
from app.services.microwave_model import PROFILES,MicrowaveObservation
from app.services.microwave_diagnostics import analyze_microwave,continuity
from app.services.microwave_policy import MicrowaveThresholds
from microwave_fixtures import mapping,observation,raw,NOW
from radio_fixtures import policy,credentials


def series(n=6,*,m=None,changes=None,link_changes=None):
    return [observation(NOW-timedelta(seconds=(n-1-i)*10),m=m,changes=changes,
        link_changes={'uptime_seconds':100000+i*1000}|(link_changes or {})) for i in range(n)]


@pytest.mark.parametrize('profile',list(PROFILES))
def test_profile_healthy_dual_channel_units_originals_and_aggregate(profile):
    m=mapping(profile);o=observation(m=m);r=analyze_microwave(series(m=m),now=NOW)
    assert r['state']=='healthy' and r['rf_state']=='healthy' and r['ethernet_state']=='up'
    assert o.carriers['c1'].measurements.bandwidth_mhz==80 and o.carriers['c2'].measurements.bandwidth_mhz==80
    assert o.carriers['c1'].measurements.local_rsl_dbm==-60 and o.carriers['c1'].measurements.remote_rsl_dbm==-62
    assert o.original_values['c1.bandwidth_mhz']==80000 and o.original_units['c1.bandwidth_mhz']=='khz'
    assert o.measurements.reported_capacity_mbps==950  # Deliberately not 500+500.
    assert o.firmware_version=='3.2.1' and o.hardware_id==88
    assert r['carriers']['c1']['fade_margin_db'] is None and not r['execution_enabled']


@pytest.mark.parametrize('bad',[True,False,None,'password-canary',{},[],float('nan'),float('inf'),-9999,2**2000])
def test_invalid_scalars_never_retained_or_zero_filled(bad):
    o=observation(changes={'c1':{'local_rsl_dbm':bad}})
    assert o.carriers['c1'].measurements.local_rsl_dbm is None
    assert o.carriers['c1'].unavailable['local_rsl_dbm']=='invalid' and 'c1.local_rsl_dbm' not in o.original_values
    assert 'canary' not in o.model_dump_json()


def test_missing_unsupported_invalid_fields_and_alarms_are_distinct():
    m=mapping(omit={'snr_db'});v=raw(m);v.pop(m.carriers['c1']['remote_rsl_dbm'].oid)
    v[m.carriers['c1']['alarm_link_loss'].oid]='unsupported';v[m.carriers['c1']['modulation'].oid]='secret'
    o=MicrowaveCollector(m,policy()).normalize(v,observed_at=NOW,received_at=NOW)
    assert o.carriers['c1'].unavailable['snr_db']=='unsupported'
    assert o.carriers['c1'].unavailable['remote_rsl_dbm']=='missing'
    assert o.carriers['c1'].unavailable['alarm_link_loss']=='unsupported'
    assert o.carriers['c1'].unavailable['modulation']=='invalid' and 'secret' not in o.model_dump_json()


@pytest.mark.parametrize('field,value,reason',[('local_rsl_dbm',-85,'weak_rsl'),('modulation',2,'modulation_low'),
    ('capacity_mbps',50000000,'capacity_low'),('temperature_c',85,'temperature_high'),
    ('availability_percent',95,'availability_low'),('alarm_temperature_high',1,'alarm_temperature_high'),
    ('radio_status',2,'carrier_down')])
def test_persistent_degradation_and_carrier_failures(field,value,reason):
    samples=series(3,changes={'c1':{field:value}})
    assert f'c1:{reason}' not in analyze_microwave(samples[:2],now=NOW)['reasons']
    report=analyze_microwave(samples,now=NOW)
    assert f'c1:{reason}' in report['reasons'] and report['state']=='degraded'
    assert report['carriers']['c2']['state']!='down' and report['root_cause']=='unconfirmed'


def test_complete_radio_outage_is_distinct_from_ethernet_and_collection_failure():
    radio=analyze_microwave(series(changes={'c1':{'radio_status':2},'c2':{'radio_status':2}}),now=NOW)
    assert radio['state']=='outage' and radio['rf_state']=='down' and radio['ethernet_state']=='up'
    ethernet=analyze_microwave(series(link_changes={'ethernet_status':2}),now=NOW)
    assert ethernet['state']=='degraded' and ethernet['rf_state']=='healthy' and ethernet['ethernet_state']=='down'
    alarm=analyze_microwave(series(changes={'c1':{'alarm_ethernet_fault':1}}),now=NOW)
    assert alarm['state']=='degraded' and alarm['rf_state']=='healthy' and alarm['ethernet_state']=='conflicting'
    stale=analyze_microwave(series(),now=NOW+timedelta(hours=1))
    assert stale['state']=='unknown' and stale['freshness']['status']=='stale' and stale['reasons']==[]


def test_hysteresis_neutral_band_missing_and_clear_recovery():
    samples=series(changes={'c1':{'local_rsl_dbm':-80}})
    samples.extend(observation(NOW+timedelta(seconds=i),changes={'c1':{'local_rsl_dbm':-74}},link_changes={'uptime_seconds':106000+i*100}) for i in (1,2,3))
    assert 'c1:weak_rsl' in analyze_microwave(samples,now=NOW+timedelta(seconds=3))['reasons']
    samples.append(observation(NOW+timedelta(seconds=4),changes={'c1':{'local_rsl_dbm':None}},link_changes={'uptime_seconds':106400}))
    assert analyze_microwave(samples,now=NOW+timedelta(seconds=4))['state']=='unknown'
    samples.extend(observation(NOW+timedelta(seconds=i),link_changes={'uptime_seconds':106000+i*100}) for i in (5,6,7,8))
    # Relative signal change also clears after recovery to the original baseline.
    assert analyze_microwave(samples,now=NOW+timedelta(seconds=8))['state']=='healthy'


def test_reference_requires_matching_engineered_modulation_and_bandwidth():
    m=mapping(reference=True)
    r=analyze_microwave(series(m=m,changes={'c1':{'local_rsl_dbm':-85}}),now=NOW)
    assert r['carriers']['c1']['fade_margin_db']==5 and 'c1:low_fade_margin' in r['reasons']
    incompatible=analyze_microwave(series(m=m,changes={'c1':{'local_rsl_dbm':-85,'modulation':2}}),now=NOW)
    assert incompatible['carriers']['c1']['fade_margin_db'] is None and not incompatible['carriers']['c1']['reference_valid']
    assert 'c1:low_fade_margin' not in incompatible['reasons']


def test_declining_signal_capacity_error_rates_and_link_instability():
    samples=series(2)
    for i in range(1,5):samples.append(observation(NOW+timedelta(seconds=i*10),changes={'c1':{'local_rsl_dbm':-70,'modulation':4,'capacity_mbps':250000000,'errors':10+i*i*300}},link_changes={'uptime_seconds':102000+i*1000}))
    r=analyze_microwave(samples,now=NOW+timedelta(seconds=40))
    assert {'c1:signal_decline','c1:capacity_reduction','c1:modulation_fallback','c1:errors_increasing','c1:high_error_rate'}<=set(r['reasons'])
    unstable=[observation(NOW+timedelta(seconds=i),changes={'c1':{'radio_status':s}}) for i,s in enumerate([1,2,1,2,1,2])]
    assert 'c1:link_instability' in analyze_microwave(unstable,now=NOW+timedelta(seconds=6))['reasons']
    unstable.extend(observation(NOW+timedelta(seconds=i)) for i in (7,8,9,10))
    assert analyze_microwave(unstable,now=NOW+timedelta(seconds=10))['state']=='healthy'


def test_counter_resets_restart_mapping_changes_and_exact_64bit_delta():
    a=observation(changes={'c1':{'errors':2**60+3}})
    b=observation(NOW+timedelta(seconds=10),changes={'c1':{'errors':2**60+8}},link_changes={'uptime_seconds':101000})
    assert continuity(a,b,'c1')['delta_errors']==5 and continuity(a,b,'c1')['errors_per_second']==.5
    reset=observation(NOW+timedelta(seconds=10),changes={'c1':{'errors':1}},link_changes={'uptime_seconds':101000})
    assert continuity(a,reset,'c1')['state']=='counter_reset' and continuity(a,reset,'c1')['errors_per_second'] is None
    reboot=observation(NOW+timedelta(seconds=10),link_changes={'uptime_seconds':1})
    assert continuity(a,reboot,'c1')['state']=='device_restart'
    assert continuity(a,observation(NOW+timedelta(seconds=10),m=mapping(omit={'snr_db'})),'c1')['state']=='mapping_changed'
    assert continuity(a,observation(NOW+timedelta(seconds=10),link_changes={'uptime_seconds':None}),'c1')['state']=='uptime_unknown'


def test_restarts_gaps_missing_and_future_never_manufacture_current_faults():
    samples=series(changes={'c1':{'local_rsl_dbm':-85}})
    reboot=observation(NOW+timedelta(seconds=1),link_changes={'uptime_seconds':1},changes={'c1':{'local_rsl_dbm':-85}})
    assert analyze_microwave(samples+[reboot],now=reboot.observed_at)['state']=='unknown'
    gap=observation(NOW+timedelta(hours=1),changes={'c1':{'local_rsl_dbm':-85}})
    assert analyze_microwave(samples+[gap],now=gap.observed_at)['state']=='unknown'
    assert analyze_microwave([gap],now=NOW)['freshness']['status']=='future'
    assert analyze_microwave(series(m=mapping(omit={'local_rsl_dbm'})),now=NOW)['state']=='unknown'


@pytest.mark.parametrize('profile,carriers',[('racom-ray3-mib-v1',3),('aviat-multicarrier-mib-v1',9)])
def test_unsupported_carrier_layouts_are_rejected(profile,carriers):
    with pytest.raises(ValidationError):mapping(profile,carriers=carriers)


def test_invalid_units_oids_provenance_and_thresholds_fail_closed():
    data=mapping().model_dump();data['carriers']['c1']['local_rsl_dbm']['unit']='hz'
    with pytest.raises(ValidationError):MicrowaveMapping.model_validate(data)
    data=mapping().model_dump();data['link_fields']['password']={'oid':'1.3.6.1.4.1.32473.1.0','unit':'count'}
    with pytest.raises(ValidationError):MicrowaveMapping.model_validate(data)
    for change in [{'vendor':'aviat'},{'observed_at':NOW.replace(tzinfo=None)},{'original_values':{'password':'canary'}}]:
        with pytest.raises(ValidationError):MicrowaveObservation.model_validate(observation().model_dump()|change)
    for change in [{'consecutive_samples':1},{'rsl_clear_dbm':-80},{'temperature_clear_c':90},{'stale_after_seconds':1}]:
        with pytest.raises(ValidationError):MicrowaveThresholds(**change)


@pytest.mark.parametrize('code',['timeout','unavailable','authentication_failed','unsupported','invalid_response'])
def test_safe_retries_and_failure_codes(code):
    t=AsyncMock();t.get.side_effect=IntegrationFailure(code)
    c=MicrowaveCollector(mapping(),policy(),transport=t,sleep=AsyncMock())
    with pytest.raises(IntegrationFailure,match=code):asyncio.run(c.collect(credentials()))
    assert t.get.call_count==(2 if code in {'timeout','unavailable'} else 1)


def test_batched_allowlisted_get_recovery_and_partial_batch_failure():
    m=mapping('aviat-multicarrier-mib-v1',carriers=8);v=raw(m);requests=[]
    async def get(address,creds,oids,timeout):
        requests.append(oids);return {k:v[k] for k in oids}
    c=MicrowaveCollector(m,policy(),transport=type('Mock',(),{'get':staticmethod(get)})(),sleep=AsyncMock())
    obs=asyncio.run(c.collect(credentials()))
    assert len(obs.carriers)==8 and obs.source_kind=='snmpv3_read_only'
    assert len(requests)==6 and all(len(r)<=32 for r in requests)
    assert set(k for r in requests for k in r)=={f.oid for f in m.fields.values()}
    with pytest.raises(IntegrationFailure,match='rate_limited'):asyncio.run(c.collect(credentials()))
    t=AsyncMock();t.get.side_effect=[TimeoutError(),{k:v[k] for k in requests[0]}]+[IntegrationFailure('unavailable')]*2
    c=MicrowaveCollector(m,policy(),transport=t,sleep=AsyncMock())
    with pytest.raises(IntegrationFailure,match='unavailable'):asyncio.run(c.collect(credentials()))


def test_disabled_before_transport_and_recovery_after_timeout():
    t=AsyncMock();m=mapping();c=MicrowaveCollector(m,policy(enabled=False),transport=t)
    with pytest.raises(IntegrationFailure,match='disabled'):asyncio.run(c.collect(credentials()))
    t.get.assert_not_called()
    attempts=0;v=raw(m)
    async def get(address,creds,oids,timeout):
        nonlocal attempts
        attempts+=1
        if attempts==1:raise TimeoutError('password-canary')
        return {k:v[k] for k in oids}
    c=MicrowaveCollector(m,policy(),transport=type('Mock',(),{'get':staticmethod(get)})(),sleep=AsyncMock())
    assert asyncio.run(c.collect(credentials())).vendor=='racom' and attempts==3


def test_firmware_text_boundary_only_accepts_version_from_explicit_oid(monkeypatch):
    from pysnmp.hlapi.v3arch import asyncio as snmp
    from pysnmp.proto.rfc1902 import OctetString
    oid='1.3.6.1.4.1.32473.88.1.0';other='1.3.6.1.4.1.32473.88.2.0'
    call=AsyncMock(return_value=(None,0,0,[(oid,OctetString('3.2.1-build4')),(other,OctetString('password-canary'))]))
    monkeypatch.setattr(snmp,'get_cmd',call);monkeypatch.setattr(snmp.UdpTransportTarget,'create',AsyncMock(return_value=object()))
    assert asyncio.run(PysnmpGET(version_oids=[oid]).get('192.0.2.88',credentials(),[oid,other],1))=={oid:'3.2.1-build4',other:None}
