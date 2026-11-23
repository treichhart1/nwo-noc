"""Bounded native-import forensics in a synthetic VM; never a production host."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import uuid

CODE = r'''
import json, subprocess, sys, time
records=[]
for name in ('numpy','uvloop','rasterio','pyproj'):
    started=time.monotonic()
    try:
        p=subprocess.run([sys.executable,'-c','import '+name],capture_output=True,text=True,timeout=20)
        code=p.returncode
        error=p.stderr.lower()
        flags={'cpu_baseline':'baseline optimizations' in error or 'x86_v2' in error,
               'illegal_instruction':'illegal instruction' in error,
               'import_error':'importerror:' in error or 'modulenotfounderror:' in error}
    except subprocess.TimeoutExpired:
        code=124
        flags={'cpu_baseline':False,'illegal_instruction':False,'import_error':False}
    records.append({'module':name,'exit_code':code,'seconds':round(time.monotonic()-started,3),
                    'timed_out':code==124,'signals':flags})
print(json.dumps({'modules':records,'all_passed':all(r['exit_code']==0 for r in records)}))
'''


def probe():
    if Path('/etc/hostname').read_text().strip()!='noc119-source' or Path('/var/lib/nwo-noc').exists():
        raise RuntimeError('Native probes require the empty owned synthetic source VM')
    image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}','noc113-backend:base'],text=True).strip()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',image):
        raise RuntimeError('Exact synthetic image identity required')
    name='noc119-native-'+uuid.uuid4().hex
    if subprocess.run(['docker','container','inspect',name],capture_output=True).returncode==0:
        raise RuntimeError('Existing container retained')
    try:
        result=subprocess.run(['docker','run','--rm','--name',name,'--network','none','--read-only',
                               '--cap-drop','ALL','--security-opt','no-new-privileges:true',
                               '--memory','1g','--pids-limit','512','--tmpfs','/tmp:rw,noexec,nosuid,size=32m',
                               '--entrypoint','python',image,'-c',CODE],
                              capture_output=True,text=True,timeout=110,check=True)
        record=json.loads(result.stdout)
        record['image_id']=image
        record['scope']='Synthetic ABI forensics; no raw errors, credentials or device access'
        return record
    finally:
        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=15)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute-disposable-host',action='store_true');a=p.parse_args()
    if not a.execute_disposable_host:p.error('Explicit disposable source-VM probe required')
    print(json.dumps(probe()))
