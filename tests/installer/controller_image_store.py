"""Align an empty disposable CI controller with OCI image IDs; never a host installer."""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile


def daemon_configuration(value):
    if not isinstance(value,dict):raise ValueError('Docker configuration must be an object')
    result=copy.deepcopy(value)
    features=result.setdefault('features',{})
    if not isinstance(features,dict):raise ValueError('Docker features must be an object')
    features['containerd-snapshotter']=True
    return result


def require_empty_controller(environment,uid,running):
    if environment.get('GITHUB_ACTIONS')!='true' or not environment.get('GITHUB_RUN_ID','').isdigit() or uid!=0:
        raise RuntimeError('Explicit root setup on disposable GitHub runner required')
    original=environment.get('SUDO_UID','')
    if not original.isdigit() or int(original)==0 or running:
        raise RuntimeError('Original unprivileged caller and empty controller required; no existing services stopped')


def operation(argv):
    result=subprocess.run(argv,capture_output=True,timeout=90,check=False)
    if result.returncode:raise RuntimeError('Disposable controller setup command failed: '+argv[0])
    return result.stdout.decode().strip()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute-disposable-ci',action='store_true');args=parser.parse_args()
    if not args.execute_disposable_ci:parser.error('Explicit disposable CI setup required')
    running=operation(['docker','ps','--quiet'])
    require_empty_controller(os.environ,os.geteuid(),running)
    before=operation(['docker','version','--format','{{.Server.Version}}'])
    if int(before.split('.')[0])<28:raise RuntimeError('Supported Docker28+ controller required')
    path=Path('/etc/docker/daemon.json')
    if path.is_symlink() or (path.exists() and (path.stat().st_uid!=0 or path.stat().st_size>65536)):
        raise RuntimeError('Unsafe Docker configuration; no changes')
    raw=path.read_bytes() if path.exists() else b'{}'
    original=json.loads(raw);updated=daemon_configuration(original)
    changed=updated!=original
    if changed:
        backup=Path(tempfile.mkdtemp(prefix='noc119-controller-',dir='/var/tmp'))
        backup.chmod(0o700);saved=backup/'daemon-before.json';saved.write_bytes(raw);saved.chmod(0o600)
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.noc119-',delete=False) as target:
            staged=Path(target.name);os.chmod(staged,0o600);target.write(json.dumps(updated).encode())
        operation(['dockerd','--validate','--config-file',str(staged)])
        require_empty_controller(os.environ,os.geteuid(),operation(['docker','ps','--quiet']))
        os.replace(staged,path)
        operation(['systemctl','restart','docker'])
    status=json.loads(operation(['docker','info','--format','{{json .DriverStatus}}']))
    if not any('io.containerd.snapshotter.v1' in str(row) for row in status):
        raise RuntimeError('OCI snapshotter unavailable; no classic-store fallback')
    result={'scope':'empty disposable CI controller only','docker_before':before,
            'docker_after':operation(['docker','version','--format','{{.Server.Version}}']),
            'containerd_snapshotter':True,'configuration_changed':changed,'original_configuration_retained':True,
            'other_configuration_fields_preserved':True,'running_containers_before':0,
            'existing_images_deleted':False,'production_authorization':False}
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
