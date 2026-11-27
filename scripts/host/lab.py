#!/usr/bin/env python3
"""Purpose-separated guided lab preparation; never a production release approval."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE / 'scripts/host'))
from policy import Reject, private_file, LOCAL, SHA, REPOSITORY
import preflight

ROLES = ('backend', 'frontend', 'postgres', 'redis')
LOCKS = ('backend/requirements.lock', 'backend/requirements-test.lock', 'backend/requirements-audit.lock', 'frontend/package-lock.json', 'deploy/image-lock.json')
CONTROLS = ('isolated_lab_authorized', 'private_management_reviewed', 'native_risks_reviewed', 'backup_custody_reviewed')
DOCKER = ['docker', '--host=unix:///var/run/docker.sock']


def environment():
    values=dict(os.environ)
    for key in ('DOCKER_CONTEXT','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):
        values.pop(key,None)
    values['DOCKER_HOST']='unix:///var/run/docker.sock'
    values['PATH']='/usr/sbin:/usr/bin:/sbin:/bin'
    return values


def output(argv):
    p = subprocess.run([str(x) for x in argv], cwd=SOURCE, capture_output=True, timeout=30, env=environment())
    if p.returncode:
        raise Reject('Required local command failed; inspect protected evidence')
    return p.stdout.decode().strip()


def git(*args):
    return output(['git', '-c', f'safe.directory={SOURCE}', '-C', SOURCE, *args])


def source_check(commit, tree):
    if not SHA.fullmatch(commit or '') or not SHA.fullmatch(tree or ''):
        raise Reject('Explicit reviewed 40-character source commit and tree required')
    if git('rev-parse', 'HEAD') != commit or git('rev-parse', 'HEAD^{tree}') != tree or git('status', '--porcelain', '--untracked-files=all'):
        raise Reject('Exact reviewed clean source checkout required; preserve unfinished work')
    git('merge-base', '--is-ancestor', '8999b6c26c8c0790df2159a2c2587fa32b00a1d2', commit)


def existing_install():
    if any(p.exists() or p.is_symlink() for p in map(Path, ('/opt/nwo-noc', '/etc/nwo-noc', '/var/lib/nwo-noc'))):
        raise Reject('Existing NOC paths detected; fresh lab installation refuses overwrite')


def protected_work(path, *, new=False):
    p = Path(path)
    if not p.is_absolute() or '..' in p.parts or p.resolve().is_relative_to(SOURCE):
        raise Reject('Absolute protected work directory outside source required')
    for parent in [p.parent, *p.parents]:
        if parent.is_symlink():
            raise Reject('Symlink work path denied')
    st = p.parent.stat()
    if st.st_uid != 0 or st.st_mode & 0o022:
        raise Reject('Root-owned non-writable work parent required')
    if new:
        if p.exists() or p.is_symlink():
            raise Reject('Preparation path already exists; preserve previous/failed evidence')
    else:
        st = p.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o700:
            raise Reject('Private root-owned0700 preparation directory required')
    return p


def write(path, value):
    raw = (json.dumps(value, indent=2) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())


def digest(path):
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > 128 * 1024**2:
            raise Reject('Invalid/oversized evidence file')
        for block in iter(lambda: f.read(1024**2), b''):
            h.update(block)
    return h.hexdigest()


def authorization(args):
    if args.authorization_file:
        a = json.loads(private_file(args.authorization_file))
    else:
        with open('/dev/tty', 'r+') as tty:
            def ask(text):
                tty.write(text + ': '); tty.flush(); return tty.readline().strip()
            a = {'purpose': 'controlled-test', 'commit': args.commit, 'tree': args.tree,
                 'reference': ask('Independent isolated-lab authorization reference'),
                 'ssh_host': ask('Approved private SSH/VPN hostname (no user or URL)'),
                 'backup_destination': ask('Approved independent encrypted backup destination description (no credentials)'),
                 'backup_recipient': ask('Public age backup recipient; private identity must remain in independent custody')}
            for key in CONTROLS:
                a[key] = ask('Confirm ' + key + ' by typing YES') == 'YES'
            a['confirmation'] = ask('Type PREPARE ISOLATED LAB or INSTALL ISOLATED LAB for this operation')
    if not isinstance(a,dict) or set(a)!={'purpose','commit','tree','reference','confirmation','ssh_host','backup_destination','backup_recipient',*CONTROLS}:
        raise Reject('Exact nonsecret authorization fields required; no passwords/private keys or extra metadata')
    if a.get('purpose') != 'controlled-test' or a.get('commit') != args.commit or a.get('tree') != args.tree:
        raise Reject('Lab authorization purpose/source mismatch; production mode never selected')
    if not all(a.get(k) is True for k in CONTROLS):
        raise Reject('Explicit isolated-lab, private access, risk and backup reviews required')
    if a.get('confirmation') != ('PREPARE ISOLATED LAB' if args.action == 'prepare' else 'INSTALL ISOLATED LAB'):
        raise Reject('Explicit confirmation for this operation required')
    if not re.fullmatch(r'[A-Za-z0-9_.:/ -]{3,160}', a.get('reference', '')) or 'REPLACE' in a['reference']:
        raise Reject('Valid independent authorization reference required')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', a.get('ssh_host', '')):
        raise Reject('Approved hostname only; no command options, users, URLs or shell syntax')
    if not re.fullmatch(r'age1[0-9a-z]{58}', a.get('backup_recipient', '')):
        raise Reject('Public age X25519 recipient required; private identities are forbidden')
    if not isinstance(a.get('backup_destination'), str) or not 3 <= len(a['backup_destination']) <= 160 or any(ord(c) < 32 for c in a['backup_destination']):
        raise Reject('Nonsecret approved backup destination description required')
    return a


def dependencies():
    if sys.version_info[:2] != (3,12):raise Reject('Supported Python3.12 host runtime required')
    for name in ('git', 'docker', 'openssl', 'age', 'python3'):
        if not shutil.which(name):
            raise Reject('Missing prerequisite: ' + name + '; no packages installed automatically')
    try:
        import yaml  # existing installer dependency
    except ImportError:
        raise Reject('Supported python3-yaml required') from None


def run(argv, work, label, *, stdout=None):
    # No secrets in argv; scanner/build output remains in private work, never echoed.
    with (work / (label + '.log')).open('xb') as log:
        os.chmod(log.name, 0o600)
        p = subprocess.run([str(x) for x in argv], cwd=SOURCE, stdout=stdout or log, stderr=log, timeout=1800, env=environment())
    if p.returncode:
        raise Reject('Lab step failed: ' + label + '; protected evidence retained; no cleanup performed')


def audit_json(path):
    doc = json.loads(path.read_bytes())
    if not isinstance(doc,dict):raise Reject('Audit object required')
    if path.name == 'pip-audit.json':
        if not doc.get('dependencies') or any(d.get('vulns') != [] for d in doc['dependencies']):
            raise Reject('Python dependency audit missing or vulnerable')
    else:
        counts = doc.get('metadata', {}).get('vulnerabilities', {})
        if set(counts) != {'info', 'low', 'moderate', 'high', 'critical', 'total'} or any(type(n) is not int or n != 0 for n in counts.values()):
            raise Reject('Complete npm audit including development dependencies must be clean')


def prepare(args, a):
    existing_install(); dependencies()
    work = protected_work(args.work, new=True); work.mkdir(mode=0o700)
    write(work/'authorization.json', a)
    images = {}
    for role in ROLES:
        tag = 'noc123-lab-' + role
        context = SOURCE / role if role in ('backend', 'frontend') else SOURCE
        command = [*DOCKER, 'build', '--label', 'org.opencontainers.image.revision='+args.commit, '-t', tag]
        if role in ('postgres', 'redis'):
            command += ['-f', SOURCE/'deploy'/('Dockerfile.'+role)]
        run([*command, context], work, 'build-'+role)
        images[role] = output([*DOCKER, 'image', 'inspect', '--format', '{{.Id}}', tag])
    run([*DOCKER, 'build', '-f', SOURCE/'backend/Dockerfile.lab-audit', '-t', 'noc123-lab-audit', SOURCE/'backend'], work, 'build-auditor')
    with (work/'pip-audit.json').open('xb') as f:
        run([*DOCKER, 'run', '--rm', '--read-only', '--cap-drop', 'ALL', '--tmpfs', '/tmp:rw,nosuid,noexec,size=32m', '--tmpfs', '/audit-work:rw,exec,nosuid,nodev,size=512m,mode=700', '-e', 'TMPDIR=/audit-work', '-e', 'XDG_CACHE_HOME=/audit-work/cache', '-e', 'PIP_CACHE_DIR=/audit-work/pip', 'noc123-lab-audit'], work, 'pip-audit', stdout=f)
    node = json.loads((SOURCE/'deploy/image-lock.json').read_text())['images']['node:22-alpine']
    with (work/'npm-audit.json').open('xb') as f:
        run([*DOCKER, 'run', '--rm', '--read-only', '--cap-drop', 'ALL', '--tmpfs', '/tmp:rw,nosuid,noexec,size=64m', '-e', 'npm_config_cache=/tmp/npm', '-v', str(SOURCE/'frontend')+':/source:ro', '-w', '/source', node, 'npm', 'audit', '--package-lock-only', '--include=dev', '--json'], work, 'npm-audit', stdout=f)
    for name in ('pip-audit.json', 'npm-audit.json'):
        os.chmod(work/name, 0o600); audit_json(work/name)
    with (work/'source-locks.spdx.json').open('xb') as f:
        run([sys.executable, SOURCE/'scripts/release/sbom.py'], work, 'source-sbom', stdout=f)
    run([sys.executable, SOURCE/'scripts/release/image_security.py', '--output', work/'images', '--require-no-high', *images.values()], work, 'image-scan')
    run([sys.executable, SOURCE/'scripts/release/image_sbom.py', work/'images'], work, 'image-sbom')
    inventory = {}
    for p in sorted(work.rglob('*')):
        if p.is_symlink(): raise Reject('Unexpected symlink in lab evidence')
        if p.is_dir(): p.chmod(0o700)
        else:
            p.chmod(0o600)
            if p.suffix == '.json' and p.name != 'authorization.json':
                inventory[p.relative_to(work).as_posix()] = digest(p)
    write(work/'prepared.json', {'format':1, 'purpose':'controlled-test', 'repository':REPOSITORY,
          'commit':args.commit, 'tree':args.tree, 'images':images, 'files':inventory,
          'source_locks':{p:digest(SOURCE/p) for p in LOCKS},
          'created_utc':datetime.now(timezone.utc).isoformat(), 'builder':'operator-authorized local Docker; unsigned lab only'})
    validate_evidence(work, args.commit, args.tree)
    print('Lab evidence prepared. No application installed. Preserve this private directory; production release authorization remains absent.')


def validate_evidence(work, commit, tree, *, inspect=True):
    data = json.loads(private_file(work/'prepared.json'))
    if not isinstance(data,dict):raise Reject('Prepared evidence object required')
    if data.get('format') != 1 or data.get('purpose') != 'controlled-test' or data.get('repository') != REPOSITORY or data.get('commit') != commit or data.get('tree') != tree:
        raise Reject('Prepared lab evidence purpose/source mismatch')
    created = datetime.fromisoformat(data.get('created_utc', ''))
    age = (datetime.now(timezone.utc) - created).total_seconds()
    if not -300 <= age <= 86400:
        raise Reject('Lab audits expired/future dated; regenerate evidence')
    images = data.get('images', {})
    if set(images) != set(ROLES) or len(set(images.values())) != 4 or any(not LOCAL.fullmatch(v) for v in images.values()):
        raise Reject('Four distinct immutable local image identities required')
    files = data.get('files', {})
    if not {'pip-audit.json','npm-audit.json','source-locks.spdx.json','images/summary.json','images/sbom-index.json'} <= files.keys():
        raise Reject('Complete dependency, raw scan and SBOM evidence required')
    for name, expected in files.items():
        p = Path(name)
        if p.is_absolute() or '..' in p.parts or not re.fullmatch(r'[a-f0-9]{64}', expected):
            raise Reject('Invalid evidence inventory')
        # Every parent must stay inside protected work without symlinks.
        for parent in (work/p).parents:
            if parent == work: break
            if parent.is_symlink(): raise Reject('Symlink evidence directory denied')
        if digest(work/p) != expected:
            raise Reject('Evidence checksum mismatch')
    if data.get('source_locks') != {p:digest(SOURCE/p) for p in LOCKS}:
        raise Reject('Source dependency/image locks changed')
    source_sbom=json.loads((work/'source-locks.spdx.json').read_bytes())
    if source_sbom.get('spdxVersion')!='SPDX-2.3' or not source_sbom.get('packages'):
        raise Reject('Complete source SPDX inventory required')
    for name in ('pip-audit.json','npm-audit.json'):audit_json(work/name)
    index = json.loads((work/'images/sbom-index.json').read_bytes())
    if not isinstance(index,list) or any(not isinstance(r,dict) for r in index):raise Reject('Image inventory list required')
    if len(index) != 4 or {r['image_id'] for r in index} != set(images.values()):
        raise Reject('Missing/duplicate exact-image inventories')
    for row in index:
        if not re.fullmatch(r'image-[0-9]+[.]json', row.get('scan', '')) or not re.fullmatch(r'sbom-image-[0-9]+[.]spdx[.]json', row.get('sbom', '')):
            raise Reject('Invalid scan/SBOM paths')
        scan, sbom = 'images/'+row['scan'], 'images/'+row['sbom']
        if scan not in files or sbom not in files:
            raise Reject('Unretained scan/SBOM evidence')
        if files.get(scan) != row['scan_sha256'] or files.get(sbom) != row['sbom_sha256']:
            raise Reject('Scan/SBOM binding mismatch')
        report = json.loads((work/scan).read_bytes())
        if report.get('Metadata',{}).get('ImageID') != row['image_id'] or not report.get('Results'):
            raise Reject('Raw scanner identity/results unavailable')
        if any(v.get('Severity') in ('HIGH','CRITICAL') for result in report['Results'] for v in result.get('Vulnerabilities',[]) or []):
            raise Reject('HIGH/CRITICAL image findings prohibit lab install')
        if not json.loads((work/sbom).read_bytes()).get('packages'):
            raise Reject('Empty image SBOM')
    if inspect:
        for role, identity in images.items():
            info = json.loads(output([*DOCKER,'image','inspect',identity]))[0]
            if info.get('Id') != identity or info.get('Config',{}).get('Labels',{}).get('org.opencontainers.image.revision') != commit:
                raise Reject('Required image missing, mismatched or not source-bound')
    return data


def install(args, a):
    existing_install(); dependencies()
    report = preflight.collect()
    required = {'os','architecture','cpu_abi','cpu_capacity','memory','disk_capacity','filesystem','docker','compose','systemd','network','time_sync','ports','existing_installation'}
    statuses = {r['check']:r['status'] for r in report['checks']}
    blocked = sorted(k for k in required if statuses.get(k) != 'PASS')
    if report['failed'] or blocked:
        # Only fixed check identifiers/statuses, never addresses or raw diagnostics.
        detail = ', '.join(k+'='+str(statuses.get(k,'NOT VERIFIED')) for k in blocked)
        raise Reject('Host preflight failed or required check unavailable: '+detail+'; run read-only preflight for details')
    if next((r['status'] for r in report['checks'] if r['check']=='firewall'),None) != 'WARNING':
        raise Reject('Active local UFW observation plus independent firewall review required')
    work = protected_work(args.work)
    data = validate_evidence(work, args.commit, args.tree)
    write(work/'install-authorization.json', a)
    write(work/'approval.json', {'format':1,'purpose':'controlled-test','repository':REPOSITORY,
          'approval_reference':a['reference'],'commit':args.commit,'tree':args.tree,
          'migration_head':'0029_multi_provider_sms','upgrade_from':[],'images':data['images']})
    cert,key = work/'lab.crt',work/'lab.key'
    run(['openssl','req','-x509','-newkey','rsa:3072','-sha256','-days','7','-nodes','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost','-keyout',key,'-out',cert],work,'lab-tls')
    cert.chmod(0o600);key.chmod(0o600)
    argv=[SOURCE/'install.sh','--controlled-test','--approval',work/'approval.json','--backup-recipient',a['backup_recipient'],'--tls-cert',cert,'--tls-key',key,'--tls-ca',cert]
    if args.password_stdin: argv += ['--password-stdin']
    # Existing installer owns private password prompting/stdin; never capture/persist it.
    subprocess.run([str(v) for v in argv], cwd=SOURCE, check=True, timeout=2400, env=environment())
    subprocess.run([str(SOURCE/'verify.sh'),'--controlled-test'], cwd=SOURCE, check=True, timeout=300, env=environment())
    print(json.dumps({'purpose':'controlled-test','services':'existing verify.sh passed','application_health':'PASS','migration':'0029_multi_provider_sms verified by installer','administrator_setup':'existing secure initializer completed','dashboard':'https://localhost:18090/','ssh_tunnel':'ssh -L 18090:127.0.0.1:18090 '+a['ssh_host'],'backup':'age public recipient configured; approved independent destination remains operator-managed','backup_destination':a['backup_destination'],'integrations':'all live collectors/Powercode/messages/publication/remediation remain disabled','troubleshooting':['sudo ./verify.sh --controlled-test','sudo systemctl status nwo-noc.service nwo-noc-ingress.service','sudo journalctl -u nwo-noc.service --since today'],'decision':'NO-GO for production/live pilot'}))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['plan','prepare','install']);p.add_argument('--commit',required=True);p.add_argument('--tree',required=True)
    p.add_argument('--work',default='/root/nwo-noc-lab');p.add_argument('--execute',action='store_true');p.add_argument('--authorization-file');p.add_argument('--password-stdin',action='store_true')
    args=p.parse_args(argv)
    try:
        source_check(args.commit,args.tree)
        existing_install()
        if args.action=='plan' or not args.execute:
            print(json.dumps({'purpose':'controlled-test only','changes_authorized':False,'source':args.commit,'tree':args.tree,'preflight':preflight.collect(),'actions':['explicit isolated-lab authorization','four source-bound image builds','complete dependency audits/raw scans/SBOM','protected purpose-separated approval','lab TLS and independently held backup recipient','existing install.sh --controlled-test','existing verify.sh --controlled-test'],'production_signature_gate':'unchanged; no production authorization'}));return 0
        if os.geteuid()!=0:raise Reject('Explicit root operator execution required; no changes made')
        if args.action=='install' and not args.password_stdin:
            with open('/dev/tty'):
                pass  # refuse getpass fallback without a controlling secure terminal
        a=authorization(args)
        if args.action=='prepare':prepare(args,a)
        else:install(args,a)
        return 0
    except Reject as error:
        print('Guided lab rejected: '+str(error)+'. Evidence preserved; no cleanup or production authorization.',file=sys.stderr)
        return 1
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        print('Guided lab operation rejected/failed. Run plan/preflight; inspect protected evidence. Existing data, services and production gates are preserved.',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
