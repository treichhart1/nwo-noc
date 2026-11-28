from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.integrations.mikrotik_advanced import (ADVANCED_READS,MONITORS,AdvancedSnapshot,bounded_rows,collect_optional,
    monitor_sentence,optical,poe,tunnel_rows,transition_evidence,lte_documented_fields)
from app.integrations.mikrotik_native import NativeClient,encode_sentence
from app.integrations.read_only import IntegrationFailure
from app.services.monitoring_collectors import RouterSnapshot,Envelope
from app.cellular.contracts import CellularEvidence,DatasetInput
from app.cellular.service import preview,OPEN_COLUMNS,distance_bearing
from test_mikrotik_native import config,credentials,Stream
from app.api.deps import current_user
from app.main import app

NOW=datetime(2026,11,28,12,tzinfo=timezone.utc)


def dataset(**changes):
    row='LTE,310,260,10,12345,42,-84.2,41.1,500,10,1,1700000000,1700000001,0\n'
    return DatasetInput(**(dict(source='opencellid',version='synthetic-v1',attribution='OpenCellID https://opencellid.org CC-BY-SA-4.0',
        license='CC-BY-SA-4.0',approval_reference='synthetic-license-review',observed_at=NOW,reviewed_license=True,
        csv_text=','.join(OPEN_COLUMNS)+'\n'+row)|changes))


def evidence(**changes):
    return CellularEvidence(**(dict(device_id=1,observed_at=NOW,evidence_reference='synthetic-reference',interface_id='*1',
        source_kind='synthetic',technology='LTE',mcc=310,mnc=260,cell='12345',area='10',cell_identity_technology='LTE')|changes))


@pytest.mark.parametrize('kind',MONITORS)
def test_fixed_once_monitor_contract(kind):
    cfg=config(ethernet_monitor_ids=('*1',),poe_monitor_ids=('*1',))
    client=NativeClient(cfg,credentials());client._socket=Stream(encode_sentence(['!done']));client._deadline=10**12
    assert client._exchange(monitor_sentence(kind,'*1'))==[]
    assert b'once' in client._socket.sent[0]


@pytest.mark.parametrize('sentence', [('/system/script/run',),('/interface/lte/monitor','=numbers=*1','=once='),
    ('/interface/wireguard/peers/print',),('/interface/ethernet/poe/set','=numbers=*1','=poe-out=off'),
    ('/ip/address/add','=address=192.0.2.1/24'),('/interface/ethernet/monitor','=numbers=*1'),
    ('/interface/wireguard/peers/print','=.proplist=private-key,preshared-key')])
def test_unapproved_or_sensitive_commands_rejected_before_io(sentence):
    client=NativeClient(config(),credentials());client._socket=Stream();client._deadline=10**12
    with pytest.raises(IntegrationFailure,match='operation_denied'):client._exchange(sentence)
    assert client._socket.sent==[]


@pytest.mark.parametrize('ident',['ether1','*1;reboot','*1,*2','*123456789','[find]',''])
def test_no_dynamic_interface_selection(ident):
    with pytest.raises(ValidationError):config(ethernet_monitor_ids=(ident,))


@pytest.mark.parametrize('group',['ipam','switch','router','tunnels'])
def test_fixed_native_reads_narrow_fields_and_tls_base(group):
    c=NativeClient(config(advanced_groups=(group,)),credentials());c._deadline=10**12
    name={'ipam':'addresses','switch':'vlans','router':'health','tunnels':'wireguard'}[group]
    c._socket=Stream(encode_sentence(['!re','=private-key=canary'])+encode_sentence(['!done']))
    assert c._read_advanced(name)==[{}]
    for _,props in ADVANCED_READS.values():assert not any(x in props for x in ('private-key','preshared-key','imsi','iccid','password'))


def test_version_boundaries_and_missing_hardware_no_healthy_fallback():
    class Fake:
        config=config(advanced_groups=('tunnels','router','ipam'))
        credentials=credentials()
        def _read_advanced(self,name):
            if name=='health':raise IntegrationFailure('operation_denied')
            return []
    result=collect_optional(Fake(),'6.49.18')
    assert result['unavailable']['wireguard']=='unsupported_routeros_version'
    assert result['unavailable']['health']=='unavailable_or_permission_denied'
    AdvancedSnapshot.model_validate(result)
    assert result['cellular_native'].startswith('BLOCKED')


def test_optics_poe_and_tunnel_units_not_guessed():
    assert optical([{'sfp-rx-power':'-5.22dBm','sfp-temperature':'33C','rate':'1Gbps'}])[0]['optical_rx_dbm']==-5.22
    assert optical([{'sfp-rx-power':'-5.22','rate':'1000000000'}])[0]['speed_mbps'] is None
    result=poe([{'poe-out-status':'powered-on','poe-out-voltage':'48.1V','poe-out-current':'123mA','poe-out-power':'5.9W'}])[0]
    assert result['current_ma']==123 and result['voltage_v']==48.1
    assert tunnel_rows([{'last-handshake':'6m','rx':'10','tx':'20'}])[0]['assessment']=='stale_handshake'
    assert tunnel_rows([{}])[0]['availability']=='unknown'


def test_receipt_validation_rejects_raw_keys_and_tampered_derived_values():
    with pytest.raises(ValueError):AdvancedSnapshot(routeros_major=7,resources={'wireguard':[{'private-key':'canary'}]})
    with pytest.raises(ValueError):AdvancedSnapshot(routeros_major=7,optical=[{'optical_rx_dbm':0}])
    a=AdvancedSnapshot(routeros_major=7,resources={'addresses':[{'address':'192.0.2.1/24','interface':'ether1'}]})
    env=Envelope(device_id=1,format='mikrotik',sampled_at=NOW,payload=RouterSnapshot(advanced=a,routeros_version='7.20').model_dump(mode='json'))
    assert env.payload['advanced']['resources']['addresses'][0]['address']=='192.0.2.1/24'


def test_partial_route_absence_and_boundaries_never_infer_outage():
    a={'resources':{'routes':[{'.id':'*1','gateway':'192.0.2.1','active':'true'}]}}
    b={'resources':{'routes':[{'.id':'*1','gateway':'192.0.2.2','active':'true'}]}}
    result=transition_evidence(a,b,elapsed_seconds=60,uptime_continuous=True)
    assert result['events'][0]['cause']=='unverified'
    assert transition_evidence(a,{},elapsed_seconds=60,uptime_continuous=True)['events']==[]
    assert transition_evidence(a,b,elapsed_seconds=60,uptime_continuous=False)['state']=='boundary'


def test_recorded_lte_does_not_infer_nr_or_identifiers():
    result=lte_documented_fields({'data-class':'LTE','current-cellid':'3103242','rsrp':'-97dBm','sinr':'6dB','status':'connected'})
    assert result['rsrp_dbm']==-97 and result['cell']=='3103242' and result['mcc'] is None
    assert lte_documented_fields({'data-class':'5G NSA','current-cellid':'3103242'})['cell'] is None
    with pytest.raises(ValueError):lte_documented_fields({'imsi':'canary'})


@pytest.mark.parametrize('changes',[dict(technology='LTE',cell_identity_technology='NR'),dict(technology='5G_SA'),dict(cell=str(2**28)),dict(rsrp_dbm=float('nan')),dict(imsi='canary')])
def test_cellular_closed_identity_contract(changes):
    with pytest.raises(ValidationError):evidence(**changes)


def test_dataset_preview_license_coords_region_and_dedup():
    b=dataset();r=preview(b,now=NOW)
    assert r['count']==1 and r['license']=='CC-BY-SA-4.0' and r['rows'][0]['kind']=='estimated_cell'
    lines=b.csv_text.splitlines();dup=dataset(csv_text='\n'.join([*lines,lines[1]])+'\n')
    assert preview(dup,now=NOW)['duplicates']==1
    out=dataset(csv_text=b.csv_text.replace('-84.2','-120.2'))
    assert preview(out,now=NOW)['outside_region']==1
    assert preview(dataset(csv_text=b.csv_text.replace('LTE,','NR,')),now=NOW)['unsupported_technology']==1


@pytest.mark.parametrize('changes',[dict(reviewed_license=False),dict(license='unknown'),dict(csv_text='bad\n'),
    dict(csv_text=dataset().csv_text.replace('-84.2','NaN')),dict(observed_at=NOW+timedelta(days=1)),
    dict(csv_text=dataset().csv_text.replace('12345','268435456'))])
def test_invalid_dataset_fails_closed(changes):
    with pytest.raises(ValueError):preview(dataset(**changes),now=NOW)


def test_fcc_structure_never_becomes_cellular_coverage():
    b=dataset(source='fcc_asr_reviewed',license='operator-reviewed-public-terms',csv_text='identity,latitude,longitude,observed_at\n12345,41.1,-84.2,2026-11-01T00:00:00Z\n')
    row=preview(b,now=NOW)['rows'][0]
    assert row['kind']=='registered_structure' and row['technology'] is None
    assert 'mcc' not in row


def test_distance_bearing_known_case():
    d,b=distance_bearing(41,-84,42,-84)
    assert 111000<d<112000 and b==0


def test_private_api_and_unauthorized_import():
    client=TestClient(app)
    assert client.get('/api/cellular/datasets').status_code==401
    app.dependency_overrides[current_user]=lambda:SimpleNamespace(id=1,role='operator')
    try:
        assert client.post('/api/cellular/datasets/preview',json=dataset().model_dump(mode='json')).status_code==403
        assert client.post('/api/cellular/evidence',json=evidence().model_dump(mode='json')).status_code==403
        assert client.post('/api/cellular/activate',json={}).status_code==404
        assert client.post('/api/cellular/evidence',content=b'x'*1700001).status_code==413
    finally:app.dependency_overrides.clear()


def test_disabled_advanced_synthetic_receipt_preserves_existing_workload(monkeypatch):
    from app.services.monitoring_collectors import synthetic
    from app.core.config import settings
    monkeypatch.setattr(settings,'offline_mode',True)
    monkeypatch.setattr(settings,'app_env','staging')
    db=SimpleNamespace(get=lambda model,ident:SimpleNamespace(id=1,source='synthetic',vendor='MikroTik'))
    job=SimpleNamespace(device_id=1,fixture={'mode':'normal','latency_ms':0})
    result=synthetic(db,job,NOW)
    assert set(result.payload)=={'cpu_percent','ram_percent','uptime_seconds','temperature_c','interfaces','ospf_states'}
    assert result.payload['interfaces'][0]['id']=='*1'
