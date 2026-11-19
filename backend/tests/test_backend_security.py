"""NOC118 strict evidence, real disposable signing and runtime build boundaries."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/host'))
from evidence import verify_release_evidence
import policy
from test_host_installer import manifest


def bundle(tmp_path):
    a = manifest(); a['purpose'] = 'release'
    a['images'] = {k: 'registry.example.invalid/' + k + '@sha256:' + c * 64 for k, c in [('backend', 'c'), ('frontend', 'd'), ('postgres', 'e'), ('redis', 'f')]}
    spdx = {'spdxVersion': 'SPDX-2.3', 'packages': [{'name': 'synthetic'}]}
    payloads = {
        'sbom': {'source': spdx, 'images': {r: spdx for r in a['images']}},
        'dependencies': {'pip': {'dependencies': [{'name': 'synthetic', 'version': '1.0', 'vulns': []}]}, 'npm': {'auditReportVersion': 2, 'vulnerabilities': {}, 'metadata': {'dependencies': {'dev': 1}, 'vulnerabilities': {k: 0 for k in ['info', 'low', 'moderate', 'high', 'critical', 'total']}}}},
        'containers': {r: {'SchemaVersion': 2, 'Metadata': {'ImageID': 'sha256:' + '1' * 64, 'RepoDigests': [ref]}, 'Results': [{'Packages': [{'Name': 'synthetic'}], 'Vulnerabilities': []}]} for r, ref in a['images'].items()},
        'provenance': {'source_commit': a['commit'], 'source_tree': a['tree'], 'subjects': a['images'], 'builder': 'synthetic-disposable-builder', 'invocation_id': 'synthetic-run', 'materials': ['python@sha256:' + 'f' * 64]},
    }
    docs = {k: {'format': 1, 'kind': k, **{x: a[x] for x in ['commit', 'tree', 'images']}, 'payload': v} for k, v in payloads.items()}
    def save():
        a['evidence'] = {}
        for kind, doc in docs.items():
            raw = json.dumps(doc).encode(); name = kind + '.json'
            p = tmp_path / name; p.write_bytes(raw); p.chmod(0o600)
            a['evidence'][kind] = {'file': name, 'sha256': hashlib.sha256(raw).hexdigest()}
    save()
    return a, docs, save


def reader(path, **kwargs):
    return policy.private_file(path, owner=os.getuid(), **kwargs)


def test_complete_private_evidence_accepted(tmp_path):
    a, _, _ = bundle(tmp_path)
    verify_release_evidence(a, tmp_path / 'approved', reader)


@pytest.mark.parametrize('kind', ['sbom', 'dependencies', 'containers', 'provenance'])
def test_missing_evidence_fails_closed(tmp_path, kind):
    a, _, _ = bundle(tmp_path)
    (tmp_path / (kind + '.json')).unlink()
    with pytest.raises(OSError): verify_release_evidence(a, tmp_path / 'approved', reader)


@pytest.mark.parametrize('field', ['commit', 'tree', 'images'])
def test_mismatched_evidence_binding_rejected(tmp_path, field):
    a, docs, save = bundle(tmp_path)
    docs['provenance'][field] = 'wrong'; save()
    with pytest.raises(ValueError, match='binding'): verify_release_evidence(a, tmp_path / 'approved', reader)


def test_hash_or_path_manipulation_rejected(tmp_path):
    a, _, _ = bundle(tmp_path)
    a['evidence']['sbom']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='digest'): verify_release_evidence(a, tmp_path / 'approved', reader)
    a['evidence']['sbom']['file'] = '../escape'
    with pytest.raises(ValueError, match='filename'): verify_release_evidence(a, tmp_path / 'approved', reader)


@pytest.mark.parametrize('kind,mutate', [
    ('sbom', lambda d: d['images'].pop('postgres')),
    ('dependencies', lambda d: d['npm']['metadata']['vulnerabilities'].update(total=1)),
    ('dependencies', lambda d: d['pip']['dependencies'][0].update(vulns=[{'id': 'synthetic'}])),
    ('containers', lambda d: d['backend']['Results'][0].update(Vulnerabilities=[{'Severity': 'HIGH'}])),
    ('containers', lambda d: d['frontend']['Metadata'].update(RepoDigests=['wrong'])),
    ('provenance', lambda d: d.update(materials=['python:latest'])),
    ('provenance', lambda d: d.update(source_commit='f' * 40)),
])
def test_incomplete_unsafe_or_unbound_evidence_rejected(tmp_path, kind, mutate):
    a, docs, save = bundle(tmp_path); mutate(docs[kind]['payload']); save()
    with pytest.raises(ValueError): verify_release_evidence(a, tmp_path / 'approved', reader)


def test_real_signed_approval_and_altered_manifest(tmp_path, monkeypatch):
    a, _, _ = bundle(tmp_path)
    private = tmp_path / 'key'; public = tmp_path / 'trusted.pub'; approved = tmp_path / 'approval'; sig = tmp_path / 'approval.sig'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(private)], check=True, capture_output=True)
    subprocess.run(['openssl', 'pkey', '-in', str(private), '-pubout', '-out', str(public)], check=True, capture_output=True)
    approved.write_text(json.dumps(a))
    subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(private), '-rawin', '-in', str(approved), '-out', str(sig)], check=True, capture_output=True)
    for p in [approved, sig, public]: p.chmod(0o600)
    original = policy.private_file
    monkeypatch.setattr(policy, 'private_file', lambda p, **kw: original(public if str(p) == '/etc/nwo-noc-release.pub' else p, owner=os.getuid(), **kw))
    assert policy.approval(approved)['commit'] == a['commit']
    approved.write_text(json.dumps({**a, 'commit': 'f' * 40}))
    with pytest.raises(ValueError, match='Signed release'): policy.approval(approved)


def test_backend_runtime_build_keeps_locked_binary_dependencies_and_nonroot():
    source = (ROOT / 'backend/Dockerfile').read_text()
    assert source.count('python:3.12-slim-bookworm@sha256:') == 2
    assert '--only-binary=:all:' in source and '--require-hashes' in source
    assert 'COPY --from=dependencies /install/ /usr/local/' in source
    assert 'pip uninstall -y pip' in source
    assert 'USER 10001:10001' in source and 'ProxyHandler({})' in source
    assert '--allow-remove-essential' not in source and 'dpkg --force' not in source


def test_image_spdx_preserves_identity_and_refuses_incomplete_inventory(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / 'scripts/release'))
    import image_sbom
    identity = 'sha256:' + 'a' * 64
    raw = json.dumps({'Metadata': {'ImageID': identity}, 'Results': [{'Packages': [{'Name': 'synthetic'}]}]}).encode()
    (tmp_path / 'summary.json').write_text(json.dumps([{'image_id': identity}]))
    (tmp_path / 'image-0.json').write_bytes(raw)
    def run(args, **kwargs):
        assert '--network' in args and 'none' in args and 'convert' in args
        return subprocess.CompletedProcess(args, 0, json.dumps({'spdxVersion': 'SPDX-2.3', 'packages': [{'name': 'synthetic'}]}).encode(), b'')
    monkeypatch.setattr(image_sbom.subprocess, 'run', run)
    index = image_sbom.export(tmp_path)
    assert index[0]['image_id'] == identity
    assert index[0]['scan_sha256'] == hashlib.sha256(raw).hexdigest()
    (tmp_path / 'summary.json').write_text(json.dumps([{'image_id': 'sha256:' + 'b' * 64}]))
    with pytest.raises(ValueError, match='identity'): image_sbom.export(tmp_path)


@pytest.mark.parametrize('mutate', [
    lambda a: a.pop('evidence'),
    lambda a: a['evidence']['sbom'].update(file='/etc/passwd'),
])
def test_missing_contract_and_absolute_paths_rejected(tmp_path, mutate):
    a, _, _ = bundle(tmp_path); mutate(a)
    with pytest.raises(ValueError): verify_release_evidence(a, tmp_path / 'approved', reader)
