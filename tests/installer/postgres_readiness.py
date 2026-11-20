"""Exercise actual deployment health commands against a temporary PostgreSQL server."""
import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4
import yaml

ROOT = Path(__file__).resolve().parents[2]


def command(args, *, check=True):
    result = subprocess.run([str(v) for v in args], capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError('Disposable PostgreSQL readiness operation failed: ' + str(args[0]))
    return result


def profiles():
    isolated = yaml.safe_load((ROOT/'deploy/compose.isolated.yml').read_text())['services']['db']['healthcheck']['test']
    tree = ast.parse((ROOT/'scripts/host/manager.py').read_text())
    assignments = [n for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(ast.unparse(t)=="svc['healthcheck']['test']" for t in n.targets)]
    if len(assignments)!=1:raise ValueError('Reviewed host database health contract missing')
    host = ast.literal_eval(assignments[0].value)
    if any(not isinstance(v,list) or len(v)!=2 or v[0]!='CMD-SHELL' for v in (isolated,host)):
        raise ValueError('Explicit reviewed PostgreSQL health commands required')
    return {'isolated':isolated[1],'host':host[1]}


def measure(image):
    commands=profiles()
    identity=command(['docker','image','inspect','--format','{{.Id}}',image]).stdout.strip()
    uid=command(['docker','run','--rm','--network','none','--entrypoint','id',identity,'-u','postgres']).stdout.strip()
    gid=command(['docker','run','--rm','--network','none','--entrypoint','id',identity,'-g','postgres']).stdout.strip()
    name='noc119-readiness-'+uuid4().hex[:12]
    owned=False
    try:
        if command(['docker','inspect',name],check=False).returncode==0:raise RuntimeError('Existing test container retained')
        command(['docker','run','-d','--name',name,'--network','none','--read-only','--user',uid+':'+gid,
                 '--cap-drop','ALL','--security-opt','no-new-privileges','--memory','512m',
                 '--tmpfs',f'/var/lib/postgresql/data:rw,uid={uid},gid={gid},mode=700,size=256m',
                 '--tmpfs',f'/var/run/postgresql:rw,uid={uid},gid={gid},mode=700,size=16m',
                 '--tmpfs','/tmp:rw,noexec,nosuid,size=16m',
                 '--mount',f'type=bind,src={ROOT}/tests/installer/postgres-init-pause.sh,dst=/docker-entrypoint-initdb.d/99-readiness.sh,readonly',
                 '-e','POSTGRES_DB=nwo_noc','-e','POSTGRES_PASSWORD=synthetic-ci-only',identity])
        owned=True
        for _ in range(45):
            if command(['docker','exec',name,'test','-f','/tmp/noc119-initializing'],check=False).returncode==0:break
            time.sleep(1)
        else:raise RuntimeError('Temporary PostgreSQL initialization server unavailable')
        assert command(['docker','exec',name,'pg_isready','-U','postgres','-d','nwo_noc'],check=False).returncode==0, 'Negative control did not expose temporary Unix server'
        for profile,script in commands.items():
            assert command(['docker','exec',name,'sh','-c',script],check=False).returncode!=0, profile+' falsely accepted temporary initialization server'
        command(['docker','exec',name,'touch','/tmp/noc119-initialization-release'])
        for _ in range(45):
            if command(['docker','exec',name,'pg_isready','-h','127.0.0.1','-U','postgres','-d','nwo_noc'],check=False).returncode==0:break
            time.sleep(1)
        else:raise RuntimeError('Final PostgreSQL TCP server unavailable')
        for script in commands.values():command(['docker','exec',name,'sh','-c',script])
        print(json.dumps({'image_id':identity,'temporary_unix_server_observed':True,'profiles_reject_initialization':list(commands),'profiles_accept_final_server':list(commands),'network':'none','data':'synthetic tmpfs only','checks_passed':5}),flush=True)
    finally:
        if owned:command(['docker','rm','-f',name],check=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--postgres',required=True);p.add_argument('--execute-disposable-test',action='store_true');a=p.parse_args()
    if os.environ.get('GITHUB_ACTIONS')!='true' or not a.execute_disposable_test:raise RuntimeError('Explicit disposable GitHub CI test required')
    measure(a.postgres)
