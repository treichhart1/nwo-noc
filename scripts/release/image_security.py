"""Pinned scanner evidence; vulnerability findings are reported, never called release approval."""
import argparse
import json
from pathlib import Path
import subprocess

SCANNER = 'aquasec/trivy:0.69.3@sha256:bcc376de8d77cfe086a917230e818dc9f8528e3c852f7b1aff648949b6258d1c'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images',nargs='+')
    parser.add_argument('--output',required=True)
    parser.add_argument('--require-no-high',action='store_true',help='Fail release gate after retaining evidence if HIGH/CRITICAL findings exist')
    args=parser.parse_args()
    root=Path(args.output);root.mkdir(parents=True,exist_ok=False)
    summary=[]
    seen=set()
    for i,ref in enumerate(args.images):
        # Resolve local built names once; scan only an exact immutable image ID.
        image=json.loads(subprocess.check_output(['docker','image','inspect',ref]))[0]
        immutable=image['Id']
        if immutable in seen:continue
        seen.add(immutable)
        result=subprocess.run(['docker','run','--rm','-v','/var/run/docker.sock:/var/run/docker.sock','-v','noc116-trivy-cache:/root/.cache/',SCANNER,
            'image','--image-src','docker','--scanners','vuln,license','--license-full','--format','json','--timeout','10m',immutable],capture_output=True,timeout=660,check=False)
        if result.returncode:raise RuntimeError('Image scanner failed; scan is NOT TESTED, not clean')
        body=json.loads(result.stdout)
        if not isinstance(body.get('Results'), list) or not body['Results'] or body.get('Metadata', {}).get('ImageID') != immutable:
            raise RuntimeError('Image scan identity/results unavailable; do not classify as clean')
        (root/f'image-{i}.json').write_text(json.dumps(body,indent=2))
        findings=[v for r in body.get('Results',[]) for v in r.get('Vulnerabilities',[])]
        row={'reference':ref,'image_id':immutable,'critical':sum(v['Severity']=='CRITICAL' for v in findings),'high':sum(v['Severity']=='HIGH' for v in findings),'total':len(findings),'status':'FAIL' if any(v['Severity'] in ('CRITICAL','HIGH') for v in findings) else 'PASS','scope':'Scanner database at execution; license findings need independent legal review; no release authorization'}
        row['license_review']='BLOCKED: independent review required'
        row['licenses']=sorted({str(v.get('Name','UNKNOWN')) for r in body.get('Results',[]) for v in r.get('Licenses',[])})
        summary.append(row)
        print(json.dumps(row),flush=True)
    (root/'summary.json').write_text(json.dumps(summary,indent=2))
    if args.require_no_high and any(r['status']=='FAIL' for r in summary):
        raise SystemExit('Release image security gate failed; retained findings require remediation/review')


if __name__=='__main__':main()
