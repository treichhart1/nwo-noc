"""NOC117 immutable helper provenance and fail-closed three-image release approval."""
import hashlib
import json
from pathlib import Path
import sys
import pytest
from test_host_installer import manifest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/host'))
import policy


def test_vendored_gosu_exact_reviewed_source():
    root=ROOT/'deploy/vendor/gosu'
    provenance=json.loads((root/'provenance.json').read_text())
    assert provenance['commit']=='29b939aa5bd4dc588af765b8248f6e39150d2565'
    for name,digest in provenance['files'].items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
    assert 'Apache License' in (root/'LICENSE').read_text()


def test_missing_database_image_rejected_for_production_release(tmp_path,monkeypatch):
    import artifacts
    value=manifest();value['purpose']='release'
    value['images']={k:'registry.example.invalid/noc@'+v for k,v in value['images'].items()}
    monkeypatch.setattr(policy,'private_file',lambda p:json.dumps(value).encode())
    monkeypatch.setattr(artifacts,'verify_signature',lambda *a:None)
    with pytest.raises(ValueError,match='PostgreSQL'):policy.approval(tmp_path/'approved')
    value['images']['postgres']='registry.example.invalid/pg@sha256:'+'e'*64
    with pytest.raises(ValueError,match='Complete signed'):
        policy.approval(tmp_path/'approved')


def test_postgres_image_only_changes_helper_not_entrypoint_or_engine():
    source=(ROOT/'deploy/Dockerfile.postgres').read_text()
    assert 'GOTOOLCHAIN=local' in source and 'go build -mod=readonly' in source
    assert 'ENTRYPOINT' not in source and 'CMD ' not in source
    assert 'go version -m /out/gosu' in source
    assert 'USER ' not in source


def test_installer_keeps_explicit_hardened_database_digest(tmp_path,monkeypatch):
    import manager
    from test_host_installer import fake_host
    host=fake_host(tmp_path);value=manifest();ref='sha256:'+'e'*64
    value['images']['postgres']=ref
    calls=[]
    def image(reference):
        calls.append(reference)
        return {'Config':{'User':'' if reference==ref else '10001:10001' if reference==value['images']['backend'] else '101:101','Labels':{'org.opencontainers.image.revision':value['commit']}}}
    monkeypatch.setattr(host,'image',image)
    monkeypatch.setattr(manager,'git',lambda *args:'' if args[0]=='ls-tree' else json.dumps({'images':{'postgres:16-alpine':'base-must-not-replace-approved-db','redis:7-alpine':'synthetic-redis'}}))
    host.images(value)
    assert value['images']['postgres']==ref and 'base-must-not-replace-approved-db' not in calls


def test_installer_rejects_database_with_mismatched_source_revision(tmp_path,monkeypatch):
    from test_host_installer import fake_host
    host=fake_host(tmp_path);value=manifest();ref='sha256:'+'e'*64;value['images']['postgres']=ref
    def image(reference):return {'Config':{'User':'' if reference==ref else '10001:10001' if reference==value['images']['backend'] else '101:101','Labels':{'org.opencontainers.image.revision':'b'*40 if reference==ref else value['commit']}}}
    monkeypatch.setattr(host,'image',image)
    with pytest.raises(ValueError,match='source revision'):host.images(value)
