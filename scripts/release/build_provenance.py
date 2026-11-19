"""Record unsigned CI build provenance; not a production signature/attestation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
summary = json.loads((root / 'summary.json').read_text())
record = {
    'format': 1,
    'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'source_tree': subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], text=True).strip(),
    'builder': 'GitHub Actions ubuntu-24.04',
    'invocation_id': 'https://github.com/treichhart1/nwo-noc/actions/runs/' + os.environ.get('GITHUB_RUN_ID', 'local-unverified'),
    'subjects': {r['reference']: r['image_id'] for r in summary},
    'materials': list(json.loads(Path('deploy/image-lock.json').read_text())['images'].values()),
    'source_files': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path('backend/Dockerfile'), Path('backend/requirements.lock'), Path('frontend/Dockerfile'), Path('frontend/package-lock.json'), Path('deploy/Dockerfile.postgres')]},
    'retained_reports': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob('*.json'))},
    'risk_review': {'reference': 'docs/release/NOC-118/blocker-matrix.json', 'open_blockers': [r['id'] for r in json.loads(Path('docs/release/NOC-118/blocker-matrix.json').read_text())['rows'] if r['status'] != 'PASS']},
    'scope': 'Unsigned CI record of local immutable config IDs; production publisher must supply registry digests/RepoDigests, independently reviewed evidence envelopes and detached Ed25519 approval. No signing key or release authorization generated.',
}
(root / 'build-provenance.json').write_text(json.dumps(record, indent=2) + '\n')
