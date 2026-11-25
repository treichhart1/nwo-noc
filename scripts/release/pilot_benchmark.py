"""Repeated synthetic polling with explicit container CPU/RAM limits and no internet."""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import uuid
import math


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
    spool_volume=prefix+'-spool'
    owned_network=owned_database=owned_volume=owned_spool=False
    records = []
    report = {'scope':'Synthetic2ms transport; no live equipment; process CPU/RSS exclude PostgreSQL',
              'backend':backend,'postgres':postgres,'host_cpus':os.cpu_count(),
              'host_memtotal':next(l.strip() for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemTotal:')),
              'limits':{'backend_cpus':2,'backend_memory_mib':768,'database_cpus':2,'database_memory_mib':1024,'polling_threads':8,'pool_size':10},
              'database_storage':'Disposable named Docker volume on CI host filesystem; physical medium unverified; not tmpfs',
              'monitoring_spool_storage':'Separate private named Docker volume; not tmpfs; every file/directory fsync preserved',
              'records':records,'target_seconds':15,'target_status':'NOT TESTED'}
    try:
        command(['docker','network','create','--internal',network])
        owned_network=True
        if command(['docker','volume','inspect',volume],check=False).returncode==0:raise RuntimeError('Existing volume retained; refusing benchmark')
        command(['docker','volume','create',volume]);owned_volume=True
        if command(['docker','volume','inspect',spool_volume],check=False).returncode==0:raise RuntimeError('Existing spool retained; refusing benchmark')
        command(['docker','volume','create',spool_volume]);owned_spool=True
        command(['docker','run','--rm','--network','none','--read-only','--user','0:0','--cap-drop','ALL','--cap-add','CHOWN',
                 '--mount',f'type=volume,src={spool_volume},dst=/volume','--entrypoint','python',backend,'-c',
                 "import os; os.chown('/volume',0,0); os.chmod('/volume',0o700); os.chown('/volume',10001,10001)"])
        for name in (volume,spool_volume):
            info=json.loads(command(['docker','volume','inspect',name]).stdout)[0]
            if info['Driver']!='local' or info.get('Options'):raise ValueError('Ordinary durable local volumes required')
        filesystems={}
        for label,name in (('postgres',volume),('monitoring_outbox',spool_volume)):
            filesystem=command(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
                '--mount',f'type=volume,src={name},dst=/volume,readonly,volume-nocopy','--entrypoint','python',backend,'-c',
                "rows=[r.split() for r in open('/proc/mounts')]; fs=[r[2] for r in rows if r[1]=='/volume']; assert len(fs)==1 and fs[0] not in ('tmpfs','ramfs'); print(fs[0])"]).stdout.strip()
            filesystems[label]=filesystem
        report['volume_filesystems']=filesystems


        uid=command(['docker','run','--rm','--network','none','--entrypoint','id',postgres,'-u','postgres']).stdout.strip()
        command(['docker','run','--rm','--network','none','--read-only','--user','0:0','--cap-drop','ALL','--cap-add','CHOWN','--mount',f'type=volume,src={volume},dst=/var/lib/postgresql/data','--entrypoint','chown',postgres,uid+':'+uid,'/var/lib/postgresql/data'])
        command(['docker','run','-d','--name',database,'--network',network,'--network-alias','db','--user',uid+':'+uid,'--cap-drop','ALL','--security-opt','no-new-privileges',
                 '--cpus','2','--memory','1g','--mount',f'type=volume,src={volume},dst=/var/lib/postgresql/data',
                 '--tmpfs',f'/var/run/postgresql:rw,uid={uid},gid={uid},mode=700,size=16m',
                 '-e','POSTGRES_USER=synthetic','-e','POSTGRES_PASSWORD=synthetic-ci-only','-e','POSTGRES_DB=nwo_noc',postgres])
        owned_database=True
        import time
        for attempt in range(60):
            if command(['docker','exec',database,'pg_isready','-h','127.0.0.1','-U','synthetic','-d','nwo_noc'],check=False).returncode==0:break
            time.sleep(1)
        else: raise RuntimeError('Disposable PostgreSQL unavailable')
        report['postgres_settings']=command(['docker','exec',database,'psql','-U','synthetic','-d','nwo_noc','-A','-t','-c',"SELECT name||'='||setting FROM pg_settings WHERE name IN('fsync','synchronous_commit','shared_buffers','max_connections','full_page_writes') ORDER BY name"]).stdout.splitlines()
        for repeat in range(1,4):
            result=command(['docker','run','--rm','--network',network,'--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
                '--cpus','2','--memory','768m','--tmpfs','/tmp:rw,noexec,nosuid,size=128m',
                '--mount',f'type=volume,src={spool_volume},dst=/var/lib/nwo-noc/monitoring',
                '-e','NOC_BENCHMARK_SPOOL_ROOT=/var/lib/nwo-noc/monitoring',
                '-e','SECRET_KEY=noc119-synthetic-secret-key-noc119-synthetic', '-e','ADMIN_PASSWORD=synthetic-ci-only',
                '-e','DATABASE_URL=postgresql+psycopg://synthetic:synthetic-ci-only@db:5432/nwo_noc',
                '-e','APP_ENV=staging','-e','OFFLINE_MODE=true','-e','SYNTHETIC_MONITORING_ENABLED=true','-e','RF_SCHEDULER_ENABLED=true',
                '--entrypoint','python',backend,'-m','app.monitoring_benchmark'])
            rows=[json.loads(l) for l in result.stdout.splitlines() if l.startswith('{')]
            if [r.get('devices') for r in rows] != [250,500,1000]:raise ValueError('Incomplete benchmark evidence')
            for row in rows:
                if row['samples_and_unique_receipts'] != row['devices']*6 or len(row['rounds']) != 6:raise ValueError('Lost sample/receipt evidence')
                if row.get('spool_parent') != 'explicit_private_volume':raise ValueError('Durable outbox profile was not used')
                records.append({'repeat':repeat,**row})
        samples=[round['elapsed_seconds'] for record in records if record['devices']==1000 for round in record['rounds']]
        report.update(min_seconds=min(samples),median_seconds=statistics.median(samples),max_seconds=max(samples),
                      target_status='PASS' if max(samples)<=15 else 'FAIL', measured_rounds=len(samples))
    finally:
        if owned_database:command(['docker','rm','-f',database],check=False)
        if owned_volume:command(['docker','volume','rm',volume],check=False)
        if owned_spool:command(['docker','volume','rm',spool_volume],check=False)
        if owned_network:command(['docker','network','rm',network],check=False)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report),flush=True)

    return report


def require_acceptance(report):
    """Release gate: recompute the complete original profile, never trust a label."""
    limits = {'backend_cpus':2,'backend_memory_mib':768,'database_cpus':2,
              'database_memory_mib':1024,'polling_threads':8,'pool_size':10}
    if report.get('limits') != limits:
        raise ValueError('Benchmark resource profile changed; evidence retained')
    filesystems = report.get('volume_filesystems', {})
    if set(filesystems) != {'postgres', 'monitoring_outbox'} or any(
            not isinstance(fs, str) or not fs or fs in {'tmpfs', 'ramfs'}
            for fs in filesystems.values()):
        raise ValueError('Both durable volume filesystems must be verified')
    settings = set(report.get('postgres_settings', []))
    if not {'fsync=on','full_page_writes=on','synchronous_commit=on'} <= settings:
        raise ValueError('PostgreSQL durability evidence is incomplete')
    records = report.get('records', [])
    expected = {(repeat, size) for repeat in (1,2,3) for size in (250,500,1000)}
    identities = [(row.get('repeat'), row.get('devices')) for row in records]
    if len(records) != 9 or len(set(identities)) != 9 or set(identities) != expected:
        raise ValueError('All independent invocations and workload sizes required')
    times = []
    for row in records:
        size, rounds = row['devices'], row.get('rounds', [])
        if (row.get('spool_parent') != 'explicit_private_volume'
                or row.get('concurrency') != 8 or row.get('sites') != 40
                or row.get('samples_and_unique_receipts') != size*6
                or len(rounds) != 6 or [r.get('round') for r in rounds] != list(range(1,7))):
            raise ValueError('Incomplete durable sample/receipt/round evidence')
        for result in rounds:
            seconds = result.get('elapsed_seconds')
            if (isinstance(seconds, bool) or not isinstance(seconds, (float, int))
                    or not math.isfinite(seconds) or seconds <= 0
                    or result.get('samples') != size):
                raise ValueError('Invalid timing or missing samples')
            if result.get('phase_profile_inclusive', {}).get('durability.fsync', {}).get('calls', 0) < size*3:
                raise ValueError('File and directory fsync evidence is incomplete')
            if size == 1000:
                times.append(seconds)
    if (len(times) != 18 or report.get('measured_rounds') != 18
            or report.get('target_seconds') != 15 or report.get('target_status') != 'PASS'
            or max(times) > 15):
        raise ValueError('Full durable 1000-device 15-second target failed; complete evidence retained')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',required=True)
    p.add_argument('--postgres',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--require-target',action='store_true',
                   help='Fail after retaining evidence unless every full-durable round meets the unchanged target')
    a=p.parse_args(argv)
    report=measure(a.backend,a.postgres,a.output)
    if a.require_target:
        require_acceptance(report)


if __name__=='__main__':
    main()
