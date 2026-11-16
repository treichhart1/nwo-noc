"""Deterministic SPDX2.3 source-lock inventory; not an installed OS/native-library SBOM."""
import hashlib
import json
from pathlib import Path
from check_supply_chain import read_lock

ROOT = Path(__file__).resolve().parents[2]


def build(root=ROOT):
    packages = []
    runtime_licenses = {p['name'].lower().replace('_','-'):p for p in json.loads((root/'docs/audits/NOC-111-runtime-licenses.json').read_text())}
    for filename in ('requirements.lock','requirements-test.lock','requirements-audit.lock'):
        for name, version in sorted(read_lock(root/'backend'/filename).items()):
            package = {'name':name,'version':version,'ecosystem':'pypi','scope':filename}
            old=runtime_licenses.get(name,{})
            package['license']=old.get('license_expression') or old.get('license') or 'NOASSERTION' if old.get('version')==version else 'NOASSERTION'
            if len(package['license'])>100:package['license']='NOASSERTION'
            packages.append(package)
    lock=json.loads((root/'frontend/package-lock.json').read_text())
    for path, item in sorted(lock['packages'].items()):
        if not path:continue
        name=path.split('node_modules/')[-1]
        if not item.get('version') or not str(item.get('integrity','')).startswith('sha512-'):
            raise ValueError('Immutable npm package version/integrity required')
        packages.append({'name':name,'version':item['version'],'ecosystem':'npm','scope':path,'license':item.get('license') or 'NOASSERTION'})
    images=json.loads((root/'deploy/image-lock.json').read_text())['images']
    fingerprint=hashlib.sha256(json.dumps([packages,images],sort_keys=True).encode()).hexdigest()
    spdx=[]
    for i,p in enumerate(packages):
        spdx.append({'SPDXID':f'SPDXRef-Package-{i}','name':p['name'],'versionInfo':p['version'],'downloadLocation':'NOASSERTION','filesAnalyzed':False,'licenseConcluded':'NOASSERTION','licenseDeclared':p['license'],'copyrightText':'NOASSERTION','externalRefs':[{'referenceCategory':'PACKAGE-MANAGER','referenceType':'purl','referenceLocator':f"pkg:{p['ecosystem']}/{p['name']}@{p['version']}"}]})
    return {'spdxVersion':'SPDX-2.3','dataLicense':'CC0-1.0','SPDXID':'SPDXRef-DOCUMENT','name':'nwo-noc-source-locks','documentNamespace':'https://nwo-noc.invalid/sbom/'+fingerprint,'creationInfo':{'creators':['Tool: nwo-noc-lock-inventory'],'created':'2026-11-16T00:00:00Z'},'packages':spdx,'annotations':[{'annotationType':'OTHER','annotator':'Tool: nwo-noc-lock-inventory','annotationDate':'2026-11-16T00:00:00Z','comment':'Source lock inventory only. NOASSERTION requires review. Installed OS/native/container components require separate image scans. Redis server licensing remains an operator/legal decision. No license approval is granted.'}]}


if __name__=='__main__':
    print(json.dumps(build(),indent=2,sort_keys=True))
