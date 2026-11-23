"""NOC116 offline security regressions and real disposable PostgreSQL query contracts."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest
from sqlalchemy import event, select
from fastapi import HTTPException
from test_rf_postgres_integration import postgres_db
from app.core.config import settings
from app.maintenance_ops import storage
from app.models.entities import User, AuditLog, Device, TelemetrySample, MonitoringAlert
from app.core.security import verify_password
from app.reset_admin import read_password, reset
from app.api import deps
from app.services.monitoring_alerts import process
from app.services.monitoring_policy import ReliabilityPolicy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/host'))
from artifacts import verify_signature
import policy


@pytest.fixture
def signed(tmp_path):
    key, pub, payload, sig = [tmp_path / n for n in ('key', 'pub', 'payload', 'sig')]
    subprocess.run(['openssl','genpkey','-algorithm','ED25519','-out',str(key)], check=True, capture_output=True)
    subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(pub)], check=True, capture_output=True)
    import json
    from test_host_installer import manifest
    from test_backend_security import bundle
    approved, _, _ = bundle(tmp_path)
    payload.write_bytes(json.dumps(approved).encode())
    subprocess.run(['openssl','pkeyutl','-sign','-inkey',str(key),'-rawin','-in',str(payload),'-out',str(sig)], check=True, capture_output=True)
    return payload.read_bytes(), sig.read_bytes(), pub.read_bytes()


def test_real_offline_ed25519_verification(signed):
    verify_signature(*signed)


@pytest.mark.parametrize('part', [0,1,2])
def test_tampered_approval_signature_or_key_rejected(signed, part):
    values=list(signed)
    values[part]=values[part]+b'x'
    with pytest.raises(ValueError, match='verification failed'):
        verify_signature(*values)


def test_unsigned_release_does_not_reach_artifact_install(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, 'private_file', lambda p: b'{"format":1}')
    with pytest.raises(ValueError, match='Signed release approval'):
        policy.approval(tmp_path/'approval')


@pytest.mark.parametrize('chunks,state', [
    ([b'stre',b'am: ',b'OK\0'],'clean'),
    ([b'stream: OK',b''],'error'),
    ([b'untrusted: OK\0'],'error'),
    ([b'stream: OK\0stream: Eicar FOUND\0'],'error'),
    ([b'x'*1024],'error'),
    ([b'stream: Eicar FOUND\0'],'infected'),
    ([TimeoutError()],'error'),
])
def test_scanner_fragmentation_and_fail_closed(monkeypatch, chunks, state):
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def settimeout(self,value):pass
        def connect(self,path):pass
        def sendall(self,value):pass
        def recv(self,size):
            value=chunks.pop(0) if chunks else b''
            if isinstance(value,Exception):raise value
            return value
    monkeypatch.setattr(settings,'maintenance_document_scanner','clamav')
    monkeypatch.setattr(settings,'maintenance_clamav_socket','/synthetic/scanner.sock')
    monkeypatch.setattr(storage.socket,'socket',lambda *a:Socket())
    assert storage.scan(b'synthetic harmless bytes') == (state,'clamav')


@pytest.mark.parametrize('value',[b'x'*15,b'x'*73,b'x'*20+b'\nextra',b'x'*16+b'\0'])
def test_recovery_input_rejects_invalid_secret(monkeypatch,value):
    monkeypatch.setattr(sys,'stdin',SimpleNamespace(buffer=BytesIO(value)))
    with pytest.raises(ValueError):read_password(stdin=True)


def test_recovery_stdin_no_argv(monkeypatch):
    monkeypatch.setattr(sys,'stdin',SimpleNamespace(buffer=BytesIO(b'synthetic-recovery-password\n')))
    assert read_password(stdin=True)=='synthetic-recovery-password'


def test_recovery_audit_and_prior_session_revocation(postgres_db,monkeypatch):
    db=postgres_db
    u=User(username='noc116-recovery',email='recovery@example.invalid',password_hash='unusable',role='admin')
    db.add(u);db.flush()
    reset(db,u.username,'synthetic-recovery-password','Approved synthetic recovery test')
    entry=db.scalar(select(AuditLog).where(AuditLog.action=='auth.password_reset',AuditLog.entity_id==str(u.id)))
    assert entry and 'password' not in str(entry.details).lower()
    assert verify_password('synthetic-recovery-password',u.password_hash)
    claims={'sub':u.username,'uid':u.id,'jti':'noc116-old','iat':int(entry.created_at.timestamp())-5}
    monkeypatch.setattr(deps,'decode_access_claims',lambda t:claims)
    request=SimpleNamespace(url=SimpleNamespace(path='/api/auth/me'))
    with pytest.raises(HTTPException) as exc:deps.current_user(request,'synthetic',db)
    assert exc.value.status_code==401
    claims['iat']=int(entry.created_at.timestamp())+1
    assert deps.current_user(request,'synthetic',db).id==u.id


def test_alert_processing_constant_select_budget_preserves_evidence(postgres_db):
    db=postgres_db;now=datetime.now(timezone.utc)
    d=Device(source='synthetic',external_id='noc116-query-budget',name='Synthetic router')
    db.add(d);db.flush()
    for n in range(3):
        db.add(TelemetrySample(device_id=d.id,sampled_at=now-timedelta(seconds=30*(2-n)),raw_data={'mikrotik_v1':{'temperature_c':90,'uptime_seconds':100+n,'interfaces':[]}}))
    db.flush();queries=[]
    def observe(conn,cursor,statement,params,context,many):
        if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
    bind=db.get_bind();event.listen(bind,'before_cursor_execute',observe)
    try:process(db,d.id,now=now,policy=ReliabilityPolicy())
    finally:event.remove(bind,'before_cursor_execute',observe)
    assert len(queries)<=3,queries
    active=db.scalar(select(MonitoringAlert).where(MonitoringAlert.device_id==d.id,MonitoringAlert.rule=='overheating'))
    assert active.state=='active' and active.evidence['consecutive_enter']==3
    process(db,d.id,now=now+timedelta(seconds=1000),policy=ReliabilityPolicy())
    assert active.state=='unknown' and active.cleared_at is None


@pytest.mark.parametrize('status,code',[(401,'authentication_failed'),(403,'authentication_failed'),(429,'rate_limited'),(503,'unavailable')])
def test_adapter_permission_and_provider_failures_are_static(status,code):
    import httpx
    from app.integrations.read_only import ReadClient,ReadPolicy,MIKROTIK_PATHS,IntegrationFailure
    policy=ReadPolicy('https://router.example.invalid',('https://router.example.invalid',),MIKROTIK_PATHS,True,True,retries=0)
    transport=httpx.MockTransport(lambda r:httpx.Response(status,text='secret-token-must-not-leak'))
    with ReadClient(policy,transport=transport,sleep=lambda s:None) as client:
        with pytest.raises(IntegrationFailure) as exc:client.request('/rest/system/resource')
    assert exc.value.code==code and 'secret' not in str(exc.value)


@pytest.mark.parametrize('version',['6.49.17','8.0','unknown',None])
def test_live_rest_rejects_unvalidated_routeros_versions(version):
    from app.integrations.mikrotik import require_routeros7
    from app.integrations.read_only import IntegrationFailure
    with pytest.raises(IntegrationFailure):require_routeros7({'version':version})


def test_powercode_compatibility_fixture_preserves_ownership_and_status():
    import json
    from app.customers.contracts import ExportBatch
    p=Path(__file__).parent/'fixtures/noc116-powercode.json'
    batch=ExportBatch.model_validate(json.loads(p.read_text()))
    assert len(batch.accounts)==3 and len(batch.services)==5
    assert len({s.external_id for s in batch.services})==5
    assert {s.status for s in batch.services}=={'active','suspended','disconnected','unknown'}
    assert sum(s.account_id=='SYN116-C1' for s in batch.services)==2
    assert all(s.source_refs.get('tower') for s in batch.services)


def test_signed_release_policy_binds_exact_approved_manifest(signed,tmp_path,monkeypatch):
    payload,signature,key=signed
    def private(path, **kwargs):
        return key if str(path).endswith('.pub') else signature if str(path).endswith('.sig') else payload if Path(path).name == 'approval' else Path(path).read_bytes()
    monkeypatch.setattr(policy,'private_file',private)
    accepted=policy.approval(tmp_path/'approval')
    assert accepted['purpose']=='release' and accepted['commit']=='a'*40


def test_source_lock_sbom_reproducible_and_explicit_about_unknown_licenses():
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root/'scripts/release'))
    from sbom import build
    a,b=build(),build()
    assert a==b and a['spdxVersion']=='SPDX-2.3'
    assert len(a['packages'])>=100 and any(p['licenseDeclared']=='NOASSERTION' for p in a['packages'])
    assert 'Redis' in a['annotations'][0]['comment']


def test_legacy_password_argument_is_rejected_without_echo(monkeypatch,capsys):
    from app.reset_admin import main
    monkeypatch.setattr(sys,'argv',['recovery','--approve-local-recovery','--reason','Synthetic recovery test','--password','must-not-be-echoed'])
    with pytest.raises(SystemExit):main()
    assert 'must-not-be-echoed' not in capsys.readouterr().err


def test_image_scan_records_high_findings_deduplicates_and_can_gate(tmp_path,monkeypatch):
    import json
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/release'))
    import image_security
    immutable='sha256:'+'e'*64
    report={'Metadata':{'ImageID':immutable},'Results':[{'Vulnerabilities':[{'Severity':'HIGH'}],'Licenses':[{'Name':'SYNTHETIC-REVIEW'}]}]}
    monkeypatch.setattr(image_security.subprocess,'check_output',lambda argv:json.dumps([{'Id':immutable}]).encode())
    calls=[]
    def scan(argv,**kwargs):calls.append(argv);return SimpleNamespace(returncode=0,stdout=json.dumps(report).encode())
    monkeypatch.setattr(image_security.subprocess,'run',scan)
    monkeypatch.setattr(sys,'argv',['scan','--output',str(tmp_path/'output'),'--require-no-high','synthetic-image','same-image'])
    with pytest.raises(SystemExit,match='security gate failed'):image_security.main()
    rows=json.loads((tmp_path/'output/summary.json').read_text())
    assert len(calls)==len(rows)==1 and rows[0]['high']==1 and rows[0]['status']=='FAIL'
    assert rows[0]['license_review'].startswith('BLOCKED') and calls[0][-1]==immutable


@pytest.mark.parametrize('body',[{}, {'Metadata':{'ImageID':'wrong'},'Results':[{}]}, {'Metadata':{'ImageID':'sha256:'+'e'*64},'Results':[]}])
def test_image_scan_unknown_or_mismatched_results_never_clean(tmp_path,monkeypatch,body):
    import json
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/release'))
    import image_security
    monkeypatch.setattr(image_security.subprocess,'check_output',lambda argv:json.dumps([{'Id':'sha256:'+'e'*64}]).encode())
    monkeypatch.setattr(image_security.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps(body).encode(),stderr=b''))
    monkeypatch.setattr(sys,'argv',['scan','--output',str(tmp_path/'output'),'synthetic-image'])
    with pytest.raises(RuntimeError,match='unavailable'):image_security.main()


def test_recovery_without_terminal_requires_explicit_broker_input(monkeypatch):
    monkeypatch.setattr(sys,'stdin',SimpleNamespace(isatty=lambda:False))
    with pytest.raises(ValueError,match='Protected terminal'):read_password()


def test_alert_cache_retains_original_lookup_scope_for_legacy_categories(postgres_db):
    db=postgres_db;now=datetime.now(timezone.utc)
    d=Device(source='synthetic',external_id='noc116-legacy-alert',name='Synthetic legacy alert')
    db.add(d);db.flush()
    old=MonitoringAlert(device_id=d.id,series='network',rule='overheating',category='monitoring',severity='warning',state='active',first_seen_at=now,updated_at=now,evidence={})
    db.add(old)
    for n in range(3):db.add(TelemetrySample(device_id=d.id,sampled_at=now-timedelta(seconds=30*(2-n)),raw_data={'mikrotik_v1':{'temperature_c':90,'uptime_seconds':100+n,'interfaces':[]}}))
    db.flush()
    process(db,d.id,now=now,policy=ReliabilityPolicy())
    db.flush()
    rows=list(db.scalars(select(MonitoringAlert).where(MonitoringAlert.device_id==d.id)))
    assert len(rows)==1 and rows[0].id==old.id and old.state=='active'
