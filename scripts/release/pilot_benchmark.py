"""Repeated synthetic polling with explicit container CPU/RAM limits and no internet."""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import uuid


def command(args, *, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=1200)
    if check and result.returncode:
        raise RuntimeError('Isolated benchmark command failed: ' + args[0])
    return result


def measure(backend, postgres, output):
    backend = command(['docker','image','inspect','--format','{{.Id}}',backend]).stdout.strip()
    postgres = command(['docker','image','inspect','--format','{{.Id}}',postgres]).stdout.strip()
    prefix = 'noc119-bench-' + uuid.uuid4().hex[:10]
    network, database = prefix + '-net', prefix + '-db'
    volume=prefix+'-data'
    owned_network=owned_database=owned_volume=False
    records = []
    report = {'scope':'Synthetic2ms transport; no live equipment; process CPU/RSS exclude PostgreSQL',
              'backend':backend,'postgres':postgres,'host_cpus':os.cpu_count(),
              'host_memtotal':next(l.strip() for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemTotal:')),
              'limits':{'backend_cpus':2,'backend_memory_mib':768,'database_cpus':2,'database_memory_mib':1024,'polling_threads':8,'pool_size':10},
              'database_storage':'Disposable named Docker volume on CI host filesystem; physical medium unverified; not tmpfs',
              'records':records,'target_seconds':15,'target_status':'NOT TESTED'}
    try:
        command(['docker','network','create','--internal',network])
        owned_network=True
        if command(['docker','volume','inspect',volume],check=False).returncode==0:raise RuntimeError('Existing volume retained; refusing benchmark')
        command(['docker','volume','create',volume]);owned_volume=True
        uid=command(['docker','run','--rm','--network','none','--entrypoint','id',postgres,'-u','postgres']).stdout.strip()
        command(['docker','run','--rm','--network','none','--read-only','--user','0:0','--cap-drop','ALL','--cap-add','CHOWN','--mount',f'type=volume,src={volume},dst=/var/lib/postgresql/data','--entrypoint','chown',postgres,uid+':'+uid,'/var/lib/postgresql/data'])
        command(['docker','run','-d','--name',database,'--network',network,'--network-alias','db','--user',uid+':'+uid,'--cap-drop','ALL','--security-opt','no-new-privileges',
                 '--cpus','2','--memory','1g','--mount',f'type=volume,src={volume},dst=/var/lib/postgresql/data',
                 '--tmpfs',f'/var/run/postgresql:rw,uid={uid},gid={uid},mode=700,size=16m',
                 '-e','POSTGRES_USER=synthetic','-e','POSTGRES_PASSWORD=synthetic-ci-only','-e','POSTGRES_DB=nwo_noc',postgres])
        owned_database=True
        import time
        for attempt in range(60):
            if command(['docker','exec',database,'pg_isready','-U','synthetic','-d','nwo_noc'],check=False).returncode==0:break
            time.sleep(1)
        else: raise RuntimeError('Disposable PostgreSQL unavailable')
        report['postgres_settings']=command(['docker','exec',database,'psql','-U','synthetic','-d','nwo_noc','-A','-t','-c',"SELECT name||'='||setting FROM pg_settings WHERE name IN('fsync','synchronous_commit','shared_buffers','max_connections','full_page_writes') ORDER BY name"]).stdout.splitlines()
        for repeat in range(1,4):
            result=command(['docker','run','--rm','--network',network,'--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
                '--cpus','2','--memory','768m','--tmpfs','/tmp:rw,noexec,nosuid,size=128m',
                '-e','SECRET_KEY=noc119-synthetic-secret-key-noc119-synthetic', '-e','ADMIN_PASSWORD=synthetic-ci-only',
                '-e','DATABASE_URL=postgresql+psycopg://synthetic:synthetic-ci-only@db:5432/nwo_noc',
                '-e','APP_ENV=staging','-e','OFFLINE_MODE=true','-e','SYNTHETIC_MONITORING_ENABLED=true','-e','RF_SCHEDULER_ENABLED=true',
                '--entrypoint','python',backend,'-m','app.monitoring_benchmark'])
            rows=[json.loads(l) for l in result.stdout.splitlines() if l.startswith('{')]
            if [r.get('devices') for r in rows] != [250,500,1000]:raise ValueError('Incomplete benchmark evidence')
            for row in rows:
                if row['samples_and_unique_receipts'] != row['devices']*6 or len(row['rounds']) != 6:raise ValueError('Lost sample/receipt evidence')
                records.append({'repeat':repeat,**row})
        samples=[round['elapsed_seconds'] for record in records if record['devices']==1000 for round in record['rounds']]
        report.update(min_seconds=min(samples),median_seconds=statistics.median(samples),max_seconds=max(samples),
                      target_status='PASS' if max(samples)<=15 else 'FAIL', measured_rounds=len(samples))
    finally:
        if owned_database:command(['docker','rm','-f',database],check=False)
        if owned_volume:command(['docker','volume','rm',volume],check=False)
        if owned_network:command(['docker','network','rm',network],check=False)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--backend',required=True);p.add_argument('--postgres',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();measure(a.backend,a.postgres,a.output)
