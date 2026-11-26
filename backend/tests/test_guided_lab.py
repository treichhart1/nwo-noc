"""Fail-closed lab evidence, explicit authorization and no production-mode fallback."""
from datetime import datetime, timezone, timedelta
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('guided_lab',ROOT/'scripts/host/lab.py');lab=importlib.util.module_from_spec(spec);spec.loader.exec_module(lab)


def fixture(tmp_path,monkeypatch):
    monkeypatch.setattr(lab,'private_file',lambda p:Path(p).read_bytes())
    def put(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d))
    images={r:'sha256:'+str(i+1)*64 for i,r in enumerate(lab.ROLES)}
    put(tmp_path/'pip-audit.json',{'dependencies':[{'name':'synthetic','vulns':[]}]})
    put(tmp_path/'npm-audit.json',{'metadata':{'vulnerabilities':{k:0 for k in ('info','low','moderate','high','critical','total')}}})
    put(tmp_path/'images/summary.json',[])
    put(tmp_path/'source-locks.spdx.json',{'spdxVersion':'SPDX-2.3','packages':[{'name':'synthetic'}]})
    rows=[]
    for i,identity in enumerate(images.values()):
        scan=f'image-{i}.json';sbom=f'sbom-image-{i}.spdx.json'
        put(tmp_path/'images'/scan,{'Metadata':{'ImageID':identity},'Results':[{'Vulnerabilities':[]}]})
        put(tmp_path/'images'/sbom,{'spdxVersion':'SPDX-2.3','packages':[{'name':'synthetic'}]})
        rows.append({'image_id':identity,'scan':scan,'scan_sha256':lab.digest(tmp_path/'images'/scan),'sbom':sbom,'sbom_sha256':lab.digest(tmp_path/'images'/sbom)})
    put(tmp_path/'images/sbom-index.json',rows)
    data={'format':1,'purpose':'controlled-test','repository':lab.REPOSITORY,'commit':'a'*40,'tree':'b'*40,'images':images,'created_utc':datetime.now(timezone.utc).isoformat(),'source_locks':{p:lab.digest(ROOT/p) for p in lab.LOCKS},'files':{p.relative_to(tmp_path).as_posix():lab.digest(p) for p in tmp_path.rglob('*.json')}}
    put(tmp_path/'prepared.json',data)
    return data


def test_complete_bound_evidence(tmp_path,monkeypatch):
    data=fixture(tmp_path,monkeypatch)
    assert lab.validate_evidence(tmp_path,'a'*40,'b'*40,inspect=False)==data


@pytest.mark.parametrize('change',['purpose','source','tree','repository','image_missing','image_duplicate','image_mutable','evidence_missing','checksum','traversal','locks','stale','future','sbom_duplicate','sbom_path','missing_raw','high','critical','npm','pip','image_identity','empty_sbom'])
def test_evidence_rejections(tmp_path,monkeypatch,change):
    d=fixture(tmp_path,monkeypatch)
    if change=='purpose':d['purpose']='release'
    elif change=='source':d['commit']='c'*40
    elif change=='tree':d['tree']='c'*40
    elif change=='repository':d['repository']='other/repository'
    elif change=='image_missing':del d['images']['backend']
    elif change=='image_duplicate':d['images']['backend']=d['images']['redis']
    elif change=='image_mutable':d['images']['backend']='latest'
    elif change=='evidence_missing':del d['files']['pip-audit.json']
    elif change=='checksum':(tmp_path/'npm-audit.json').write_text('{}')
    elif change=='traversal':d['files']['../outside']='0'*64
    elif change=='locks':d['source_locks']={}
    elif change in ('stale','future'):d['created_utc']=(datetime.now(timezone.utc)+timedelta(days=2 if change=='future' else -2)).isoformat()
    else:
        path=tmp_path/'images/sbom-index.json';rows=json.loads(path.read_text())
        if change=='sbom_duplicate':rows[-1]=rows[0]
        elif change=='sbom_path':rows[0]['scan']='../../outside'
        elif change=='missing_raw':del d['files']['images/'+rows[0]['scan']]
        elif change in ('high','critical','image_identity'):
            p=tmp_path/'images'/rows[0]['scan'];r=json.loads(p.read_text())
            if change=='image_identity':r['Metadata']['ImageID']='sha256:'+'f'*64
            else:r['Results'][0]['Vulnerabilities']=[{'Severity':change.upper()}]
            p.write_text(json.dumps(r));rows[0]['scan_sha256']=lab.digest(p);d['files'][p.relative_to(tmp_path).as_posix()]=lab.digest(p)
        elif change=='empty_sbom':
            p=tmp_path/'images'/rows[0]['sbom'];p.write_text('{}');rows[0]['sbom_sha256']=lab.digest(p);d['files'][p.relative_to(tmp_path).as_posix()]=lab.digest(p)
        elif change in ('npm','pip'):
            p=tmp_path/('npm-audit.json' if change=='npm' else 'pip-audit.json');p.write_text('{}');d['files'][p.name]=lab.digest(p)
        path.write_text(json.dumps(rows));d['files']['images/sbom-index.json']=lab.digest(path)
    (tmp_path/'prepared.json').write_text(json.dumps(d))
    with pytest.raises((lab.Reject,ValueError,KeyError)):lab.validate_evidence(tmp_path,'a'*40,'b'*40,inspect=False)


def test_missing_physical_image(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(lab,'output',lambda argv:(_ for _ in ()).throw(lab.Reject('image unavailable')))
    with pytest.raises(lab.Reject):lab.validate_evidence(tmp_path,'a'*40,'b'*40)


def authorization():
    return {'purpose':'controlled-test','commit':'a'*40,'tree':'b'*40,'reference':'Synthetic isolated lab approval','confirmation':'INSTALL ISOLATED LAB','ssh_host':'lab.example.invalid','backup_recipient':'age1'+'q'*58,'backup_destination':'independent synthetic receiver',**{k:True for k in lab.CONTROLS}}


@pytest.mark.parametrize('change',['purpose','source','confirmation','control','ssh','private_key','no_reference'])
def test_authorization_rejected(tmp_path,monkeypatch,change):
    a=authorization()
    if change=='purpose':a['purpose']='release'
    if change=='source':a['commit']='f'*40
    if change=='confirmation':a['confirmation']='YES'
    if change=='control':a[lab.CONTROLS[0]]=False
    if change=='ssh':a['ssh_host']='x;id'
    if change=='private_key':a['backup_recipient']='AGE-SECRET-KEY-EXAMPLE'
    if change=='no_reference':a['reference']='REPLACE'
    monkeypatch.setattr(lab,'private_file',lambda _:json.dumps(a).encode())
    with pytest.raises(lab.Reject):lab.authorization(SimpleNamespace(authorization_file='synthetic',commit='a'*40,tree='b'*40,action='install'))


def test_plan_cannot_mutate_or_authorize(monkeypatch,capsys):
    monkeypatch.setattr(lab,'source_check',lambda *a:None);monkeypatch.setattr(lab,'existing_install',lambda:None)
    monkeypatch.setattr(lab.preflight,'collect',lambda:{'failed':1,'checks':[]})
    monkeypatch.setattr(lab,'authorization',lambda _:pytest.fail('No authorization during plan'))
    monkeypatch.setattr(lab,'prepare',lambda *a:pytest.fail('No preparation during plan'))
    assert lab.main(['prepare','--commit','a'*40,'--tree','b'*40])==0
    assert json.loads(capsys.readouterr().out)['changes_authorized'] is False


def test_hardware_failure_cannot_enter_installer(monkeypatch):
    monkeypatch.setattr(lab,'existing_install',lambda:None);monkeypatch.setattr(lab,'dependencies',lambda:None)
    monkeypatch.setattr(lab.preflight,'collect',lambda:{'failed':1,'checks':[]})
    monkeypatch.setattr(lab,'validate_evidence',lambda *a:pytest.fail('Never reach install/evidence mutation'))
    with pytest.raises(lab.Reject):lab.install(SimpleNamespace(work='/no-write'),{})


def test_remote_docker_context_cannot_redirect_lab(monkeypatch):
    monkeypatch.setenv('DOCKER_HOST','tcp://unapproved.invalid:2375')
    monkeypatch.setenv('DOCKER_CONTEXT','remote')
    monkeypatch.setenv('DOCKER_TLS_VERIFY','1')
    monkeypatch.setenv('DOCKER_CERT_PATH','/unapproved')
    env=lab.environment()
    assert env['DOCKER_HOST']=='unix:///var/run/docker.sock'
    assert not {'DOCKER_CONTEXT','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'} & env.keys()


def test_extra_secret_metadata_rejected(monkeypatch):
    a=authorization();a['password']='SYNTHETIC-NEVER-RETAIN'
    monkeypatch.setattr(lab,'private_file',lambda _:json.dumps(a).encode())
    with pytest.raises(lab.Reject):lab.authorization(SimpleNamespace(authorization_file='synthetic',commit='a'*40,tree='b'*40,action='install'))


def test_no_insecure_password_fallback(monkeypatch):
    monkeypatch.setattr(lab,'source_check',lambda *a:None);monkeypatch.setattr(lab,'existing_install',lambda:None)
    monkeypatch.setattr(lab.os,'geteuid',lambda:0)
    monkeypatch.setattr(lab,'open',lambda *a:(_ for _ in ()).throw(OSError('no controlling terminal')),raising=False)
    monkeypatch.setattr(lab,'authorization',lambda *a:pytest.fail('No authorization/mutation after missing secure TTY'))
    assert lab.main(['install','--execute','--commit','a'*40,'--tree','b'*40])==1
