"""Synthetic API contracts + real disposable database; no network sockets."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import select,func
from app.core.config import settings
from app.models.entities import Device,Site,NetworkLink,Sector,Integration,IntegrationBinding,AuditLog,TelemetrySample,MonitorCheck
from app.integrations.uisp import sync_uisp
from app.integrations.mikrotik import collect_mikrotik
from app.integrations.read_only import IntegrationFailure
from test_rf_postgres_integration import postgres_db

NOW=datetime(2026,11,12,20,tzinfo=timezone.utc)
TOKEN='token-canary-do-not-store'
PASSWORD='password-canary-do-not-store'


@pytest.fixture
def configured(monkeypatch):
    values=dict(offline_mode=False,uisp_enabled=True,mikrotik_enabled=True,integrations_admin_approved=True,
        uisp_base_url='https://uisp.example.invalid',uisp_allowed_origins='https://uisp.example.invalid',uisp_token=TOKEN,
        uisp_verify_tls=True,rf_collection_enabled=False,rf_history_pruning_enabled=False,
        mikrotik_base_url='https://router.example.invalid',mikrotik_allowed_origins='https://router.example.invalid',
        mikrotik_username='read-only-fixture',mikrotik_password=PASSWORD)
    for key,value in values.items():monkeypatch.setattr(settings,key,value)
    return values


def inventory(now=NOW):
    return {'devices':[{'identification':{'id':'radio-1','name':'Synthetic radio','model':'model-x','firmwareVersion':'7.16',
        'vendor':'MikroTik','mac':'aa:bb:cc:00:00:01','authorized':True,'site':{'id':'tower-1','name':'Synthetic Tower'}},
        'overview':{'status':'online','lastSeen':now.isoformat()},'password':PASSWORD,'token':TOKEN}],
        'sites':[{'id':'tower-1','name':'Synthetic Tower'},{'id':'pop-1','name':'Synthetic POP','type':'pop'}],
        'links':[{'id':'link-1','name':'Synthetic backhaul','fromSiteId':'pop-1','toSiteId':'tower-1','capacityMbps':500,'toDeviceId':'radio-1'}],
        'sectors':[{'id':'sector-1','name':'Synthetic sector','siteId':'tower-1','deviceId':'radio-1','frequencyMhz':5800,'channelWidthMhz':40}]}


def uisp_transport(payload,failures=None):
    def respond(req):
        assert req.method=='GET'
        resource=req.url.path.rsplit('/',1)[-1]
        if failures and resource in failures:return httpx.Response(failures[resource],text='password='+PASSWORD)
        return httpx.Response(200,json=deepcopy(payload.get(resource,[])))
    return httpx.MockTransport(respond)


def router_transport(*,rx=1000,errors=0,uptime='1d2h3m4s',failure=None,optional=False):
    def respond(req):
        assert req.method=='GET'
        if failure:return httpx.Response(failure,text='token='+TOKEN+' password='+PASSWORD)
        paths={'/rest/system/identity':{'name':'Synthetic Router'},
            '/rest/system/resource':{'version':'7.17','cpu-load':'15','total-memory':'1000','free-memory':'400','uptime':uptime},
            '/rest/interface':[{'.id':'*1','name':'ether1','running':'true','disabled':'false','rx-byte':str(rx),'tx-byte':'2000','rx-error':str(errors),'tx-error':'0','password':PASSWORD}],
            '/rest/system/health':[{'name':'temperature','value':'45'}],
            '/rest/routing/ospf/neighbor':[{'router-id':'192.0.2.1','state':'Full'}]}
        if optional and req.url.path in {'/rest/system/health','/rest/routing/ospf/neighbor'}:return httpx.Response(404)
        return httpx.Response(200,json=paths[req.url.path])
    return httpx.MockTransport(respond)


def device(db,monkeypatch):
    d=Device(source='synthetic',external_id='router-1',name='Operator label',firmware_version='7.17',monitor_enabled=False)
    db.add(d);db.flush();monkeypatch.setattr(settings,'mikrotik_device_id',d.id)
    return d


def test_uisp_metadata_relationships_idempotency_provenance(postgres_db,configured):
    db=postgres_db;transport=uisp_transport(inventory())
    assert sync_uisp(db,transport=transport,now=NOW)['imported']==1
    sync_uisp(db,transport=uisp_transport(inventory(NOW+timedelta(seconds=1))),now=NOW+timedelta(seconds=1))
    for model,count in [(Device,1),(Site,2),(NetworkLink,1),(Sector,1),(IntegrationBinding,5)]:
        assert db.scalar(select(func.count()).select_from(model))==count
    d=db.scalar(select(Device));assert d.wisp_site_id is not None and d.mac_address=='AA:BB:CC:00:00:01'
    assert d.firmware_version=='7.16' and not d.monitor_enabled
    assert TOKEN not in json.dumps(d.raw_data) and PASSWORD not in json.dumps(d.raw_data)
    assert len(list(db.scalars(select(AuditLog).where(AuditLog.action=='integration.uisp.read_sync'))))==2
    state=db.scalar(select(Integration));assert state.last_success_at==NOW+timedelta(seconds=1)


def test_stale_conflicting_and_partial_records_preserve_current_data(postgres_db,configured):
    db=postgres_db;payload=inventory();sync_uisp(db,transport=uisp_transport(payload),now=NOW)
    d=db.scalar(select(Device));d.name='Operator renamed';db.flush()
    older=inventory(NOW-timedelta(minutes=30));older['devices'][0]['identification']['name']='Stale name'
    sync_uisp(db,transport=uisp_transport(older),now=NOW+timedelta(seconds=1))
    assert d.name=='Operator renamed' and d.last_seen==NOW
    payload=inventory(NOW+timedelta(seconds=2));payload['devices']+=[None,{},deepcopy(payload['devices'][0])]
    sync_uisp(db,transport=uisp_transport(payload,{'sectors':503}),now=NOW+timedelta(seconds=2))
    state=db.scalar(select(Integration));assert state.status=='degraded' and state.last_error=='partial_sync'
    assert state.last_skipped_count>=3 and d.name=='Operator renamed'
    assert db.scalar(select(func.count()).select_from(Device))==1


def test_existing_device_mac_correlation_does_not_overwrite_operator_inventory(postgres_db,configured):
    db=postgres_db;existing=Device(source='manual',external_id='manual-1',name='Operator name',mac_address='aa-bb-cc-00-00-01')
    db.add(existing);db.flush()
    sync_uisp(db,transport=uisp_transport(inventory()),now=NOW)
    assert db.scalar(select(func.count()).select_from(Device))==1
    row=db.scalar(select(IntegrationBinding).where(IntegrationBinding.kind=='device'))
    assert row.device_id==existing.id and existing.source=='manual' and existing.name=='Operator name'


def test_auth_failure_preserves_success_timestamp_and_redacts_errors(postgres_db,configured,caplog):
    db=postgres_db;sync_uisp(db,transport=uisp_transport(inventory()),now=NOW)
    with pytest.raises(IntegrationFailure,match='authentication_failed') as error:
        sync_uisp(db,transport=uisp_transport({}, {'devices':401}),now=NOW+timedelta(seconds=1))
    state=db.scalar(select(Integration));assert state.last_success_at==NOW and state.last_attempt_at==NOW+timedelta(seconds=1)
    assert state.last_error=='authentication_failed'
    assert TOKEN not in caplog.text+str(error.value) and PASSWORD not in caplog.text+str(error.value)


def test_duplicate_mac_ambiguity_fails_closed(postgres_db,configured):
    db=postgres_db
    db.add_all([Device(source='manual',external_id=str(i),name='Ambiguous '+str(i),mac_address='AA:BB:CC:00:00:01') for i in range(2)])
    db.flush();assert sync_uisp(db,transport=uisp_transport(inventory()),now=NOW)['imported']==0
    assert db.scalar(select(func.count()).select_from(Device))==2
    assert db.scalar(select(Integration)).status=='degraded'


def test_mikrotik_read_only_observation_deltas_reset_and_conflict(postgres_db,configured,monkeypatch):
    db=postgres_db;d=device(db,monkeypatch);d.firmware_version='7.16';db.flush()
    assert collect_mikrotik(db,transport=router_transport(),now=NOW)['imported']==1
    assert collect_mikrotik(db,transport=router_transport(rx=1100,errors=1,uptime='1d2h4m4s'),now=NOW+timedelta(minutes=1))['degraded'] is True
    sample=db.scalar(select(TelemetrySample).order_by(TelemetrySample.id.desc()))
    values=sample.raw_data['mikrotik_v1'];assert values['interfaces'][0]['rx_bytes_delta']==100
    assert values['interfaces'][0]['rx_errors_delta']==1 and values['firmware_conflict']
    assert values['ram_percent']==60 and values['temperature_c']==45
    assert PASSWORD not in json.dumps(values) and d.name=='Operator label' and not d.monitor_enabled
    assert collect_mikrotik(db,transport=router_transport(rx=5,uptime='1m'),now=NOW+timedelta(minutes=2))['degraded'] is False
    last=db.scalar(select(TelemetrySample).order_by(TelemetrySample.id.desc())).raw_data['mikrotik_v1']
    assert last['counter_reset'] and last['interfaces'][0]['rx_bytes_delta'] is None
    assert set(db.scalars(select(MonitorCheck.source)))=={'mikrotik-read-only'}


@pytest.mark.parametrize('status,code',[(401,'authentication_failed'),(503,'unavailable')])
def test_mikrotik_failure_does_not_add_false_telemetry(postgres_db,configured,monkeypatch,status,code):
    db=postgres_db;device(db,monkeypatch)
    with pytest.raises(IntegrationFailure,match=code):collect_mikrotik(db,transport=router_transport(failure=status),now=NOW)
    assert db.scalar(select(func.count()).select_from(TelemetrySample))==0
    state=db.scalar(select(Integration));assert state.last_success_at is None and state.last_error==code


def test_optional_routeros_sensors_are_partial_not_fabricated(postgres_db,configured,monkeypatch):
    db=postgres_db;device(db,monkeypatch)
    collect_mikrotik(db,transport=router_transport(optional=True),now=NOW)
    sample=db.scalar(select(TelemetrySample));assert sample.temperature_c is None
    state=db.scalar(select(Integration));assert state.status=='degraded' and state.last_skipped_count==2


def test_stale_router_observation_cannot_replace_current(postgres_db,configured,monkeypatch):
    db=postgres_db;device(db,monkeypatch)
    collect_mikrotik(db,transport=router_transport(),now=NOW)
    with pytest.raises(IntegrationFailure):collect_mikrotik(db,transport=router_transport(rx=0),now=NOW-timedelta(seconds=1))
    assert db.scalar(select(func.count()).select_from(TelemetrySample))==1


def test_integration_status_and_legacy_errors_never_expose_secrets(postgres_db):
    from app.services.integration_status import integration_status
    from app.schemas.common import IntegrationOut
    db=postgres_db;row=Integration(name='uisp',status='error',last_error='password='+PASSWORD)
    db.add(row);db.flush()
    result=integration_status(db,now=NOW)
    assert all(not x['enabled'] and x['read_only'] for x in result)
    assert PASSWORD not in json.dumps(result,default=str)
    assert PASSWORD not in IntegrationOut.model_validate(row).model_dump_json()


def test_unsupported_and_malformed_router_payloads(postgres_db,configured,monkeypatch):
    db=postgres_db;device(db,monkeypatch)
    with pytest.raises(IntegrationFailure,match='invalid_response'):
        collect_mikrotik(db,transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'password':PASSWORD})),now=NOW)
    assert db.scalar(select(func.count()).select_from(TelemetrySample))==0


def test_unvalidated_posted_payload_is_not_exposed_as_mikrotik_data(postgres_db,monkeypatch):
    from fastapi import HTTPException
    from app.api.routes import mikrotik_observation
    db=postgres_db;d=device(db,monkeypatch)
    db.add(TelemetrySample(device_id=d.id,raw_data={'mikrotik_v1':{'password':PASSWORD}}));db.flush()
    with pytest.raises(HTTPException) as error:mikrotik_observation(d.id,db,None)
    assert error.value.status_code==404


def test_remote_echoed_credentials_are_redacted_or_rejected(postgres_db,configured):
    db=postgres_db;payload=inventory()
    payload['devices'][0]['identification']['name']='Router '+TOKEN
    payload['devices'].append({'identification':{'id':TOKEN,'name':'Echo'}})
    payload['sites'].append({'id':TOKEN,'name':'Echo'})
    sync_uisp(db,transport=uisp_transport(payload),now=NOW)
    assert db.scalar(select(func.count()).select_from(Device))==1
    for row in db.scalars(select(IntegrationBinding)):
        assert TOKEN not in row.external_id+json.dumps(row.values)
    assert TOKEN not in db.scalar(select(Device)).name


def test_mikrotik_64_bit_counters_preserve_precision(postgres_db,configured,monkeypatch):
    db=postgres_db;device(db,monkeypatch)
    big=2**63+100
    collect_mikrotik(db,transport=router_transport(rx=big),now=NOW)
    collect_mikrotik(db,transport=router_transport(rx=big+1,uptime='1d2h4m4s'),now=NOW+timedelta(minutes=1))
    sample=db.scalar(select(TelemetrySample).order_by(TelemetrySample.id.desc())).raw_data['mikrotik_v1']
    assert sample['interfaces'][0]['rx_bytes']==big+1 and sample['interfaces'][0]['rx_bytes_delta']==1


def test_only_provenance_verified_observations_are_returned(postgres_db,configured,monkeypatch):
    from app.api.routes import mikrotik_observation
    from fastapi import HTTPException
    db=postgres_db;d=device(db,monkeypatch)
    collect_mikrotik(db,transport=router_transport(),now=NOW)
    assert mikrotik_observation(d.id,db,None)['read_only'] is True
    sample=db.scalar(select(TelemetrySample));value=dict(sample.raw_data['mikrotik_v1']);value['password']=PASSWORD
    sample.raw_data={'mikrotik_v1':value};db.flush()
    with pytest.raises(HTTPException):mikrotik_observation(d.id,db,None)
