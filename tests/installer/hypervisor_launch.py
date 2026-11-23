"""Use existing CI KVM access without chmod, chown, usermod or service changes."""
import grp
import json
import os
from pathlib import Path
import stat
import sys

ROOT=Path(__file__).resolve().parents[2]
PRESERVE_ENV="GITHUB_ACTIONS,GITHUB_SHA,GITHUB_WORKSPACE,GITHUB_RUN_ID,GITHUB_RUN_ATTEMPT,GITHUB_REPOSITORY,GITHUB_EVENT_NAME,GITHUB_REF,RUNNER_TEMP"


def launch_plan(base, uid, device, accessible, kvm_gid):
    command=list(base)
    facts={'device_exists':device is not None,'existing_access':bool(accessible),
           'method':'existing-access' if accessible else 'tcg-no-group-access'}
    if device is not None:
        facts.update(owner_uid=device.st_uid,group_gid=device.st_gid,mode=oct(stat.S_IMODE(device.st_mode)))
    safe=(device is not None and stat.S_ISCHR(device.st_mode)
          and os.major(device.st_rdev)==10 and os.minor(device.st_rdev)==232
          and device.st_uid==0 and kvm_gid is not None and device.st_gid==kvm_gid
          and device.st_mode & 0o060==0o060 and not device.st_mode & 0o002)
    if not accessible and safe and type(uid) is int and uid>0:
        command=['/usr/bin/sudo','-n','-P','-u','#'+str(uid),'-g','kvm',
                 '--preserve-env='+PRESERVE_ENV,'--',*command]
        facts['method']='temporary-existing-kvm-process-group'
    return command,facts


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true' or '--execute-disposable-vms' not in sys.argv:
        raise RuntimeError('Explicit disposable GitHub VM execution required')
    try:device=Path('/dev/kvm').stat()
    except FileNotFoundError:device=None
    try:kvm_gid=grp.getgrnam('kvm').gr_gid
    except KeyError:kvm_gid=None
    command,facts=launch_plan([sys.executable,str(ROOT/'tests/installer/pilot_vms.py'),*sys.argv[1:]],
                             os.getuid(),device,os.access('/dev/kvm',os.R_OK|os.W_OK),kvm_gid)
    print(json.dumps({'hypervisor_access':facts,'global_permissions_changed':False,
                      'service_accounts_changed':False}),flush=True)
    os.execvpe(command[0],command,os.environ)


if __name__=='__main__':main()
