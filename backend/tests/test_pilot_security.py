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
    value=next(n.value.value for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='redis_script' for t in n.targets))
    compile(textwrap.dedent(value),'redis-volume-recovery','exec')
    # Never use arbitrary archive owners; runtime Redis data uses only its pinned identities.
    assert 'm.uid not in(0,999)' in value and 'm.gid not in(0,999)' in value


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
