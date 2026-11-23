"""Scoped CI KVM setup; no device/account/permission changes, controller stays unprivileged."""
import grp
import json
import os
from pathlib import Path
import stat
import sys

ROOT=Path(__file__).resolve().parents[2]
PUBLIC_ENV=("GITHUB_ACTIONS","GITHUB_SHA","GITHUB_WORKSPACE","GITHUB_RUN_ID","GITHUB_RUN_ATTEMPT",
            "GITHUB_REPOSITORY","GITHUB_EVENT_NAME","GITHUB_REF","RUNNER_TEMP")


def launch_plan(base, uid, device, accessible, kvm_gid, groups=(), public_env=None):
    command=list(base)
    facts={'device_exists':device is not None,'existing_access':bool(accessible),
           'method':'existing-access' if accessible else 'tcg-no-group-access'}
    if device is not None:
        facts.update(owner_uid=device.st_uid,group_gid=device.st_gid,mode=oct(stat.S_IMODE(device.st_mode)))
    safe=(device is not None and stat.S_ISCHR(device.st_mode)
          and os.major(device.st_rdev)==10 and os.minor(device.st_rdev)==232
          and device.st_uid==0 and type(kvm_gid) is int and kvm_gid>0 and device.st_gid==kvm_gid
          and device.st_mode & 0o060==0o060 and not device.st_mode & 0o002)
    if not accessible and safe and type(uid) is int and uid>0:
        if not all(type(g) is int and g>=0 for g in groups):
            raise ValueError('Invalid original supplementary groups')
        group_option='--groups='+','.join(str(g) for g in sorted(set(groups))) if groups else '--clear-groups'
        public_env=public_env or {}
        values=[key+'='+public_env[key] for key in PUBLIC_ENV if key in public_env]
        command=['/usr/bin/sudo','-n','--','/usr/bin/setpriv',
                 '--reuid='+str(uid),'--regid='+str(kvm_gid),group_option,
                 '--bounding-set=-all','--inh-caps=-all','--ambient-caps=-all',
                 '--no-new-privs','--reset-env','--','/usr/bin/env',*values,
                 'NOC_CI_EXPECTED_UID='+str(uid),'NOC_CI_EXPECTED_GID='+str(kvm_gid),*command]
        facts['method']='root-setup-drop-to-original-uid-kvm-group'
    return command,facts


def verify_controller_identity(env, status, uid, euid, gid, kvm_access):
    if uid<=0 or euid<=0:
        raise RuntimeError('Disposable QEMU controller must not run as root')
    if 'NOC_CI_EXPECTED_UID' not in env and 'NOC_CI_EXPECTED_GID' not in env:
        return {'privilege_drop_requested':False,'uid':uid,'gid':gid}
    try:
        expected_uid=int(env['NOC_CI_EXPECTED_UID']);expected_gid=int(env['NOC_CI_EXPECTED_GID'])
        fields=dict(line.split(':',1) for line in status.splitlines() if ':' in line)
        secure=(expected_uid>0 and expected_gid>0 and uid==euid==expected_uid and gid==expected_gid
                and int(fields['CapEff'].strip(),16)==0 and int(fields['CapBnd'].strip(),16)==0
                and int(fields['NoNewPrivs'].strip())==1 and kvm_access)
    except (ValueError,KeyError,TypeError):
        secure=False
    if not secure:
        raise RuntimeError('Scoped KVM controller identity/capability/access validation failed')
    return {'privilege_drop_requested':True,'uid':uid,'gid':gid,
            'no_new_privileges':True,'capabilities_dropped':True,'kvm_access_verified':True}


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true' or '--execute-disposable-vms' not in sys.argv:
        raise RuntimeError('Explicit disposable GitHub VM execution required')
    if os.getuid()==0:
        raise RuntimeError('Launch the disposable controller as its original unprivileged job user')
    try:device=Path('/dev/kvm').stat()
    except FileNotFoundError:device=None
    try:kvm_gid=grp.getgrnam('kvm').gr_gid
    except KeyError:kvm_gid=None
    command,facts=launch_plan([sys.executable,str(ROOT/'tests/installer/pilot_vms.py'),*sys.argv[1:]],
                             os.getuid(),device,os.access('/dev/kvm',os.R_OK|os.W_OK),kvm_gid,
                             os.getgroups(),dict(os.environ))
    metadata={'hypervisor_access':facts,'global_permissions_changed':False,'service_accounts_changed':False}
    # Separate file: even a privileged setup refusal retains evidence without
    # overwriting the VM harness's fresh evidence-directory guard.
    with os.fdopen(os.open(ROOT/'pilot-hypervisor-access.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as out:
        json.dump(metadata,out)
    print(json.dumps(metadata),flush=True)
    os.execvpe(command[0],command,os.environ)


if __name__=='__main__':main()
