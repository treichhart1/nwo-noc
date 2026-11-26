"""Actual systemd backup recovery acceptance on an explicitly synthetic VM only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4


PROBE = '''import errno,json,sys
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,'/opt/nwo-noc/current/scripts/host')
from manager import Host
host=object.__new__(Host);host.root=Path('/')
try:
    host.prepare_ingress()
except OSError as error:
    if sys.argv[1]!='legacy-readonly' or error.errno!=errno.EROFS:raise
    print(json.dumps({'profile':'legacy-readonly','errno':error.errno,'expected_rejection':True}))
else:
    if sys.argv[1]!='fixed-writable':raise AssertionError('Legacy read-only boundary not enforced')
    print(json.dumps({'profile':'fixed-writable','ingress_recovery_write':'PASS'}))
'''


def acceptance(command, invoke, passed, identity):
    state=json.loads(Path('/var/lib/nwo-noc/installation.json').read_text())
    if os.geteuid()!=0 or state.get('purpose')!='controlled-test' or state.get('status')!='ready':
        raise RuntimeError('Only the ready synthetic independent VM may run backup acceptance')
    unit=Path('/etc/systemd/system/nwo-noc-backup.service').read_text()
    paths=next(line.split('=',1)[1].split() for line in unit.splitlines() if line.startswith('ReadWritePaths='))
    legacy={'/var/lib/nwo-noc','/var/log/nwo-noc','/var/backups/nwo-noc','/run/lock'}
    if set(paths)!=legacy|{'/run/nwo-noc-ingress'}:
        raise AssertionError('Unexpected or broad backup filesystem permissions')
    probe=Path('/var/lib/nwo-noc/noc124-ingress-probe.py')
    with probe.open('x') as stream:stream.write(PROBE)
    probe.chmod(0o600)
    for profile, allowed in [('legacy-readonly',sorted(legacy)),('fixed-writable',paths)]:
        result=command(['systemd-run','--wait','--pipe','--collect',
            '--unit=noc124-ingress-'+uuid4().hex,
            '--property=ProtectSystem=strict','--property=ProtectHome=true',
            '--property=NoNewPrivileges=true','--property=PrivateTmp=true',
            '--property=ReadWritePaths='+' '.join(allowed),
            '/usr/bin/python3',probe,profile])
        rows=[json.loads(line) for line in result.stdout.splitlines() if line.startswith(b'{')]
        if len(rows)!=1:raise AssertionError('Systemd sandbox probe evidence unavailable')
        data=rows[0]
        passed('scheduled_backup_sandbox:'+profile,
            data.get('expected_rejection') is True if profile=='legacy-readonly' else data.get('ingress_recovery_write')=='PASS')
        print(json.dumps({'scheduled_backup_sandbox':data}),flush=True)
    # Earlier upgrade/rollback tests deliberately restart ingress repeatedly.
    # Exercise a scheduled backup outside that existing 300-second rate window;
    # never reset the service failure counter or weaken its production limit.
    rate=command(['systemctl','show','nwo-noc-ingress.service','--property=StartLimitIntervalUSec','--value']).stdout.strip()
    if rate!=b'5min':raise AssertionError('Unexpected ingress restart-rate policy')
    print(json.dumps({'scheduled_backup_rate_window':'waiting 301 seconds after upgrade stress; production limits unchanged'}),flush=True)
    time.sleep(301)
    folder=Path('/var/backups/nwo-noc')
    before=set(folder.glob('*.tar.age'))
    started=command(['systemctl','start','nwo-noc-backup.service'],success=False)
    if started.returncode:
        journal=command(['journalctl','-u','nwo-noc-backup.service','-n','80','--no-pager','-o','cat'],success=False)
        text=journal.stdout.decode(errors='replace').lower()
        flags={name:token in text for name,token in {'read_only':'read-only file system','permission':'permission denied','home':'/root/.docker','ingress':'/run/nwo-noc-ingress','docker':'docker','encryption':'backup encryption failed'}.items()}
        print(json.dumps({'scheduled_backup_failure':flags}),flush=True)
        raise RuntimeError('Scheduled backup failed: '+json.dumps(flags))
    passed('scheduled_backup_service_success',command(['systemctl','show','nwo-noc-backup.service','--property=Result','--value']).stdout.strip()==b'success')
    new=set(folder.glob('*.tar.age'))-before
    if len(new)!=1:raise AssertionError('Scheduled backup did not create exactly one ciphertext')
    archive=new.pop()
    metadata=json.loads(archive.with_suffix('.age.json').read_text())
    with archive.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
    passed('scheduled_backup_current_source_and_checksum',metadata['sha256']==checksum and metadata['source_commit']==state['commit'] and metadata['restore_tested'] is False)
    decrypted=subprocess.run(['age','--decrypt','--identity',str(identity),str(archive)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120,check=False)
    passed('scheduled_backup_decrypts',decrypted.returncode==0)
    passed('scheduled_backup_application_recovered',invoke('verify').returncode==0)
    timer=command(['systemctl','is-enabled','nwo-noc-backup.timer'],success=False)
    passed('scheduled_backup_timer_stays_disabled',timer.stdout.strip()==b'disabled')
