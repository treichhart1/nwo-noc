"""NOC119 real signing recovery and optionally deployed ClamAV engine acceptance."""
import base64
import ast
import copy
import json
import io
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import tarfile
import threading
import time
import textwrap
from uuid import uuid4
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/host'))
import policy
import release_trust
from test_backend_security import bundle
from test_rf_postgres_integration import postgres_db


def test_startup_import_diagnostics_export_only_numeric_timings_and_modules():
    sys.path.insert(0,str(ROOT/'tests/installer'))
    from pilot_diagnostics import import_profile
    report=import_profile('import time: 123 | 456 | app.main\n'
                          'import time: 7 | 8 | secret=password\n'
                          'postgresql://private:secret@internal.invalid/db\n')
    assert report['last']==[{'self_us':123,'cumulative_us':456,'module':'app.main'}]
    assert 'password' not in json.dumps(report) and 'internal.invalid' not in json.dumps(report)


def test_health_probe_import_does_not_initialize_monitoring_database():
    result=subprocess.run([sys.executable,'-c',
        'import sys; import app.worker_health; '
        'assert "sqlalchemy" not in sys.modules; '
        'assert "app.services.worker_runtime" not in sys.modules; '
        'assert "app.core.config" not in sys.modules'],capture_output=True,timeout=30)
    assert result.returncode==0


@pytest.mark.parametrize('value,healthy',[(b'90',True),(b'39',False),(b'101',False),(None,False)])
def test_lightweight_worker_probe_preserves_worker_specific_freshness(tmp_path,monkeypatch,value,healthy):
    from app import worker_health
    identity=tmp_path/'worker-id';identity.write_text('synthetic-worker')
    monkeypatch.setattr(worker_health,'ID_FILE',str(identity))
    monkeypatch.setattr(worker_health.time,'time',lambda:100)
    keys=[]
    class Client:
        def get(self,key):keys.append(key);return value
    monkeypatch.setattr(worker_health.Redis,'from_url',lambda *a,**kw:Client())
    if healthy:worker_health.main()
    else:
        with pytest.raises(SystemExit):worker_health.main()
    assert keys==['noc:worker:synthetic-worker:heartbeat']


@pytest.mark.parametrize('condition',['fresh','missing','stale','symlink'])
def test_planning_health_cli_retains_freshness_without_gis_or_orm(tmp_path,condition):
    heartbeat=tmp_path/'worker.heartbeat'
    if condition=='symlink':heartbeat.symlink_to(tmp_path/'target')
    elif condition!='missing':
        heartbeat.write_text('')
        if condition=='stale':os.utime(heartbeat,(1,1))
    script='''import runpy,sys
sys.argv=['app.planning.worker','--health']
try:
    runpy.run_module('app.planning.worker',run_name='__main__')
except SystemExit as e:
    status=0 if e.code is None else e.code
assert not any(n in sys.modules for n in ('sqlalchemy','rasterio','numpy','app.planning.service','app.core.config'))
raise SystemExit(status)
'''
    result=subprocess.run([sys.executable,'-c',script],env={**os.environ,'PLANNING_STORAGE_ROOT':str(tmp_path)},capture_output=True,timeout=30)
    assert (result.returncode==0)==(condition=='fresh')


def capsule(tmp_path):
    approved, _, _ = bundle(tmp_path)
    source = tmp_path / 'approval'; source.write_text(json.dumps(approved)); source.chmod(0o600)
    key=tmp_path/'key';public=tmp_path/'trusted.pem'
    subprocess.run(['openssl','genpkey','-algorithm','ED25519','-out',str(key)],check=True,capture_output=True)
    subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(public)],check=True,capture_output=True)
    signature=Path(str(source)+'.sig')
    subprocess.run(['openssl','pkeyutl','-sign','-inkey',str(key),'-rawin','-in',str(source),'-out',str(signature)],check=True,capture_output=True)
    for p in (key,public,signature):p.chmod(0o600)
    def reader(path, **kw):
        return policy.private_file(public if str(path)=='/etc/nwo-noc-release.pub' else path,owner=os.getuid(),**kw)
    output=tmp_path/'captured'
    index=release_trust.capture(approved,source,output,reader=reader)
    return approved,output,index,reader


def test_original_signed_evidence_survives_recovery(tmp_path):
    approved,output,index,reader=capsule(tmp_path)
    assert release_trust.verify(output,index['public_key_spki_sha256'],reader=reader)==approved
    assert {p.name for p in output.iterdir()} == {'approval.json','approval.json.sig','trusted-public.pem','trust-index.json','sbom.json','dependencies.json','containers.json','provenance.json'}
    assert all(p.stat().st_mode & 0o077 == 0 for p in output.iterdir())
    assert not (output/'key').exists()


@pytest.mark.parametrize('change',['wrong-key','missing-signature','changed-evidence','changed-index','changed-manifest'])
def test_recovered_release_requires_independent_intact_trust(tmp_path,change):
    _,output,index,reader=capsule(tmp_path)
    expected=index['public_key_spki_sha256']
    if change=='wrong-key':expected='0'*64
    elif change=='missing-signature':(output/'approval.json.sig').unlink()
    elif change=='changed-evidence':(output/'sbom.json').write_text('{}')
    elif change=='changed-index':(output/'trust-index.json').write_text('{}')
    else:(output/'approval.json').write_text('{}')
    with pytest.raises((ValueError,OSError)):
        release_trust.verify(output,expected,reader=reader)


@pytest.mark.parametrize('attack',['none','traversal','absolute','link','duplicate','missing','existing'])
def test_recovery_transport_cannot_write_arbitrary_paths(tmp_path,attack):
    sys.path.insert(0,str(ROOT/'tests/installer'))
    from pilot_recovery import unpack_transport
    names=['backup.age','backup-metadata.json','backup.key','approval.json','approval.json.sig','source.bundle','images.tar','synthetic-auth.json','trusted-public.pem','recovery.json','recovery.json.sig']
    if attack=='missing':names.pop()
    if attack=='duplicate':names.append(names[0])
    archive=tmp_path/'transport.tar'
    with tarfile.open(archive,'w') as t:
        for i,name in enumerate(names):
            if i==0 and attack=='traversal':name='../escaped'
            if i==0 and attack=='absolute':name='/escaped'
            item=tarfile.TarInfo(name);item.size=4
            if i==0 and attack=='link':item.type=tarfile.SYMTYPE;item.linkname='/etc/passwd';item.size=0
            t.addfile(item,io.BytesIO(b'test') if item.isfile() else None)
    dest=tmp_path/'received'
    if attack=='existing':dest.mkdir();(dest/'sentinel').write_text('preserve')
    if attack=='none':
        unpack_transport(archive,dest)
        assert len(list(dest.iterdir()))==11
        assert all(p.read_bytes()==b'test' and p.stat().st_mode&0o077==0 for p in dest.iterdir())
    else:
        with pytest.raises((ValueError,FileExistsError)):unpack_transport(archive,dest)
        if attack=='existing':assert (dest/'sentinel').read_text()=='preserve'
        else:assert not dest.exists()
    assert not (tmp_path/'escaped').exists()


def test_benchmark_refuses_existing_storage_without_removing_it(tmp_path,monkeypatch):
    sys.path.insert(0,str(ROOT/'scripts/release'))
    import pilot_benchmark
    calls=[]
    def command(args,**kw):
        calls.append(args)
        return SimpleNamespace(returncode=0,stdout=('sha256:'+'a'*64+'\n') if args[:3]==['docker','image','inspect'] else '')
    monkeypatch.setattr(pilot_benchmark,'command',command)
    with pytest.raises(RuntimeError,match='Existing volume retained'):
        pilot_benchmark.measure('synthetic-backend','synthetic-postgres',tmp_path/'report.json')
    assert not any(args[:3] in (['docker','volume','rm'],['docker','rm','-f']) for args in calls)
    assert json.loads((tmp_path/'report.json').read_text())['target_status']=='NOT TESTED'


@pytest.mark.parametrize('failure',[False,True])
def test_independent_restore_guard_preserves_failed_state(tmp_path,failure):
    sys.path.insert(0,str(ROOT/'tests/installer'))
    from pilot_recovery import restoration_guard
    calls=[]
    host=SimpleNamespace(guard=tmp_path/'recovery-required.json',opt=tmp_path,
                         state=lambda:{'commit':'synthetic'},compose=lambda *a,**kw:calls.append((a,kw)))
    retained=tmp_path/'historical-data';retained.write_text('retain')
    def restore():
        with restoration_guard(host):
            assert json.loads(host.guard.read_text())['phase']=='independent-restoration-in-progress'
            if failure:raise ValueError('synthetic interrupted restore')
            host.guard.unlink()
    if failure:
        with pytest.raises(ValueError,match='interrupted restore'):restore()
        assert json.loads(host.guard.read_text())['phase']=='independent-restoration-failed'
        assert calls[0][0][2:]==('stop','frontend','planning-worker','worker','backend')
    else:
        restore();assert not host.guard.exists() and not calls
    assert retained.read_text()=='retain'


def test_offline_bundle_preserves_nonancestor_reviewed_audit_history(tmp_path):
    source=tmp_path/'source';source.mkdir()
    def git(*args,cwd=source):
        return subprocess.run(['git',*args],cwd=cwd,check=True,capture_output=True,text=True).stdout.strip()
    git('init','-b','main');git('config','user.name','Synthetic');git('config','user.email','test@example.invalid')
    (source/'record').write_text('base');git('add','.');git('commit','-m','base')
    git('checkout','-b','side');(source/'record').write_text('reviewed side history');git('commit','-am','side')
    side=git('rev-parse','HEAD');git('update-ref','refs/remotes/origin/reviewed-side',side)
    git('checkout','main');git('branch','-D','side')
    bundle_path=tmp_path/'source.bundle';git('bundle','create',str(bundle_path),'--all')
    target=tmp_path/'offline';git('clone','--quiet',str(bundle_path),str(target))
    git('fetch','--quiet',str(bundle_path),'+refs/*:refs/noc119-audit/*',cwd=target)
    assert git('show',side+':record',cwd=target)=='reviewed side history'
    assert subprocess.run(['git','merge-base','--is-ancestor',side,'HEAD'],cwd=target).returncode==1


def test_recovery_redis_payload_is_executable_and_preserves_volume_ownership(tmp_path):
    tree=ast.parse((ROOT/'tests/installer/pilot_recovery.py').read_text())
    sys.path.insert(0,str(ROOT/'tests/installer'))
    from pilot_recovery import REDIS_UID,REDIS_GID
    assert (REDIS_UID,REDIS_GID)==(999,1000)
    assignments=[n for n in ast.walk(tree) if isinstance(n,ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id=='redis_script' for t in n.targets)]
    scope={'REDIS_UID':REDIS_UID,'REDIS_GID':REDIS_GID,'textwrap':textwrap}
    # Evaluate only the source string/prefix assignments, never the restore program.
    exec(compile(ast.Module(body=sorted(assignments,key=lambda n:n.lineno),type_ignores=[]),
                 'redis-program-construction','exec'),scope)
    value=scope['redis_script']
    compile(value,'redis-volume-recovery','exec')
    assert value.startswith('REDIS_UID=999\nREDIS_GID=1000\n')
    # Root and the exact signed-image identity only; no arbitrary saved owners.
    assert 'm.uid not in(0,REDIS_UID)' in value and 'm.gid not in(0,REDIS_GID)' in value


@pytest.mark.parametrize('field,value',[('User','0'),('revision','e'*40)])
def test_installer_image_identity_rejection_identifies_role_without_weakening_gate(field,value):
    import manager
    config={'User':'10001:10001','Labels':{'org.opencontainers.image.revision':'a'*40,'private_token':'never-log-this'}}
    if field=='revision':config['Labels']['org.opencontainers.image.revision']=value
    else:config[field]=value
    host=SimpleNamespace(image=lambda ref:{'Config':config})
    with pytest.raises(policy.Reject,match='Approved backend image') as error:
        manager.Host.images(host,{'commit':'a'*40,'images':{'backend':'synthetic','frontend':'synthetic'}})
    assert 'never-log-this' not in str(error.value)
    assert 'expected_revision=' in str(error.value) and 'observed_uid=' in str(error.value)


@pytest.mark.parametrize('role,value,accepted',[
    ('postgres','omitted',True),('postgres',None,False),
    ('backend','omitted',False),('frontend','omitted',False),
])
def test_docker29_omitted_default_user_preserves_application_identity_guards(monkeypatch,role,value,accepted):
    import manager
    revision='a'*40
    configs={k:{'Config':{'User':u,'Labels':{'org.opencontainers.image.revision':revision}}} for k,u in [('backend','10001:10001'),('frontend','101:101'),('postgres','')]}
    if value=='omitted':configs[role]['Config'].pop('User')
    else:configs[role]['Config']['User']=value
    locks=json.loads((ROOT/'deploy/image-lock.json').read_text())
    monkeypatch.setattr(manager,'git',lambda *args:json.dumps(locks))
    host=SimpleNamespace(image=lambda ref:configs.get(ref,{}))
    approved={'commit':revision,'images':{k:k for k in configs}}
    if accepted:
        manager.Host.images(host,approved)
        assert approved['images']['redis']==locks['images']['redis:7-alpine']
    else:
        with pytest.raises(policy.Reject,match='Approved '+role+' image'):manager.Host.images(host,approved)


def test_disposable_diagnostics_never_export_raw_credentials():
    sys.path.insert(0,str(ROOT/'tests/installer'))
    from pilot_diagnostics import signals
    secret='synthetic-do-not-export-credential'
    result=signals('OperationalError: postgresql://owner:'+secret+'@private.invalid/database\nPermission denied\nApplication startup complete')
    assert secret not in json.dumps(result) and 'private.invalid' not in json.dumps(result)
    assert result['flags']['permission_denied'] and result['flags']['startup_complete']
    assert result['exception_types']==['OperationalError']


def test_readiness_regression_uses_both_actual_deployment_contracts():
    sys.path.insert(0,str(ROOT/'tests/installer'))
    from postgres_readiness import profiles
    commands=profiles()
    assert set(commands)=={'host','isolated'}
    # The CI regression executes these exact extracted commands against real
    # temporary/final PostgreSQL servers rather than a separately copied probe.
    assert all(command.startswith('pg_isready ') for command in commands.values())


def real_redis_evidence(tmp_path):
    approved,docs,save=bundle(tmp_path)
    approved['images']['redis']=json.loads((ROOT/'deploy/image-lock.json').read_text())['images']['redis:7-alpine']
    # Exact RepoDigest shape observed in NOC119 run639's retained Trivy metadata.
    docs['containers']['payload']['redis']['Metadata']['RepoDigests']=['redis@sha256:7f7d30b7fdd09467bac58198a1f166dd69789d348d4fe859cccb20e0cf287fbe']
    save()
    return approved,docs,save


def test_real_docker_redis_digest_shape_matches_signed_tagged_reference(tmp_path):
    from evidence import verify_release_evidence
    approved,_,_=real_redis_evidence(tmp_path)
    reader=lambda path,**kw:policy.private_file(path,owner=os.getuid(),**kw)
    verify_release_evidence(approved,tmp_path/'approval',reader)


@pytest.mark.parametrize('reference',[
    'other/redis@sha256:7f7d30b7fdd09467bac58198a1f166dd69789d348d4fe859cccb20e0cf287fbe',
    'redis@sha256:'+'0'*64,
    'redis:7-alpine',
])
def test_digest_normalization_never_accepts_wrong_repository_hash_or_mutable_tag(tmp_path,reference):
    from evidence import verify_release_evidence
    approved,docs,save=real_redis_evidence(tmp_path)
    docs['containers']['payload']['redis']['Metadata']['RepoDigests']=[reference];save()
    reader=lambda path,**kw:policy.private_file(path,owner=os.getuid(),**kw)
    with pytest.raises(ValueError):verify_release_evidence(approved,tmp_path/'approval',reader)


@pytest.mark.parametrize('left,right',[
    ('registry.example.invalid:5000/team/radio:rc','registry.example.invalid:5000/team/radio'),
    ('index.docker.io/redis:7-alpine','docker.io/library/redis'),
])
def test_registry_port_and_docker_hub_namespace_are_preserved(left,right):
    from evidence import registry_identity
    assert registry_identity(left+'@sha256:'+'a'*64)==right+'@sha256:'+'a'*64


EICAR = b'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'


@pytest.fixture
def real_scanner(monkeypatch):
    if os.environ.get('NOC119_REAL_CLAMAV')!='1':pytest.skip('Real ClamAV deployment not requested')
    executable=shutil.which('clamd')
    assert executable, 'Requested real scanner was not installed'
    from app.core.config import settings
    with tempfile.TemporaryDirectory(prefix='noc119-av-') as temp:
        root=Path(temp);(root/'db').mkdir()
        (root/'db/test.ndb').write_text('NOC119.Safe.Eicar:0:*:'+EICAR.hex()+'\n')
        config=root/'clamd.conf'
        config.write_text(f'Foreground yes\nLocalSocket {root}/socket\nLocalSocketMode 600\nFixStaleSocket yes\nDatabaseDirectory {root}/db\nPidFile {root}/pid\nLogFile {root}/log\nMaxFileSize 10M\nStreamMaxLength 10M\nMaxScanSize 12M\n')
        processes=[]
        def start():
            p=subprocess.Popen([executable,'--config-file',str(config)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);processes.append(p)
            for _ in range(100):
                if p.poll() is not None:raise AssertionError('Actual ClamAV engine exited')
                try:
                    with socket.socket(socket.AF_UNIX) as s:
                        s.settimeout(.2);s.connect(str(root/'socket'));s.sendall(b'zPING\0')
                        if s.recv(20)==b'PONG\0':return p
                except OSError:time.sleep(.1)
            raise AssertionError('Actual scanner unavailable')
        monkeypatch.setattr(settings,'maintenance_document_scanner','clamav')
        monkeypatch.setattr(settings,'maintenance_clamav_socket',str(root/'socket'))
        first=start()
        try:yield first,start,root
        finally:
            for p in processes:
                if p.poll() is None:p.terminate();p.wait(timeout=10)


def test_real_engine_safe_signature_unavailability_and_restart(real_scanner):
    from app.maintenance_ops import storage
    first,start,_=real_scanner
    assert storage.scan(b'harmless synthetic report')==('clean','clamav')
    assert storage.scan(EICAR)==('infected','clamav')
    first.terminate();first.wait(timeout=10)
    assert storage.scan(b'harmless synthetic report')==('error','clamav')
    start()
    assert storage.scan(b'harmless synthetic report')==('clean','clamav')


def test_real_socket_timeout_never_becomes_clean(real_scanner,monkeypatch):
    from app.core.config import settings
    from app.maintenance_ops import storage
    _,_,root=real_scanner
    listener=socket.socket(socket.AF_UNIX);listener.bind(str(root/'stalled'));listener.listen(1)
    def stall():
        client,_=listener.accept()
        with client:time.sleep(4)
    worker=threading.Thread(target=stall);worker.start()
    monkeypatch.setattr(settings,'maintenance_clamav_socket',str(root/'stalled'))
    before=time.monotonic()
    try:assert storage.scan(b'synthetic')==('error','clamav')
    finally:worker.join(timeout=6);listener.close()
    assert 2.5 < time.monotonic()-before < 7


def test_real_upload_quarantine_approval_audit_and_recovery(real_scanner,postgres_db,tmp_path,monkeypatch):
    from sqlalchemy import select
    from app.core.config import settings
    from app.models.entities import Site,User
    from app.maintenance_ops import service as s
    from app.maintenance_ops.models import Category,Document,Version,DocumentEvent
    from app.maintenance_ops.contracts import VersionInput,DocumentControl
    monkeypatch.setattr(settings,'maintenance_document_root',str(tmp_path/'documents'))
    db=postgres_db;u=User(username='noc119-scanner',email='synthetic@example.invalid',password_hash='unused',role='admin')
    site=Site(name='Synthetic scanner POP');category=Category(code='noc119-scan',name='Synthetic')
    db.add_all([u,site,category]);db.flush()
    d=Document(site_id=site.id,category_id=category.id,title='Synthetic security report',created_by=u.id)
    db.add(d);db.flush()
    reason='Synthetic actual scanner acceptance evidence'
    def upload(raw):
        result=s.upload_version(db,u,d,VersionInput(operation_key=uuid4(),reason=reason,expected_revision=d.revision,filename='report.txt',media_type='text/plain',contents_base64=base64.b64encode(raw).decode(),change_description=reason))
        return db.get(Version,result['id'])
    def control(action,v):return s.control_document(db,u,d,DocumentControl(operation_key=uuid4(),reason=reason,expected_revision=d.revision,action=action,version_id=v.id))
    infected=upload(EICAR);assert infected.scan_state=='infected'
    with pytest.raises(ValueError,match='Clean malware'):control('approve',infected)
    first,start,_=real_scanner;first.terminate();first.wait(timeout=10)
    unavailable=upload(b'harmless synthetic report');assert unavailable.scan_state=='error'
    with pytest.raises(ValueError,match='Clean malware'):control('approve',unavailable)
    start();control('rescan',unavailable);control('approve',unavailable)
    assert unavailable.scan_state=='clean' and d.approved_revision==unavailable.number
    events=list(db.scalars(select(DocumentEvent.action).where(DocumentEvent.document_id==d.id)))
    assert events.count('version_uploaded')==2 and 'rescan' in events and 'approve' in events


@pytest.mark.parametrize('field,value',[('REDIS_URL','redis://synthetic.invalid:6379/4'),('redis_url','redis://synthetic.invalid:6379/5'),(None,None)])
def test_probe_values_preserve_case_insensitive_environment_precedence(tmp_path,monkeypatch,field,value):
    from app.core.probe_config import probe_value,DEFAULT_REDIS_URL
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.lower()=='redis_url':monkeypatch.delenv(key)
    if field:monkeypatch.setenv(field,value)
    assert probe_value('REDIS_URL',DEFAULT_REDIS_URL)==(value if field else DEFAULT_REDIS_URL)


def test_probe_dotenv_fallback_uses_existing_validated_settings(tmp_path,monkeypatch):
    from app.core.probe_config import probe_value,DEFAULT_REDIS_URL
    from app.core.config import settings
    monkeypatch.chdir(tmp_path)
    (tmp_path/'.env').write_text('# synthetic standalone configuration')
    for key in list(os.environ):
        if key.lower()=='redis_url':monkeypatch.delenv(key)
    monkeypatch.setattr(settings,'redis_url','redis://synthetic.invalid:6379/6')
    assert probe_value('REDIS_URL',DEFAULT_REDIS_URL)==settings.redis_url


def test_probe_defaults_share_the_application_settings_authority():
    from app.core.probe_config import DEFAULT_REDIS_URL,DEFAULT_PLANNING_STORAGE_ROOT
    from app.core.config import Settings
    assert Settings.model_fields['redis_url'].default==DEFAULT_REDIS_URL
    assert Settings.model_fields['planning_storage_root'].default==DEFAULT_PLANNING_STORAGE_ROOT


@pytest.mark.parametrize('cpuinfo,accepted',[
    ('flags : pni ssse3 sse4_1 sse4_2 popcnt cx16 lahf_lm',True),
    ('flags : pni cx16 lahf_lm',False),
    ('model name : synthetic',False),
    ('flags : pni ssse3 sse4_1 sse4_2 popcnt cx16 lahf_lm\nflags : pni cx16 lahf_lm',False),
])
def test_installer_rejects_unsupported_native_abi_before_state_changes(cpuinfo,accepted):
    from policy import require_cpu_features,Reject
    if accepted:require_cpu_features(cpuinfo)
    else:
        with pytest.raises(Reject,match='x86-64-v2'):require_cpu_features(cpuinfo)


def test_ingress_unit_diagnostics_allowlist_never_exports_credentials():
    sys.path.insert(0,str(ROOT/'tests/installer'))
    from pilot_diagnostics import unit_properties
    result=unit_properties('ActiveState=failed\nResult=exit-code\nExecMainStatus=1\n'
                           'Environment=PASSWORD=synthetic-secret\nExecStart=/private/path\n'
                           'SubState=synthetic-secret\n')
    assert result=={'ActiveState':'failed','Result':'exit-code','ExecMainStatus':1,'SubState':'unknown'}
    assert 'synthetic-secret' not in json.dumps(result) and '/private' not in json.dumps(result)


@pytest.mark.parametrize('mask',[0o022,0o077])
def test_approved_source_directories_are_traversable_under_secure_umask(tmp_path,monkeypatch,mask):
    import manager
    archive=io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w') as tar:
        for name in ('scripts','scripts/host'):
            member=tarfile.TarInfo(name);member.type=tarfile.DIRTYPE;member.mode=0o775
            tar.addfile(member)
        contents=b'# synthetic ingress source\n'
        member=tarfile.TarInfo('scripts/host/ingress.py')
        member.mode=0o664;member.size=len(contents)
        tar.addfile(member,io.BytesIO(contents))
    monkeypatch.setattr(manager,'run',lambda *a,**kw:SimpleNamespace(stdout=archive.getvalue()))
    host=object.__new__(manager.Host);host.opt=tmp_path/'opt'
    (host.opt/'releases').mkdir(parents=True)
    previous=os.umask(mask)
    try:
        release=host.stage({'commit':'a'*40,'purpose':'controlled-test'})
        assert os.umask(mask)==mask
    finally:os.umask(previous)
    for path in (release,release/'scripts',release/'scripts/host'):
        assert path.stat().st_mode & 0o777==0o755
        assert path.stat().st_mode & 0o022==0
    source=release/'scripts/host/ingress.py'
    assert source.read_bytes()==contents and source.stat().st_mode & 0o004
    assert source.stat().st_mode & 0o022==0
    assert (release/'release.json').stat().st_mode & 0o777==0o600


def test_recovered_release_preserves_code_traversal_and_private_metadata(tmp_path):
    sys.path.insert(0,str(ROOT/'tests/installer'))
    from pilot_recovery import restore_release_modes
    release=tmp_path/'release';(release/'scripts/host').mkdir(parents=True)
    (release/'scripts/host/ingress.py').write_text('# synthetic code')
    (release/'release.json').write_text('{}')
    members=[]
    for name,mode,directory in [('release',0o755,True),('release/scripts',0o755,True),
        ('release/scripts/host',0o755,True),('release/scripts/host/ingress.py',0o644,False),
        ('release/release.json',0o600,False)]:
        item=tarfile.TarInfo(name);item.mode=mode;item.uid=item.gid=0
        item.type=tarfile.DIRTYPE if directory else tarfile.REGTYPE;members.append(item)
    for path in (release,release/'scripts',release/'scripts/host'):path.chmod(0o700)
    restore_release_modes(members,release)
    assert all(p.stat().st_mode&0o777==0o755 for p in (release,release/'scripts',release/'scripts/host'))
    assert (release/'release.json').stat().st_mode&0o777==0o600
    assert (release/'scripts/host/ingress.py').read_text()=='# synthetic code'
    unsafe=copy.copy(members[0]);unsafe.mode=0o777
    with pytest.raises(ValueError,match='Unsafe recovered'):restore_release_modes([unsafe],release)
    unsafe.mode=0o755;unsafe.uid=10001
    with pytest.raises(ValueError,match='Unsafe recovered'):restore_release_modes([unsafe],release)


def test_guest_command_failure_exports_only_safe_identity():
    sys.path.insert(0,str(ROOT/'tests/installer'))
    from pilot_diagnostics import command_failure
    import hashlib
    out=json.dumps({'check':'synthetic-check','status':'PASSED'})+'\n'
    result=command_failure(out,'urllib.error.HTTPError: HTTP Error 401: private-secret\n/private/key/path',1)
    assert result['exception_types']==['HTTPError'] and result['http_statuses']==[401]
    assert result['exit_code']==1 and result['completed_check_count']==1
    assert result['last_check_sha256']==hashlib.sha256(b'synthetic-check').hexdigest()
    assert 'private-secret' not in json.dumps(result) and '/private/' not in json.dumps(result)


def test_recovery_login_matches_existing_oauth2_form_contract():
    sys.path.insert(0,str(ROOT/'tests/installer'))
    from pilot_recovery import authentication_request
    from urllib.parse import parse_qs
    values={'username':'synthetic technician','password':'synthetic+&=password'}
    request=authentication_request(values)
    assert request.full_url=='https://localhost:18090/api/auth/login'
    assert request.get_header('Content-type')=='application/x-www-form-urlencoded'
    assert parse_qs(request.data.decode())=={k:[v] for k,v in values.items()}


def test_authentication_cost_diagnostics_never_export_secrets_or_hashes():
    sys.path.insert(0,str(ROOT/'tests/installer'))
    from pilot_diagnostics import safe_authentication_cost
    value={'scheme':'bcrypt','policy_rounds':12,'hash_rounds':12,'hash_seconds':2.0,
           'verify_seconds':1.75,'wrong_seconds':1.8,'correct_password_accepted':True,
           'incorrect_password_rejected':True,'password':'synthetic-secret','hash':'private-hash'}
    result=safe_authentication_cost(value)
    assert result=={k:v for k,v in value.items() if k not in ('password','hash')}
    assert 'synthetic-secret' not in json.dumps(result) and 'private-hash' not in json.dumps(result)


@pytest.mark.parametrize('variant',['valid','wrong_uid','wrong_gid','symlink','outside_path'])
def test_redis_restore_requires_exact_pinned_alpine_ownership(variant):
    sys.path.insert(0,str(ROOT/'tests/installer'))
    from pilot_recovery import redis_restore_members,REDIS_UID,REDIS_GID
    assert (REDIS_UID,REDIS_GID)==(999,1000)
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as tar:
        root=tarfile.TarInfo('redis');root.type=tarfile.DIRTYPE;root.uid=root.gid=0;tar.addfile(root)
        item=tarfile.TarInfo('redis/appendonly.aof');item.uid=999;item.gid=1000
        if variant=='wrong_uid':item.uid=10001
        if variant=='wrong_gid':item.gid=999
        if variant=='symlink':item.type=tarfile.SYMTYPE;item.linkname='/private'
        if variant=='outside_path':item.name='other/data'
        tar.addfile(item)
    stream.seek(0)
    with tarfile.open(fileobj=stream) as tar:
        if variant=='valid':assert len(redis_restore_members(tar))==2
        else:
            with pytest.raises(ValueError):redis_restore_members(tar)
