"""Build/audit lab bundle on the disposable CI controller; no real server target."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]


def run(argv, **kw):
    return subprocess.run([str(v) for v in argv], check=True, capture_output=True, timeout=2400, **kw)


def main():
    if os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or not os.environ.get('GITHUB_RUN_ID','').isdigit():
        raise RuntimeError('Explicit disposable GitHub controller only')
    target=Path('/var/lib')/('noc123-guided-'+os.environ['GITHUB_RUN_ID'])
    target.mkdir(mode=0o700,exist_ok=False)
    source=target/'source'
    run(['git','-c',f'safe.directory={ROOT}','clone','--shared','--quiet',ROOT,source])
    run(['git','-C',source,'remote','set-url','origin','https://github.com/treichhart1/nwo-noc.git'])
    commit=run(['git','-C',source,'rev-parse','HEAD']).stdout.decode().strip()
    tree=run(['git','-C',source,'rev-parse','HEAD^{tree}']).stdout.decode().strip()
    key=target/'test-backup.key';run(['age-keygen','-o',key]);key.chmod(0o600)
    a={'purpose':'controlled-test','commit':commit,'tree':tree,'reference':'NOC123 disposable CI lab only','confirmation':'PREPARE ISOLATED LAB','ssh_host':'localhost','backup_recipient':run(['age-keygen','-y',key]).stdout.decode().strip(),'backup_destination':'synthetic independent recovery fixture',**{k:True for k in ('isolated_lab_authorized','private_management_reviewed','native_risks_reviewed','backup_custody_reviewed')}}
    auth=target/'authorize.json';auth.write_text(json.dumps(a));auth.chmod(0o600)
    prepared=target/'prepared'
    result=subprocess.run(['python3',str(source/'scripts/host/lab.py'),'prepare','--execute','--commit',commit,'--tree',tree,'--work',str(prepared),'--authorization-file',str(auth)],capture_output=True,timeout=2400)
    retained=ROOT/'pilot-guided-evidence';retained.mkdir(mode=0o755,exist_ok=False)
    import shutil
    for record in prepared.rglob('*.json'):
        if record.name=='authorization.json':continue
        dest=retained/record.relative_to(prepared);dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(record,dest);dest.chmod(0o644)
    if result.returncode:
        safe=[line for line in result.stderr.decode(errors='replace').splitlines() if line.startswith(('Guided lab rejected:','Guided lab operation rejected/failed.'))]
        labels={}
        for log in prepared.glob('*.log'):
            text=log.read_text(errors='replace')
            labels[log.name]={k:phrase in text for k,phrase in {'read_only':'Read-only file system','permission_denied':'Permission denied','noexec':'not executable','invalid_argument':'unrecognized arguments','missing_file':'No such file or directory','network':'ConnectionError','tls':'CERTIFICATE_VERIFY_FAILED','docker_build':'failed to solve','dependency_vulnerability':'Found '}.items()}
        # These two fixture containers receive only public lockfiles/cache paths,
        # never broker secrets, host credentials, customer data or private keys.
        # Keep their exact package-audit stderr for evidence; never build/TLS logs.
        import re
        for name in ('pip-audit.log','npm-audit.log'):
            log=prepared/name
            if log.exists():
                text=log.read_text(errors='replace')[-8000:]
                text=re.sub(r'https?://[^\s]+','<public-registry-url>',text)
                (retained/name).write_text(text)
                print(json.dumps({'public_lock_audit':name,'diagnostic':text}),file=sys.stderr,flush=True)
        failed={'source':commit,'purpose':'controlled-test','status':'FAIL','safe_error':safe[:1],'step_error_flags':labels}
        (retained/'failure-summary.json').write_text(json.dumps(failed,indent=2)+'\n')
        print(json.dumps(failed),file=sys.stderr,flush=True)
        raise RuntimeError('Guided preparation failed; sanitized partial evidence retained')
    archive=target/'prepared.tar';run(['tar','-cf',archive,'-C',prepared,'.'])
    archive.chmod(0o600);os.chown(archive,int(os.environ['SUDO_UID']),int(os.environ['SUDO_GID']))
    target.chmod(0o711)
    print(json.dumps({'archive':str(archive),'source':commit,'tree':tree,'purpose':'controlled-test','prepared':True}))


if __name__=='__main__':main()
