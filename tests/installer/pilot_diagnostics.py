"""Whitelisted, read-only diagnostics for explicitly synthetic disposable hosts."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time


def import_profile(text):
    """Numeric CPython import timings and module identifiers, never log values."""
    rows=[]
    for line in text.splitlines():
        m=re.fullmatch(r'import time:\s+(\d+)\s+\|\s+(\d+)\s+\|\s+([a-zA-Z_][a-zA-Z0-9_.]*)',line)
        if m:rows.append({'self_us':int(m[1]),'cumulative_us':int(m[2]),'module':m[3]})
    return {'last':rows[-12:], 'largest_self':sorted(rows,key=lambda r:r['self_us'],reverse=True)[:15]}


def signals(text):
    lower=text.lower()
    phrases={'permission_denied':'permission denied','illegal_instruction':'illegal instruction',
             'missing_file':'no such file or directory','connection_refused':'connection refused',
             'timed_out':'timed out','authentication_failed':'password authentication failed',
             'startup_complete':'application startup complete','cpu_baseline':'baseline optimizations',
             'unsupported_cpu':"doesn't support",'import_failed':'could not import module',
             'fatal_python':'fatal python error',
             'health_timeout':'health check exceeded timeout','healthcheck_timeout':'healthcheck exceeded timeout',
             'exec_deadline':'context deadline exceeded','proc_not_ready':'procready not received',
             'resource_unavailable':'resource temporarily unavailable',
             'address_in_use':'address already in use','operation_not_permitted':'operation not permitted',
             'sandbox_failed':'failed at step','envfile_failed':'failed to load environment',
             'start_limit':'start request repeated too quickly'}
    allowed={'PermissionError','OperationalError','ImportError','ModuleNotFoundError','ValueError',
             'ValidationError','RuntimeError','OSError','KeyError','FileNotFoundError',
             'AttributeError','TypeError','AssertionError','ProgrammingError','IntegrityError'}
    found=set(re.findall(r'\b([A-Za-z]+(?:Error|Exception)):',text)) & allowed
    return {'flags':{k:v in lower for k,v in phrases.items()},'exception_types':sorted(found)}


def command(args):
    return subprocess.run(args,capture_output=True,text=True,timeout=20)



def unit_properties(text):
    states = {
        'ActiveState': {'active','inactive','failed','activating','deactivating','reloading','maintenance'},
        'SubState': {'running','dead','failed','auto-restart','start-pre','start','start-post','stop','stop-sigterm','stop-sigkill','exited','condition','cleaning'},
        'Result': {'success','exit-code','signal','core-dump','timeout','resources','start-limit-hit','watchdog','protocol','oom-kill'},
    }
    numeric = {'ExecMainCode','ExecMainStatus','MainPID','MemoryCurrent','MemoryPeak','CPUUsageNSec'}
    result = {}
    for line in text.splitlines():
        key, separator, value = line.partition('=')
        if not separator: continue
        if key in states: result[key] = value if value in states[key] else 'unknown'
        elif key in numeric: result[key] = int(value) if value.isdigit() else 'unavailable'
    return result


def ingress_diagnostics():
    properties = ['ActiveState','SubState','Result','ExecMainCode','ExecMainStatus','MainPID','MemoryCurrent','MemoryPeak','CPUUsageNSec']
    show = command(['systemctl','show','nwo-noc-ingress.service',*['--property='+p for p in properties]])
    journal = command(['journalctl','-u','nwo-noc-ingress.service','-n','80','--no-pager','-o','cat'])
    return {'available':show.returncode==0, 'properties':unit_properties(show.stdout),
            'journal_signals':signals(journal.stdout)}


def collect():
    state_path=Path('/var/lib/nwo-noc/installation.json')
    if not state_path.exists():return {'installation_state':'not created'}
    state=json.loads(state_path.read_text())
    if state.get('purpose')!='controlled-test' or not re.fullmatch(r'noc113-[a-f0-9]{12}',state.get('project','')):
        raise ValueError('Only synthetic disposable installation diagnostics permitted')
    ids=command(['docker','ps','-aq','--filter','label=com.docker.compose.project='+state['project']]).stdout.split()
    if not ids:return {'containers':[]}
    result=command(['docker','inspect',*ids])
    if result.returncode:raise RuntimeError('Disposable container inspection unavailable')
    records=[]
    for info in json.loads(result.stdout):
        role=info.get('Config',{}).get('Labels',{}).get('com.docker.compose.service')
        if role not in {'db','redis','backend','worker','planning-worker','frontend','migrate'}:continue
        data=info['State'];health=data.get('Health',{})
        logs=command(['docker','logs','--tail','5000',info['Id']])
        probes=health.get('Log',[])[-3:]
        records.append({'service':role,'status':data.get('Status'),'running':data.get('Running'),
                        'exit_code':data.get('ExitCode'),'oom_killed':data.get('OOMKilled'),
                        'health':health.get('Status'),'health_exit_codes':[p.get('ExitCode') for p in probes],
                        'log_signals':signals(logs.stdout+'\n'+logs.stderr),
                        'health_signals':signals('\n'.join(p.get('Output','') for p in probes))})
        records[-1]['health_seconds']=[round(__import__('datetime').datetime.fromisoformat(p['End'].replace('Z','+00:00')).timestamp()-__import__('datetime').datetime.fromisoformat(p['Start'].replace('Z','+00:00')).timestamp(),3) for p in probes]
        if role=='backend':
            records[-1]['restart_count']=info.get('RestartCount')
            records[-1]['import_profile']=import_profile(logs.stdout+'\n'+logs.stderr)
            records[-1]['health_seconds']=[round(__import__('datetime').datetime.fromisoformat(p['End'].replace('Z','+00:00')).timestamp()-__import__('datetime').datetime.fromisoformat(p['Start'].replace('Z','+00:00')).timestamp(),3) for p in probes]
            top=command(['docker','top',info['Id'],'-eo','pcpu,pmem,nlwp,time,comm'])
            records[-1]['process_resources']=[line.split() for line in top.stdout.splitlines()[1:] if re.fullmatch(r'\s*[\d.]+\s+[\d.]+\s+\d+\s+[\d:]+\s+(python(?:3(?:\.12)?)?|uvicorn)\s*',line)]
            records[-1]['memory_limit_bytes']=info.get('HostConfig',{}).get('Memory')
            events=command(['docker','events','--since',info['Created'],'--until',str(int(time.time())),
                            '--filter','container='+info['Id'],'--format','{{json .}}'])
            exited=[];oom=0
            for line in events.stdout.splitlines():
                try:event=json.loads(line)
                except ValueError:continue
                code=event.get('Actor',{}).get('Attributes',{}).get('exitCode','')
                if event.get('Action')=='die' and str(code).isdigit():exited.append(int(code))
                if event.get('Action')=='oom':oom+=1
            records[-1]['retained_die_exit_codes']=exited[-6:]
            records[-1]['retained_oom_events']=oom
    flags=next((line.split(':',1)[1].split() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('flags')),[])
    kernel=command(['dmesg'])
    return {'installation_state':state.get('status'),'containers':records,
            'private_ingress_unit':ingress_diagnostics(),
            'cpu_features':{name:name in flags for name in ('ssse3','sse4_1','sse4_2','popcnt','cx16','lahf_lm','avx','avx2')},
            'kernel_signals_available':kernel.returncode==0,
            'kernel_failure_flags':{name:token in kernel.stdout.lower() for name,token in
                                    {'invalid_opcode':'invalid opcode','segfault':'segfault at','oom':'out of memory','killed_process':'killed process'}.items()},
            'scope':'No raw logs, environment variables, credentials or application records exported'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--synthetic-disposable-host',action='store_true');a=p.parse_args()
    if not a.synthetic_disposable_host:p.error('Explicit synthetic diagnostics required')
    print(json.dumps(collect()))


def command_failure(stdout, stderr, exit_code):
    """Retain failure identity without raw command output, paths or credentials."""
    import hashlib
    checks=[];last_elapsed=None;authentication_cost=None
    for line in stdout.splitlines():
        try:row=json.loads(line)
        except (ValueError,TypeError):continue
        if isinstance(row,dict) and 'authentication_cost' in row:
            authentication_cost=safe_authentication_cost(row['authentication_cost'])
        if isinstance(row,dict) and row.get('status') in ('PASS','PASSED') and isinstance(row.get('check'),str):
            checks.append(row['check'][:256])
            value=row.get('elapsed_seconds')
            if isinstance(value,(int,float)) and not isinstance(value,bool) and 0<=value<=86400:last_elapsed=value
    allowed={'AssertionError','RuntimeError','ValueError','TimeoutError','HTTPError',
             'URLError','RemoteDisconnected','SSLError','ConnectionResetError',
             'FileNotFoundError','PermissionError','OSError'}
    exceptions=sorted({m.group(1) for m in re.finditer(r'(?m)^(?:[a-zA-Z_][a-zA-Z0-9_]*\.)*([A-Za-z_][A-Za-z0-9_]*):',stderr)
                       if m.group(1) in allowed})
    statuses=sorted({int(m.group(1)) for m in re.finditer(r'HTTP Error ([1-5][0-9]{2})(?:\D|$)',stderr)})
    lower=stderr.lower()
    return {'exit_code':int(exit_code),'completed_check_count':len(checks),'last_completed_elapsed_seconds':last_elapsed,'authentication_cost':authentication_cost,
            'last_check_sha256':hashlib.sha256(checks[-1].encode()).hexdigest() if checks else None,
            'exception_types':exceptions,'http_statuses':statuses,
            'transport_flags':{name:phrase in lower for name,phrase in {
                'broken_pipe':'broken pipe','connection_closed':'connection closed',
                'connection_reset':'connection reset','killed':'killed','timed_out':'timed out'}.items()}}


def safe_authentication_cost(value):
    """Only public policy identifiers, booleans and bounded durations; never hashes."""
    if not isinstance(value,dict):return None
    result={}
    if value.get('scheme')=='bcrypt':result['scheme']='bcrypt'
    for key in ('policy_rounds','hash_rounds'):
        x=value.get(key)
        if type(x) is int and 4<=x<=31:result[key]=x
    for key in ('hash_seconds','verify_seconds','wrong_seconds'):
        x=value.get(key)
        if type(x) in (int,float) and 0<=x<=1800:result[key]=x
    for key in ('correct_password_accepted','incorrect_password_rejected'):
        if type(value.get(key)) is bool:result[key]=value[key]
    return result
