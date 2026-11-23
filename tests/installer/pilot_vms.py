"""Two independent disposable Ubuntu kernels/disks; never run against an existing host."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import tarfile
import time
from cpu_contract import CPU_MODEL, V2, model_contract

ROOT = Path(__file__).resolve().parents[2]


def run(argv, *, data=None, output=None, timeout=1800, check=True):
    p = subprocess.run([str(v) for v in argv], input=data, stdout=output or subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    if check and p.returncode:
        from pilot_diagnostics import command_failure
        identity=command_failure((p.stdout or b'').decode(errors='replace'),
                                 (p.stderr or b'').decode(errors='replace'),p.returncode)
        raise RuntimeError('Disposable VM command failed: '+str(argv[0])+' / '+json.dumps(identity))
    return p


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024**2),b''): h.update(b)
    return h.hexdigest()


class VM:
    def __init__(self, work, name, port, image, key, cpu_model=CPU_MODEL):
        self.root = work / name; self.root.mkdir(mode=0o700)
        self.port, self.key, self.process = port, key, None
        host_key = self.root / 'host-key'
        run(['ssh-keygen','-q','-t','ed25519','-N','','-f',host_key])
        pub = (Path(str(host_key)+'.pub')).read_text().strip()
        self.known = self.root / 'known-hosts'; self.known.write_text(f'[127.0.0.1]:{port} {pub}\n')
        user = '#cloud-config\n' + json.dumps({
            'hostname':name,'disable_root':True,'ssh_pwauth':False,
            'ssh_keys':{'ed25519_private':host_key.read_text(),'ed25519_public':pub},
            'users':[{'name':'nocpilot','groups':['sudo'],'shell':'/bin/bash','sudo':'ALL=(ALL) NOPASSWD:ALL','lock_passwd':True,'ssh_authorized_keys':[Path(str(key)+'.pub').read_text().strip()]}],
            'write_files':[{'path':'/etc/ssh/sshd_config.d/00-noc119.conf','permissions':'0600','content':'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\nAllowUsers nocpilot\n'}],
            'runcmd':[['systemctl','restart','ssh']]})
        (self.root/'user-data').write_text(user)
        (self.root/'meta-data').write_text(json.dumps({'instance-id':name,'local-hostname':name}))
        run(['cloud-localds',self.root/'seed.img',self.root/'user-data',self.root/'meta-data'])
        run(['qemu-img','create','-f','qcow2','-F','qcow2','-b',image,self.root/'disk.qcow2','32G'])
        kvm = os.access('/dev/kvm',os.R_OK|os.W_OK)
        self.acceleration = 'kvm' if kvm else 'tcg'
        self.serial = (self.root/'serial.log').open('wb')
        args=['qemu-system-x86_64','-accel',self.acceleration,'-cpu',cpu_model,'-m','5120','-smp','4','-nographic',
              '-serial',f'file:{self.root}/serial.log','-monitor','none','-drive',f'file={self.root}/disk.qcow2,format=qcow2,if=virtio',
              '-drive',f'file={self.root}/seed.img,format=raw,if=virtio',
              '-netdev',f'user,id=noc,hostfwd=tcp:127.0.0.1:{port}-:22','-device','virtio-net-pci,netdev=noc']
        started=time.monotonic()
        self.qemu_args=args
        self.process=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=self.serial)
        try:
            self.wait()
            self.ssh('cloud-init status --wait', timeout=900)
            self.boot_seconds=round(time.monotonic()-started,3)
        except Exception:
            self.stop()
            raise

    def argv(self):
        return ['ssh','-i',str(self.key),'-p',str(self.port),'-o','BatchMode=yes','-o','ConnectTimeout=3',
                '-o','StrictHostKeyChecking=yes','-o',f'UserKnownHostsFile={self.known}','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=6','nocpilot@127.0.0.1']

    def ssh(self, script, *, timeout=2400, output=None, check=True):
        try:
            result=run([*self.argv(),'bash -se'],data=script.encode(),output=output,timeout=timeout,check=False)
        except subprocess.TimeoutExpired as error:
            from pilot_diagnostics import command_failure
            identity=command_failure((error.output or b'').decode(errors='replace'),
                                     (error.stderr or b'').decode(errors='replace'),124)
            identity['whole_command_deadline_seconds']=timeout
            raise RuntimeError('Independent guest whole-command deadline: '+json.dumps(identity)) from None
        if check and result.returncode:
            safe=[line for line in (result.stderr or b'').decode(errors='replace').splitlines() if line.startswith(('NOC packaging','RuntimeError:','AssertionError:','ssh:'))]
            from pilot_diagnostics import command_failure
            identity=command_failure((result.stdout or b'').decode(errors='replace'),
                                     (result.stderr or b'').decode(errors='replace'),result.returncode)
            raise RuntimeError('Independent guest command failed: '+json.dumps(identity)+ '; '+ '; '.join(safe[-3:]))
        return result

    def wait(self):
        deadline=time.monotonic()+600
        last_error='No SSH connection yet'
        while time.monotonic()<deadline:
            if self.process.poll() is not None: raise RuntimeError('VM exited before SSH readiness')
            try:
                result=run([*self.argv(),'true'],timeout=8,check=False)
                if result.returncode==0:return
                last_error=result.stderr.decode(errors='replace').strip()[-240:]
            except subprocess.TimeoutExpired:
                # SLIRP can accept TCP before the guest kernel/sshd is ready.
                # Retry only within the fixed boot deadline; never weaken host-key checks.
                last_error='Individual SSH transport timeout before guest readiness'
            time.sleep(3)
        raise RuntimeError('Independent VM boot/SSH readiness blocked: '+last_error)

    def copy(self, path, destination):
        run(['scp','-i',self.key,'-P',str(self.port),'-o','StrictHostKeyChecking=yes','-o',f'UserKnownHostsFile={self.known}',path,f'nocpilot@127.0.0.1:{destination}'],timeout=1200)

    def change_cpu_before_install(self, model):
        # A/B the native ABI on identical source-VM disk/image bytes. No NOC
        # installation exists yet; receiver recovery still uses a separate disk.
        before=self.ssh('cat /proc/sys/kernel/random/boot_id').stdout.strip()
        self.stop()
        args=list(self.qemu_args)
        args[args.index('-cpu')+1]=model
        serial=self.root/'serial-compatible-cpu.log'
        args[args.index('-serial')+1]='file:'+str(serial)
        self.serial=serial.open('ab')
        self.process=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=self.serial)
        self.qemu_args=args
        self.wait()
        self.ssh('cloud-init status --wait',timeout=300)
        if self.ssh('cat /proc/sys/kernel/random/boot_id').stdout.strip()==before:
            raise RuntimeError('CPU compatibility reboot not observed')
        return {'kernel_boot_changed':True,'cpu_model':model,'noc_installed_before_change':False}

    def reboot(self):
        started=time.monotonic()
        before=self.ssh('cat /proc/sys/kernel/random/boot_id').stdout.strip()
        self.ssh('sudo systemctl reboot',check=False)
        time.sleep(8); self.wait()
        after=self.ssh('cat /proc/sys/kernel/random/boot_id').stdout.strip()
        if before==after: raise RuntimeError('Guest kernel did not reboot')
        # SSH can become ready before Docker and the automatically enabled NOC
        # units. Observe their bounded readiness; never start them manually to
        # manufacture a successful unattended reboot test.
        self.ssh("sudo timeout 300 sh -c 'until systemctl is-active --quiet nwo-noc.service && systemctl is-active --quiet nwo-noc-ingress.service; do sleep 2; done'",timeout=330)
        self.ssh('sudo /opt/nwo-noc/current/verify.sh --controlled-test',timeout=300)
        return {'kernel_boot_changed':True,'post_reboot_verification':'PASS','reboot_and_verification_seconds':round(time.monotonic()-started,3)}

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.serial.close()


def provision(vm, source, images, redis):
    vm.copy(source,'/home/nocpilot/source.bundle'); vm.copy(images,'/home/nocpilot/images.tar')
    return vm.ssh('''sudo apt-get -o APT::Update::Error-Mode=any update
sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get upgrade -y --no-install-recommends
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker.io docker-compose-v2 docker-buildx python3 python3-yaml git age openssl ca-certificates logrotate ufw
sudo systemctl start docker
sudo docker load -i /home/nocpilot/images.tar >/dev/null
git clone --quiet /home/nocpilot/source.bundle /home/nocpilot/source
git -C /home/nocpilot/source fetch --quiet /home/nocpilot/source.bundle '+refs/*:refs/noc119-audit/*'
'''+f'sudo docker pull {shlex.quote(redis)} >/dev/null\n'+'''
python3 -c 'import socket; socket.create_connection(("10.0.2.2",22229),3).close(); print("controlled-egress-positive: PASS")'
sudo ufw default deny incoming
sudo ufw default deny outgoing
sudo ufw allow in from 10.0.2.2 to any port 22 proto tcp
sudo ufw allow out proto udp to any port 67
sudo ufw allow in proto udp from 10.0.2.2 port 67 to any port 68
sudo ufw --force enable
python3 - <<'PY'
import socket
try:
 socket.create_connection(("10.0.2.2",22229),2).close()
except OSError: print("controlled-egress-denied: PASS")
else: raise AssertionError("Default-deny outbound rule did not block controlled probe")
PY
sudo ufw status verbose
sudo sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin) '
''',timeout=1800)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',type=Path,required=True);p.add_argument('--execute-disposable-vms',action='store_true');a=p.parse_args()
    if not a.execute_disposable_vms or os.environ.get('GITHUB_ACTIONS')!='true':
        raise RuntimeError('Explicit disposable GitHub CI execution required; never target an existing server')
    work=a.work.resolve();work.mkdir(mode=0o700,parents=True,exist_ok=False)
    evidence=ROOT/'pilot-vm-evidence';evidence.mkdir(exist_ok=False)
    report={'decision':'NO-GO','installation':'NOT TESTED','recovery':'NOT TESTED','real_vpn_mfa':'BLOCKED','offsite_independent_failure_domain':'BLOCKED'}
    def phase(name):
        report['phase']=name
        print(json.dumps({'phase':name,'scope':'disposable independent VM acceptance'}),flush=True)
    guests=[];egress=None
    try:
        phase('verify-unprivileged-controller')
        from hypervisor_launch import verify_controller_identity
        report['controller_identity']=verify_controller_identity(
            os.environ,Path('/proc/self/status').read_text(),os.getuid(),os.geteuid(),os.getgid(),
            os.access('/dev/kvm',os.R_OK|os.W_OK))
        phase('verify-pinned-ubuntu-image')
        report['cpu_model_contract']=model_contract()
        egress=socket.socket();egress.bind(('127.0.0.1',22229));egress.listen(8)
        image=json.loads((ROOT/'deploy/pilot-vm-image.json').read_text())
        run(['curl','--fail','--location','--proto','=https','--tlsv1.2','--retry','3','--max-time','900','-o',work/'ubuntu.img',image['url']],timeout=1000)
        if sha(work/'ubuntu.img')!=image['sha256']:raise ValueError('Canonical Ubuntu image digest mismatch; no fallback')
        report['ubuntu_image']=image;report['guest_resources']={'vcpus':4,'memory_mib':5120,'disk_gib':32}
        report['hypervisor']={'qemu':run(['qemu-system-x86_64','--version']).stdout.decode().splitlines()[0], 'host_cpus':os.cpu_count(), 'host_memory':next(line for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:'))}
        key=work/'controller-key';run(['ssh-keygen','-q','-t','ed25519','-N','','-f',key])
        # The pinned integration audit verifies PR86's non-ancestor original against
        # its reviewed replay. HEAD-only bundles omit that required side history.
        source=work/'source.bundle';run(['git','bundle','create',source,'--all'])
        redis=json.loads((ROOT/'deploy/image-lock.json').read_text())['images']['redis:7-alpine']
        run(['docker','pull',redis])
        images=work/'images.tar'
        redis_id=run(['docker','image','inspect','--format','{{.Id}}',redis]).stdout.decode().strip()
        with images.open('xb') as out:run(['docker','save','noc113-backend:base','noc113-frontend:base','noc117-postgres:rc',redis_id],output=out)
        phase('source-kernel-boot-and-pinned-ssh')
        report['acceleration']='kvm' if os.access('/dev/kvm',os.R_OK|os.W_OK) else 'tcg'
        sender=VM(work,'noc119-source',22221,work/'ubuntu.img',key,cpu_model='qemu64');guests.append(sender)
        report['acceleration']=sender.acceleration
        report['source_boot_and_cloudinit_seconds']=sender.boot_seconds
        cpu_script="import json; f=next(line.split(':',1)[1].split() for line in open('/proc/cpuinfo') if line.startswith('flags')); print(json.dumps({k:k in f for k in "+repr(list(V2))+"}))"
        report['source_cpu_features']=json.loads(sender.ssh('python3 -c '+shlex.quote(cpu_script)).stdout)
        phase('source-supported-packages-and-private-firewall')
        (evidence/'source-provision.log').write_bytes(provision(sender,source,images,redis).stdout)
        probe_command='sudo python3 /home/nocpilot/source/tests/installer/native_probe.py --execute-disposable-host'
        phase('source-baseline-native-imports')
        report['baseline_native_imports']=json.loads(sender.ssh(probe_command,timeout=140).stdout)
        phase('source-compatible-cpu-kernel-boot')
        report['cpu_compatibility_reboot']=sender.change_cpu_before_install(CPU_MODEL)
        report['corrected_cpu_features']=json.loads(sender.ssh('python3 -c '+shlex.quote(cpu_script)).stdout)
        if not all(report['corrected_cpu_features'].values()):raise AssertionError('Guest x86-64-v2 ABI unavailable')
        phase('source-exact-image-native-imports')
        report['source_native_imports']=json.loads(sender.ssh(probe_command,timeout=140).stdout)
        if report['baseline_native_imports']['image_id']!=report['source_native_imports']['image_id']:
            raise AssertionError('Native A/B images differ')
        if not report['source_native_imports']['all_passed']:
            raise AssertionError('Supported-CPU native imports failed; no installation acceptance')
        phase('source-installer-upgrade-and-isolated-restore')
        result=sender.ssh('cd /home/nocpilot/source\nsudo python3 tests/installer/test_ubuntu_host.py --workspace /var/tmp/noc119-acceptance --extended --pilot-checkpoint',timeout=4800 if report['acceleration']=='tcg' else 2400)
        (evidence/'source-install.log').write_bytes(result.stdout)
        report['installation']='PASS';phase('source-actual-kernel-reboot');report['source_reboot']=sender.reboot()
        fingerprint=sender.ssh('sudo cat /var/tmp/noc119-acceptance/independent-trust-fingerprint').stdout.decode().strip()
        report['test_public_key_spki_sha256']=fingerprint
        transfer=work/'recovery-private.tar'
        with transfer.open('xb') as out:
            transfer.chmod(0o600)
            sender.ssh('sudo tar -C /var/tmp/noc119-acceptance/recovery-export -cf - .',output=out)
        sender.stop()
        report['source_powered_off_during_restore']=True
        phase('scan-exact-independent-installed-images')
        # A separate runner's rebuild is not evidence for these actual guest bytes.
        from pilot_recovery import unpack_transport,validate
        transported=work/'security-transport'
        unpack_transport(transfer,transported)
        attestation=validate(transported,fingerprint)
        identities=sorted(set(attestation['tested_runtime_image_ids']+[report['source_native_imports']['image_id']]))
        if not identities:raise AssertionError('Independent runtime scan identities missing')
        run(['docker','load','-i',transported/'images.tar'])
        scans=evidence/'runtime-security'
        run(['python3',ROOT/'scripts/release/image_security.py','--require-no-high','--output',scans,*identities],timeout=1800)
        run(['python3',ROOT/'scripts/release/image_sbom.py',scans],timeout=600)
        run(['python3',ROOT/'scripts/release/build_provenance.py',scans])
        report['exact_installed_image_security']=json.loads((scans/'summary.json').read_text())
        phase('independent-receiver-kernel-boot')
        receiver=VM(work,'noc119-recovery',22222,work/'ubuntu.img',key);guests.append(receiver)
        report['receiver_boot_and_cloudinit_seconds']=receiver.boot_seconds
        phase('receiver-supported-packages-and-private-firewall')
        (evidence/'receiver-provision.log').write_bytes(provision(receiver,source,images,redis).stdout)
        receiver.copy(transfer,'/home/nocpilot/recovery-private.tar')
        receiver.ssh('cd /home/nocpilot/source\nsudo python3 tests/installer/pilot_recovery.py --folder /var/tmp/noc119-recovery --unpack /home/nocpilot/recovery-private.tar\nsudo docker load -i /var/tmp/noc119-recovery/images.tar >/dev/null')
        phase('independent-signed-backup-recovery')
        result=receiver.ssh('cd /home/nocpilot/source\nsudo python3 tests/installer/pilot_recovery.py --folder /var/tmp/noc119-recovery --expected-fingerprint '+shlex.quote(fingerprint),timeout=1800)
        (evidence/'independent-recovery.log').write_bytes(result.stdout)
        report['recovery']='PASS';phase('receiver-actual-kernel-reboot');report['receiver_reboot']=receiver.reboot()
        report['host_security_evidence']=receiver.ssh("sudo ufw status verbose; sudo sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin) ';timedatectl show -p NTPSynchronized;uname -a;systemd --version | head -n 1").stdout.decode()
        required=('Default: deny (incoming), deny (outgoing)','passwordauthentication no','kbdinteractiveauthentication no','permitrootlogin no')
        if not all(value in report['host_security_evidence'] for value in required):raise AssertionError('Guest firewall or key-only administration evidence missing')
        report['firewall_scope']='Guest default deny in/out; SSH only through loopback-forwarded CI control channel; Docker application network internal; not a real VPN/MFA acceptance'
    except Exception as exc:
        report['failure_type']=type(exc).__name__;report['failure']=str(exc)[:500] if isinstance(exc,(ValueError,RuntimeError)) else 'VM validation incomplete; inspect private CI logs, no test converted to PASS'
        report['sanitized_service_diagnostics']=[]
        for guest in guests:
            if guest.process.poll() is None:
                try:
                    result=guest.ssh('sudo python3 /home/nocpilot/source/tests/installer/pilot_diagnostics.py --synthetic-disposable-host',timeout=90,check=False)
                    if result.returncode==0:report['sanitized_service_diagnostics'].append(json.loads(result.stdout))
                except Exception:
                    report['diagnostics_unavailable']=True
        diagnostics=[]
        for path in sorted(work.glob('noc119-*/serial*.log')):
            if path.exists():
                diagnostics += [line[-240:] for line in path.read_text(errors='replace').splitlines() if any(token in line for token in ('Linux version','Kernel panic','No bootable device','Failed to start')) or line.startswith('qemu-system-x86_64:')]
        report['sanitized_boot_diagnostics']=diagnostics[-12:]
        raise
    finally:
        for guest in guests:guest.stop()
        if egress:egress.close()
        (evidence/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report),flush=True)


if __name__=='__main__':main()
