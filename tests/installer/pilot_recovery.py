"""Synthetic two-host recovery acceptance; never an operational restore command."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/host'))
from artifacts import verify_signature
from policy import safe_members, file_hash


def run(argv, *, data=None, output=None, check=True):
    p = subprocess.run([str(v) for v in argv], input=data, stdout=output or subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=1800)
    if check and p.returncode:
        raise RuntimeError('Disposable recovery operation failed: ' + str(argv[0]))
    return p


def export_checkpoint(work, clone, approved, identity, backup, password, compose, state, tested_images):
    folder = work / 'recovery-export'
    folder.mkdir(mode=0o700)
    shutil.copy2(backup, folder / 'backup.age')
    shutil.copy2(backup.with_suffix('.age.json'), folder / 'backup-metadata.json')
    shutil.copy2(identity, folder / 'backup.key')
    shutil.copy2(approved, folder / 'approval.json')
    # Test-side credentials are never installed in runtime.env or uploaded as CI artifacts.
    (folder / 'synthetic-auth.json').write_text(json.dumps({'username': 'admin', 'password': password}))
    images = json.loads((Path('/opt/nwo-noc/releases') / json.loads(approved.read_text())['commit'] / 'release.json').read_text())['approval']['images']
    with (folder / 'images.tar').open('xb') as stream:
        ids=set(tested_images)|{run(['docker','image','inspect','--format','{{.Id}}',ref]).stdout.decode().strip() for ref in images.values()}
        run(['docker', 'save', *sorted(ids)], output=stream)
    # Shared clones have access to alternate objects but do not retain the
    # non-ancestor audit refs unless explicitly fetched before offline export.
    run(['git','-c',f'safe.directory={clone}','-c',f'safe.directory={ROOT}','-c',f'safe.directory={ROOT / ".git"}',
         '-C',clone,'fetch','--quiet',ROOT,'+refs/*:refs/noc119-audit/*'])
    run(['git', '-c', f'safe.directory={clone}', '-C', clone, 'bundle', 'create', folder / 'source.bundle', '--all'])
    key = work / 'identities/recovery-signing.key'
    run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', key])
    run(['openssl', 'pkey', '-in', key, '-pubout', '-out', folder / 'trusted-public.pem'])
    run(['openssl','pkeyutl','-sign','-inkey',key,'-rawin','-in',folder/'approval.json','-out',folder/'approval.json.sig'])
    manifest = {'format': 1, 'purpose': 'isolated-recovery-test', 'source_machine_id': Path('/etc/machine-id').read_text().strip(),
                'source_boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'release_commit': json.loads(approved.read_text())['commit'], 'images': images, 'tested_runtime_image_ids':sorted(ids),
                'files': {name: file_hash(folder / name) for name in ('backup.age', 'backup-metadata.json', 'backup.key', 'approval.json', 'approval.json.sig', 'source.bundle', 'images.tar', 'synthetic-auth.json')}}
    (folder / 'recovery.json').write_text(json.dumps(manifest))
    run(['openssl', 'pkeyutl', '-sign', '-inkey', key, '-rawin', '-in', folder / 'recovery.json', '-out', folder / 'recovery.json.sig'])
    from release_trust import fingerprint
    (work / 'independent-trust-fingerprint').write_text(fingerprint((folder / 'trusted-public.pem').read_bytes()))
    for f in folder.iterdir():
        f.chmod(0o600)
    key.chmod(0o600)
    (work / 'independent-trust-fingerprint').chmod(0o600)
    print(json.dumps({'checkpoint': 'signed synthetic recovery package prepared', 'production_signature': False, 'private_signing_key_exported': False}), flush=True)


def validate(folder, expected):
    from release_trust import fingerprint
    public = (folder / 'trusted-public.pem').read_bytes()
    if fingerprint(public) != expected:
        raise ValueError('Independent recovery fingerprint mismatch')
    raw = (folder / 'recovery.json').read_bytes()
    verify_signature(raw, (folder / 'recovery.json.sig').read_bytes(), public)
    manifest = json.loads(raw)
    if manifest.get('format') != 1 or manifest.get('purpose') != 'isolated-recovery-test':
        raise ValueError('Explicit isolated recovery test contract required')
    required = {'backup.age', 'backup-metadata.json', 'backup.key', 'approval.json', 'approval.json.sig', 'source.bundle', 'images.tar', 'synthetic-auth.json'}
    if set(manifest.get('files', {})) != required:
        raise ValueError('Incomplete recovery artifacts')
    for name, digest in manifest['files'].items():
        if (folder / name).is_symlink() or file_hash(folder / name) != digest:
            raise ValueError('Recovery artifact integrity mismatch')
    verify_signature((folder/'approval.json').read_bytes(),(folder/'approval.json.sig').read_bytes(),public)
    return manifest


def unpack_transport(archive, destination):
    """Extract only a bounded, exact flat test transport; never trust tar paths."""
    allowed={'backup.age','backup-metadata.json','backup.key','approval.json','approval.json.sig','source.bundle','images.tar','synthetic-auth.json','trusted-public.pem','recovery.json','recovery.json.sig'}
    with tarfile.open(archive) as tar:
        members=[];names=[];size=0
        for m in tar:
            if m.name=='.' and m.isdir():continue
            name=m.name.removeprefix('./');size+=m.size
            if name not in allowed or name in names or not m.isfile() or m.size<0 or size>6*1024**3 or m.name not in(name,'./'+name):
                raise ValueError('Unsafe or incomplete test recovery transport')
            members.append(m);names.append(name)
        if set(names)!=allowed:
            raise ValueError('Unsafe or incomplete test recovery transport')
        destination=Path(destination);destination.mkdir(mode=0o700,exist_ok=False)
        for m,name in zip(members,names):
            with tar.extractfile(m) as src,(destination/name).open('xb') as out:
                (destination/name).chmod(0o600);shutil.copyfileobj(src,out)


@contextmanager
def restoration_guard(host):
    """Retain recovery state and block future starts if a new-host restore fails."""
    from manager import write_json
    write_json(host.guard,{'phase':'independent-restoration-in-progress'})
    try:
        yield
    except BaseException:
        write_json(host.guard,{'phase':'independent-restoration-failed','recovery':'preserve all state and investigate; no automatic overwrite'})
        try:
            state=host.state()
            host.compose(host.opt/'releases'/state['commit'],state,'stop','frontend','planning-worker','worker','backend',check=False)
        except Exception:
            pass  # Original restore error still propagates; guard remains and data is retained.
        raise



def restore_release_modes(members, release):
    """Restore reviewed code traversal after data-filter extraction; retain private metadata."""
    for member in members:
        if member.name != 'release' and not member.name.startswith('release/'):
            continue
        if member.uid != 0 or member.gid != 0 or member.mode & 0o022:
            raise ValueError('Unsafe recovered release ownership or permissions')
        relative=Path(member.name).relative_to('release')
        destination=Path(release)/relative
        if destination.is_symlink() or not (member.isfile() or member.isdir()):
            raise ValueError('Unsafe recovered release entry')
        destination.chmod(member.mode & 0o777)



def authentication_request(auth):
    """Use the existing OAuth2 form contract; no new customer/session platform."""
    from urllib.parse import urlencode
    from urllib.request import Request
    return Request('https://localhost:18090/api/auth/login',data=urlencode(auth).encode(),
                   headers={'Content-Type':'application/x-www-form-urlencoded'})



REDIS_UID=999
REDIS_GID=1000


def redis_restore_members(archive):
    """Pinned Redis7 Alpine uses999:1000; never accept arbitrary saved owners."""
    members=safe_members(archive)
    for member in members:
        if not (member.name=='redis' or member.name.startswith('redis/')) or member.uid not in (0,REDIS_UID) or member.gid not in (0,REDIS_GID):
            raise ValueError('Unsafe Redis archive ownership or path')
    return members


def restore(folder, expected):
    """Only on an empty independent VM, using explicitly synthetic signed artifacts."""
    if os.geteuid() != 0 or Path('/.dockerenv').exists() or not Path('/run/systemd/system').is_dir():
        raise RuntimeError('Independent root systemd VM required')
    if any(Path(p).exists() for p in ('/opt/nwo-noc', '/etc/nwo-noc', '/var/lib/nwo-noc')):
        raise RuntimeError('Existing installation retained; restore refuses overwrites')
    manifest = validate(folder, expected)
    if Path('/etc/machine-id').read_text().strip() == manifest['source_machine_id']:
        raise RuntimeError('Recovery host is not independent')
    tests = []
    def passed(name, condition=True):
        if not condition:
            raise AssertionError(name)
        tests.append(name)
        print(json.dumps({'check': name, 'status': 'PASS'}), flush=True)
    passed('signed_artifacts_and_independent_key')
    passed('independent_machine_and_kernel_boot', Path('/proc/sys/kernel/random/boot_id').read_text().strip() != manifest['source_boot_id'])
    with tempfile.TemporaryDirectory(prefix='noc119-private-restore-') as temp:
        temp = Path(temp)
        wrong = temp / 'wrong.key'
        run(['age-keygen', '-o', wrong])
        for key in (wrong, temp / 'missing.key'):
            passed('incorrect_or_missing_key_rejected', run(['age', '-d', '-i', key, folder / 'backup.age'], check=False).returncode != 0)
        good = folder / 'backup.age'
        damaged = folder / 'damaged.age'
        with damaged.open('wb') as out, good.open('rb') as src:
            shutil.copyfileobj(src, out)
        with damaged.open('r+b') as stream:
            first = stream.read(1); stream.seek(0); stream.write(bytes([first[0] ^ 1]))
        held = folder / 'backup.age.held'
        good.rename(held)
        try:
            damaged.rename(good)
            try: validate(folder, expected)
            except ValueError: passed('damaged_backup_hash_rejected')
            else: raise AssertionError('Damaged backup accepted')
        finally:
            good.unlink(missing_ok=True)
            held.rename(good)
        sentinel = temp / 'sentinel'; sentinel.write_text('preserve')
        original = folder / 'source.bundle'
        original.rename(folder / 'source.bundle.held')
        try:
            try: validate(folder, expected)
            except (ValueError, OSError): passed('missing_artifact_rejected')
            else: raise AssertionError('missing artifact accepted')
        finally: (folder / 'source.bundle.held').rename(original)
        passed('failure_checks_preserve_valid_data', sentinel.read_text() == 'preserve' and not Path('/etc/nwo-noc').exists())
        archive = temp / 'restore.tar'
        with archive.open('xb') as output:
            archive.chmod(0o600)
            run(['age', '-d', '-i', folder / 'backup.key', good], output=output)
        extracted = temp / 'extracted'; extracted.mkdir(mode=0o700)
        with tarfile.open(archive) as tar:
            members = safe_members(tar)
            names = {m.name for m in members}
            passed('complete_database_configuration_keys_documents_release', {'database.dump','redis.tar','installation.json','configuration/runtime.env','configuration/keys/signup','release/release.json','data/documents/synthetic-preservation.txt'} <= names)
            tar.extractall(extracted, members=members, filter='data')
        import re
        import policy
        approved=policy.approval(folder/'approval.json',controlled=True)
        state=json.loads((extracted/'installation.json').read_text())
        metadata=json.loads((extracted/'release/release.json').read_text())
        if state.get('purpose')!='controlled-test' or state.get('commit')!=approved['commit'] or state['commit']!=manifest['release_commit'] or not re.fullmatch(r'noc113-[a-f0-9]{12}',state.get('project','')) or metadata['approval']['images']!=manifest['images'] or any(manifest['images'].get(k)!=v for k,v in approved['images'].items()):
            raise ValueError('Recovered controlled-test source or image relationships invalid')
        import manager
        args = manager.parser().parse_args(['verify', '--controlled-test'])
        h = manager.Host(args)
        h.environment(); h.dependencies(); h.account(); h.directories()
        with restoration_guard(h):
            shutil.copytree(extracted / 'configuration', h.etc, dirs_exist_ok=True)
            state = json.loads((extracted / 'installation.json').read_text())
            release = h.opt / 'releases' / state['commit']
            shutil.copytree(extracted / 'release', release)
            restore_release_modes(members, release)
            for name in manager.DATA_DIRS:
                shutil.copytree(extracted / 'data' / name, h.lib / name, dirs_exist_ok=True)
            # Restore only reviewed identities, never arbitrary archive owners or setuid modes.
            for m in members:
                if m.name.startswith('configuration/'):
                    dest = h.etc / m.name.removeprefix('configuration/')
                elif m.name.startswith('data/'):
                    dest = h.lib / m.name.removeprefix('data/')
                else: continue
                if m.uid not in (0, 101, 10001) or m.gid not in (0, 101, 10001):
                    raise ValueError('Unexpected recovered owner')
                os.chown(dest, m.uid, m.gid); dest.chmod(m.mode & 0o777)
            manager.write_json(h.state_path, state)
            h.prepare_ingress()
            h.compose(release, state, 'up', '-d', '--wait', 'db', 'redis')
            h.initialize_database(release, state)
            with (extracted / 'database.dump').open('rb') as dump:
                h.compose(release, state, 'exec', '-T', 'db', 'pg_restore', '-U', 'postgres', '-d', 'nwo_noc', '--role=noc_owner', '--no-owner', '--no-privileges', '--exit-on-error', input_file=dump)
            h.psql(release, state, h.role_sql(release))
            h.compose(release, state, 'run', '--rm', '--no-deps', 'migrate')
            passed('restored_migrations_compatible')
            with tarfile.open(extracted / 'redis.tar') as redis:
                passed('redis_archive_safe', bool(redis_restore_members(redis)))
            h.compose(release, state, 'stop', 'redis')
            redis_script = """
    import tarfile,shutil,os
    from pathlib import Path
    with tarfile.open('/backup.tar') as t:
     for m in t.getmembers():
      p=Path(m.name)
      if p.is_absolute() or '..' in p.parts or not(m.isfile() or m.isdir()) or not(m.name=='redis' or m.name.startswith('redis/')) or m.uid not in(0,REDIS_UID) or m.gid not in(0,REDIS_GID): raise ValueError('Unsafe Redis archive')
      dest=Path('/volume')/('/'.join(p.parts[1:]))
      if m.isdir(): dest.mkdir(parents=True,exist_ok=True)
      else:
       dest.parent.mkdir(parents=True,exist_ok=True)
       with t.extractfile(m) as src,dest.open('wb') as out: shutil.copyfileobj(src,out)
      os.chown(dest,m.uid,m.gid);dest.chmod(m.mode&0o777)
    """
            identity=run(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL',
                          '--entrypoint','sh',manifest['images']['redis'],'-ec','id -u redis; id -g redis']).stdout.decode().split()
            if identity != [str(REDIS_UID),str(REDIS_GID)]:
                raise ValueError('Pinned Redis runtime identity changed; review restore compatibility')
            redis_script=chr(10).join((f'REDIS_UID={REDIS_UID}',f'REDIS_GID={REDIS_GID}',textwrap.dedent(redis_script)))
            run(['docker','run','--rm','--network','none','--read-only','--user','0:0','--cap-drop','ALL','--cap-add','CHOWN','--cap-add','DAC_OVERRIDE',
                 '--mount',f"type=volume,src={state['project']}-redis,dst=/volume",'-v',f'{extracted}/redis.tar:/backup.tar:ro','--entrypoint','python',manifest['images']['backend'],'-c',redis_script])
            h.switch(release); h.register(release)
            h.guard.unlink()  # All signed data, keys and migrations restored before managed startup.
            run(['systemctl', 'start', 'nwo-noc.service'])
            h.start_ingress(release, state)
            result = run([release / 'verify.sh', '--controlled-test'])
            verification = json.loads(result.stdout)
            passed('restored_application_verification', all(c['status'] != 'FAILED' for c in verification['checks']))
            from urllib.request import build_opener, ProxyHandler, HTTPSHandler, Request
            import ssl
            auth = json.loads((folder / 'synthetic-auth.json').read_text())
            browser = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=str(h.etc / 'tls-cert.pem'))))
            with browser.open(authentication_request(auth), timeout=15) as response:
                passed('restored_original_user_authentication', bool(json.load(response)['access_token']))
            history = h.compose(release,state,'exec','-T','db','psql','-U','postgres','-d','nwo_noc','-t','-A','-c',"SELECT count(*) FROM audit_logs WHERE action='installer.synthetic_sentinel';").stdout.strip()
            passed('preserved_monitoring_and_audit_history', history == b'1')
            sample = h.compose(release,state,'exec','-T','db','psql','-U','postgres','-d','nwo_noc','-t','-A','-c',"SELECT count(*) FROM telemetry_samples t JOIN devices d ON d.id=t.device_id WHERE d.external_id='noc119-recovery';").stdout.strip()
            passed('preserved_actual_telemetry_sample', sample == b'1')
            passed('preserved_redis_queue_state', h.compose(release,state,'exec','-T','redis','redis-cli','GET','noc119:preservation').stdout.strip()==b'synthetic')
            passed('preserved_document_bytes', (h.lib / 'documents/synthetic-preservation.txt').read_text() == 'synthetic document preservation')
            try: restore(folder, expected)
            except RuntimeError: passed('existing_restored_installation_cannot_be_overwritten')
            else: raise AssertionError('Existing restore accepted')
    print(json.dumps({'checks_passed':len(tests),'checks':tests,'independent_vm':True,'same_hypervisor_failure_domain':True,'real_vpn_mfa':False,'production_release_authorized':False}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--folder', type=Path, required=True)
    p.add_argument('--expected-fingerprint')
    p.add_argument('--unpack',type=Path)
    a = p.parse_args()
    if a.unpack:unpack_transport(a.unpack,a.folder)
    elif a.expected_fingerprint:restore(a.folder,a.expected_fingerprint)
    else:p.error('Independent expected fingerprint required for restore')
