"""Validate signed release evidence before any host/image mutation; no downloads."""
import hashlib
import hmac
import json
import re
from pathlib import Path

KINDS = {'sbom', 'dependencies', 'containers', 'provenance'}


def _verify_release_evidence(approved, manifest_path, read_private):
    entries = approved.get('evidence')
    if not isinstance(entries, dict) or set(entries) != KINDS:
        raise ValueError('Complete signed SBOM, dependency, container and provenance evidence required')
    reports = {}
    for kind, entry in entries.items():
        if not isinstance(entry, dict) or set(entry) != {'file', 'sha256'}:
            raise ValueError('Invalid release evidence reference')
        name, digest = entry['file'], entry['sha256']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', name) or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('Private local evidence filename and SHA256 required')
        raw = read_private(Path(manifest_path).parent / name, max_bytes=64 * 1024 * 1024)
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest):
            raise ValueError('Release evidence digest mismatch')
        doc = json.loads(raw)
        if not isinstance(doc, dict) or doc.get('format') != 1 or doc.get('kind') != kind or any(doc.get(key) != approved[key] for key in ('commit', 'tree', 'images')):
            raise ValueError('Release evidence source/image binding mismatch')
        reports[kind] = doc.get('payload')
    sbom = reports['sbom']
    def spdx(value):
        return isinstance(value, dict) and value.get('spdxVersion') == 'SPDX-2.3' and isinstance(value.get('packages'), list) and bool(value['packages'])
    if not isinstance(sbom, dict) or not spdx(sbom.get('source')) or set(sbom.get('images', {})) != set(approved['images']) or not all(spdx(v) for v in sbom['images'].values()):
        raise ValueError('Complete source and per-image SPDX inventories required')
    deps = reports['dependencies']
    if not isinstance(deps, dict) or not isinstance(deps.get('pip', {}).get('dependencies'), list) or not deps['pip']['dependencies'] or not isinstance(deps.get('npm', {}).get('metadata', {}).get('vulnerabilities'), dict):
        raise ValueError('Full pip and npm audit reports required')
    if any(not isinstance(p, dict) or not p.get('name') or not p.get('version') or not isinstance(p.get('vulns'), list) for p in deps['pip']['dependencies']) or deps['npm'].get('auditReportVersion') != 2 or not isinstance(deps['npm'].get('vulnerabilities'), dict) or not isinstance(deps['npm']['metadata'].get('dependencies', {}).get('dev'), int) or deps['npm']['metadata']['dependencies']['dev'] < 1 or set(deps['npm']['metadata']['vulnerabilities']) != {'info', 'low', 'moderate', 'high', 'critical', 'total'}:
        raise ValueError('Complete dependency reports including development dependencies required')
    if any(p['vulns'] for p in deps['pip']['dependencies']) or any(type(v) is not int or v != 0 for v in deps['npm']['metadata']['vulnerabilities'].values()):
        raise ValueError('Dependency vulnerabilities block release approval')
    containers = reports['containers']
    if not isinstance(containers, dict) or set(containers) != set(approved['images']):
        raise ValueError('Every approved runtime image needs a vulnerability report')
    for role, report in containers.items():
        if not isinstance(report, dict) or report.get('SchemaVersion') != 2 or not re.fullmatch(r'sha256:[a-f0-9]{64}', report.get('Metadata', {}).get('ImageID', '')) or approved['images'][role] not in report.get('Metadata', {}).get('RepoDigests', []) or not isinstance(report.get('Results'), list) or not report['Results'] or not any(r.get('Packages') for r in report['Results']):
            raise ValueError('Container report identity or package inventory missing')
        if any(not isinstance(v, dict) or v.get('Severity') not in {'UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'} for r in report['Results'] for v in r.get('Vulnerabilities', []) or []):
            raise ValueError('Malformed container vulnerability severity')
        if any(v.get('Severity') in ('HIGH', 'CRITICAL') for r in report['Results'] for v in r.get('Vulnerabilities', []) or []):
            raise ValueError('HIGH/CRITICAL container findings block release approval')
    provenance = reports['provenance']
    if not isinstance(provenance, dict) or provenance.get('source_commit') != approved['commit'] or provenance.get('source_tree') != approved['tree'] or provenance.get('subjects') != approved['images'] or not isinstance(provenance.get('builder'), str) or not provenance['builder'] or not isinstance(provenance.get('invocation_id'), str) or not provenance['invocation_id'] or not isinstance(provenance.get('materials'), list) or not provenance['materials'] or any(not isinstance(v, str) or not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}', v) for v in provenance['materials']):
        raise ValueError('Bound build provenance and immutable input materials required')


def verify_release_evidence(approved, manifest_path, read_private):
    try:
        return _verify_release_evidence(approved, manifest_path, read_private)
    except (KeyError, TypeError, AttributeError):
        raise ValueError('Malformed release evidence') from None
