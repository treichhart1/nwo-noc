"""Whitelisted, read-only diagnostics for explicitly synthetic disposable hosts."""
import argparse
import json
from pathlib import Path
import re
import subprocess


def signals(text):
    lower=text.lower()
    phrases={'permission_denied':'permission denied','illegal_instruction':'illegal instruction',
             'missing_file':'no such file or directory','connection_refused':'connection refused',
             'timed_out':'timed out','authentication_failed':'password authentication failed',
             'startup_complete':'application startup complete','cpu_baseline':'baseline optimizations',
             'unsupported_cpu':"doesn't support",'import_failed':'could not import module',
             'fatal_python':'fatal python error'}
    allowed={'PermissionError','OperationalError','ImportError','ModuleNotFoundError','ValueError',
             'ValidationError','RuntimeError','OSError','KeyError','FileNotFoundError',
             'AttributeError','TypeError','AssertionError','ProgrammingError','IntegrityError'}
    found=set(re.findall(r'\b([A-Za-z]+(?:Error|Exception)):',text)) & allowed
    return {'flags':{k:v in lower for k,v in phrases.items()},'exception_types':sorted(found)}


def command(args):
    return subprocess.run(args,capture_output=True,text=True,timeout=20)


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
        logs=command(['docker','logs','--tail','100',info['Id']])
        probes=health.get('Log',[])[-3:]
        records.append({'service':role,'status':data.get('Status'),'running':data.get('Running'),
                        'exit_code':data.get('ExitCode'),'oom_killed':data.get('OOMKilled'),
                        'health':health.get('Status'),'health_exit_codes':[p.get('ExitCode') for p in probes],
                        'log_signals':signals(logs.stdout+'\n'+logs.stderr),
                        'health_signals':signals('\n'.join(p.get('Output','') for p in probes))})
    return {'installation_state':state.get('status'),'containers':records,
            'scope':'No raw logs, environment variables, credentials or application records exported'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--synthetic-disposable-host',action='store_true');a=p.parse_args()
    if not a.synthetic_disposable_host:p.error('Explicit synthetic diagnostics required')
    print(json.dumps(collect()))
