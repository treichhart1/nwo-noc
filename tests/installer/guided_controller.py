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
    result=run(['python3',source/'scripts/host/lab.py','prepare','--execute','--commit',commit,'--tree',tree,'--work',prepared,'--authorization-file',auth])
    archive=target/'prepared.tar';run(['tar','-cf',archive,'-C',prepared,'.'])
    archive.chmod(0o600);os.chown(archive,int(os.environ['SUDO_UID']),int(os.environ['SUDO_GID']))
    target.chmod(0o711)
    print(json.dumps({'archive':str(archive),'source':commit,'tree':tree,'purpose':'controlled-test','prepared':True}))


if __name__=='__main__':main()
