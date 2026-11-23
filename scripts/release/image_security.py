"""Pinned scanner evidence; vulnerability findings are reported, never called release approval."""
import argparse
import json
import re
from pathlib import Path
import subprocess

SCANNER = 'aquasec/trivy:0.69.3@sha256:bcc376de8d77cfe086a917230e818dc9f8528e3c852f7b1aff648949b6258d1c'



def scanner_error(root, index, image_id, kind, code, stderr="", observed_id=None, results_count=None):
    """Persist typed scanner failures, never raw credential-bearing output."""
    lower=stderr.lower()
    phrases={
        'registry_rate_limit':('toomanyrequests','too many requests','rate limit','429 too many','status code: 429'),
        'registry_auth':('unauthorized','authentication required','401 unauthorized','status code: 401'),
        'registry_denied':('access denied','403 forbidden','status code: 403'),
        'timeout':('timeout','timed out','deadline exceeded'),
        'dns':('no such host','name resolution'),
        'network':('connection refused','network is unreachable','connection reset'),
        'tls':('x509','tls handshake','certificate'),
        'storage':('no space left','disk quota'),
        'permission':('permission denied',),
        'missing_image':('no such image','image not found','manifest unknown'),
        'unsupported_cli':('unknown flag','unknown command'),
        'docker_api':('client version','api version'),
        'download':('failed to download','failed to fetch'),
    }
    error={'image_id':image_id,'kind':kind,'return_code':int(code),
           'flags':{key:any(value in lower for value in values) for key,values in phrases.items()},
           'observed_image_id':observed_id if isinstance(observed_id,str) and re.fullmatch(r'sha256:[a-f0-9]{64}',observed_id) else None,
           'results_count':results_count if type(results_count) is int and results_count>=0 else None,
           'scan_identity_verified':False,'status':'NOT TESTED'}
    (Path(root)/f'error-{index}.json').write_text(json.dumps(error,indent=2))
    print(json.dumps({'image_scan_error':error}),flush=True)
    return error



def retain_unverified_scan(root, index, raw):
    """Keep complete finding reports on execution failure, never arbitrary output."""
    try:body=json.loads(raw)
    except (ValueError,TypeError):return False
    if not isinstance(body,dict) or not isinstance(body.get('Results'),list):return False
    (Path(root)/f'unverified-image-{index}.json').write_text(json.dumps(body,indent=2))
    return True


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
        try:
            result=subprocess.run(['docker','run','--rm','-v','/var/run/docker.sock:/var/run/docker.sock','-v','noc116-trivy-cache:/root/.cache/',SCANNER,
                'image','--image-src','docker','--scanners','vuln,license','--license-full','--format','json','--timeout','10m',immutable],capture_output=True,timeout=660,check=False)
        except subprocess.TimeoutExpired as error:
            retain_unverified_scan(root,i,error.output or b'')
            scanner_error(root,i,immutable,'scanner_timeout',124,(error.stderr or b'').decode(errors='replace'))
            raise RuntimeError('Image scanner timed out; no clean result') from None
        if result.returncode:
            retain_unverified_scan(root,i,result.stdout)
            scanner_error(root,i,immutable,'scanner_process',result.returncode,result.stderr.decode(errors='replace'))
            raise RuntimeError('Image scanner failed; scan is NOT TESTED, not clean')
        try:body=json.loads(result.stdout)
        except (ValueError,TypeError):
            scanner_error(root,i,immutable,'invalid_json',0,result.stderr.decode(errors='replace'))
            raise RuntimeError('Image scanner JSON unavailable; no clean result') from None
        # Retain every valid JSON report even if identity/schema verification fails.
        (root/f'image-{i}.json').write_text(json.dumps(body,indent=2))
        metadata=body.get('Metadata') if isinstance(body,dict) else None
        observed=metadata.get('ImageID') if isinstance(metadata,dict) else None
        results=body.get('Results') if isinstance(body,dict) else None
        if not isinstance(results,list) or not results or observed != immutable:
            count=len(results) if isinstance(results,list) else None
            scanner_error(root,i,immutable,'identity_results',0,result.stderr.decode(errors='replace'),observed,count)
            raise RuntimeError('Image scan identity/results unavailable; do not classify as clean')
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
