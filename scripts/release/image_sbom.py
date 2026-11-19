"""Export complete Trivy SPDX inventories, bound to retained raw report hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from image_security import SCANNER


def export(root):
    root = Path(root).resolve()
    summary = json.loads((root / 'summary.json').read_text())
    expected = {v['image_id'] for v in summary}
    index = []
    for path in sorted(root.glob('image-*.json')):
        raw = path.read_bytes()
        report = json.loads(raw)
        identity = report.get('Metadata', {}).get('ImageID')
        if identity not in expected:
            raise ValueError('SPDX source image identity not in retained scan summary')
        result = subprocess.run(['docker', 'run', '--rm', '--read-only', '--network', 'none', '--cap-drop', 'ALL', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m', '-v', f'{root}:/evidence:ro', SCANNER, 'convert', '--format', 'spdx-json', '/evidence/' + path.name], capture_output=True, timeout=120, check=True)
        spdx = json.loads(result.stdout)
        if spdx.get('spdxVersion') != 'SPDX-2.3' or not spdx.get('packages'):
            raise ValueError('Complete image SPDX conversion unavailable')
        target = root / ('sbom-' + path.stem + '.spdx.json')
        target.write_bytes(result.stdout)
        index.append({'image_id': identity, 'scan': path.name, 'scan_sha256': hashlib.sha256(raw).hexdigest(), 'sbom': target.name, 'sbom_sha256': hashlib.sha256(result.stdout).hexdigest(), 'packages': len(spdx['packages'])})
    if {r['image_id'] for r in index} != expected or len(index) != len(expected):
        raise ValueError('Missing/duplicate image SPDX inventory')
    (root / 'sbom-index.json').write_text(json.dumps(index, indent=2) + '\n')
    return index


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory')
    print(json.dumps(export(p.parse_args().directory)))
