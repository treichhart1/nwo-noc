"""Disposable Ubuntu24.04 packaging acceptance; synthetic data and local test images only.

Run as root only on the CI disposable host or inside the explicit rehearsal container.
Never against an existing NOC installation. Outputs assertion metadata, never credentials.
"""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import signal
import tarfile
import tempfile
import ssl
import urllib.request
import urllib.parse
from uuid import uuid4


def command(argv, *, data=None, success=True, cwd=None, env=None):
    p = subprocess.run(
        [str(v) for v in argv],
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        timeout=1800,
    )
    if success and p.returncode:
        if str(argv[0]) == "systemctl" and any("nwo-noc" in str(v) for v in argv):
            journal = subprocess.run(
                [
                    "journalctl",
                    "-u",
                    "nwo-noc.service",
                    "-u",
                    "nwo-noc-ingress.service",
                    "-n",
                    "80",
                    "--no-pager",
                    "-o",
                    "cat",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            safe = [
                line
                for line in journal.stdout.splitlines()
                if line.startswith("NOC packaging")
                or line.startswith("nwo-noc.service:")
                or line.startswith("nwo-noc-ingress.service:")
                or "Failed at step" in line
            ]
            print(json.dumps({"sanitized_unit_diagnostics": safe}), flush=True)
            state_path = Path("/var/lib/nwo-noc/installation.json")
            if state_path.exists():
                state = json.loads(state_path.read_text())
                statuses = subprocess.run(
                    [
                        "docker",
                        "ps",
                        "-a",
                        "--filter",
                        "label=com.docker.compose.project=" + state["project"],
                        "--format",
                        "{{.Names}} {{.Status}}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                print(
                    json.dumps(
                        {"managed_container_status": statuses.stdout.splitlines()}
                    ),
                    flush=True,
                )
                errors = subprocess.run(
                    [
                        "docker",
                        "logs",
                        "--tail",
                        "20",
                        state["project"] + "-frontend-1",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                print(
                    json.dumps(
                        {
                            "nginx_startup_errors": [
                                line
                                for line in errors.stderr.splitlines()
                                if "[emerg]" in line
                            ]
                        }
                    ),
                    flush=True,
                )
        # Only the installer's deliberately sanitized diagnostics may enter CI output.
        # A database/library exception may otherwise include credential-bearing URLs.
        safe_errors = [
            line
            for line in p.stderr.decode(errors="replace").splitlines()
            if line.startswith(("NOC packaging", "Guided lab rejected:", "Guided lab operation rejected/failed."))
        ]
        raise RuntimeError(
            "Acceptance command failed: "
            + str(argv[0])
            + " / exit "
            + str(p.returncode)
            + " / "
            + "\n".join(safe_errors)[-800:]
        )
    return p


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--guided-evidence", type=Path)
    p.add_argument("--rehearsal", action="store_true")
    p.add_argument("--worktree-snapshot", action="store_true")
    p.add_argument("--extended", action="store_true")
    p.add_argument("--pilot-checkpoint", action="store_true", help="Retain healthy disposable VM for reboot and second-host recovery acceptance")
    p.add_argument(
        "--failure-stage",
        choices=[
            "database",
            "migration",
            "startup",
            "interrupted",
            "resources",
            "dependencies",
        ],
    )
    p.add_argument("--backend", default="noc113-backend:base")
    p.add_argument("--frontend", default="noc113-frontend:base")
    p.add_argument("--postgres", default="noc117-postgres:rc")
    p.add_argument("--redis", default="noc120-redis:rc")
    args = p.parse_args()
    if (
        os.geteuid() != 0
        or 'VERSION_ID="24.04"' not in Path("/etc/os-release").read_text()
        or "ID=ubuntu" not in Path("/etc/os-release").read_text()
    ):
        raise RuntimeError("Disposable Ubuntu24.04 root environment required")
    source = Path(__file__).resolve().parents[2]
    work = Path(args.workspace)
    work.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(work, 0o700)
    home = work / "harness-home"
    home.mkdir(mode=0o700)
    (home / ".gitconfig").write_text(
        "[safe]\n\tdirectory = "
        + str(source)
        + "\n\tdirectory = "
        + str(source / ".git")
        + "\n"
    )
    os.environ["HOME"] = str(home)
    clone = work / "source"
    command(
        [
            "git",
            "-c",
            f"safe.directory={source}",
            "-c",
            f"safe.directory={source / '.git'}",
            "clone",
            "--shared",
            "--quiet",
            source,
            clone,
        ]
    )

    # Exercise the documented regular-user clone followed by sudo installation.
    # Root-owned test clones previously masked child Git audits rejecting ownership.
    os.chown(clone, 65534, 65534)
    os.chown(clone / ".git", 65534, 65534)

    def git(*argv):
        return (
            command(["git", "-c", f"safe.directory={clone}", "-C", clone, *argv])
            .stdout.decode()
            .strip()
        )

    git("config", "user.name", "NOC113 Synthetic Acceptance")
    git("config", "user.email", "installer-acceptance@example.invalid")
    git("remote", "set-url", "origin", "https://github.com/treichhart1/nwo-noc.git")
    if args.worktree_snapshot:
        files = (
            command(
                [
                    "git",
                    "-c",
                    f"safe.directory={source}",
                    "-C",
                    source,
                    "ls-files",
                    "--modified",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ]
            )
            .stdout.decode()
            .split("\0")
        )
        for name in filter(None, files):
            path = source / name
            if path.is_file():
                (clone / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, clone / name)
        git("add", ".")
        git("commit", "--quiet", "-m", "Synthetic current worktree packaging snapshot")
    # All commits are confined to this disposable clone, never the development branch.
    # Guided evidence is bound to the exact reviewed source, never a synthetic
    # first-install revision. Later upgrade revisions remain disposable fixtures.
    if not args.guided_evidence:
        git(
            "commit",
            "--allow-empty",
            "--quiet",
            "-m",
            "Synthetic approved first packaging release",
        )
    root = work / "root" if args.rehearsal else Path("/")
    if args.failure_stage == "resources":
        if not args.rehearsal or not Path("/noc114-small").is_dir():
            raise RuntimeError("Resource probe requires the disposable64MiB tmpfs")
        root = Path("/noc114-small")
    if not args.rehearsal and any(
        Path(v).exists() for v in ["/etc/nwo-noc", "/var/lib/nwo-noc", "/opt/nwo-noc"]
    ):
        raise RuntimeError("Refusing acceptance on an existing installation")
    env = (
        {**os.environ, "NOC_INSTALLER_REHEARSAL": "1"}
        if args.rehearsal
        else os.environ.copy()
    )
    approvals = work / "approvals"
    approvals.mkdir(mode=0o700)
    identities = work / "identities"
    identities.mkdir(mode=0o700)
    # age must be available before generating a public recipient; production install also provisions it.
    if not shutil.which("age-keygen"):
        command(["apt-get", "update"])
        command(["apt-get", "install", "-y", "--no-install-recommends", "age"])
    identity = identities / "backup.key"
    command(["age-keygen", "-o", identity])
    os.chmod(identity, 0o600)
    recipient = command(["age-keygen", "-y", identity]).stdout.decode().strip()
    tls = work / "tls"
    tls.mkdir(mode=0o700)
    certificate = tls / "certificate.pem"
    tls_key = tls / "key.pem"
    command(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-keyout",
            tls_key,
            "-out",
            certificate,
        ]
    )
    certificate.chmod(0o600)
    tls_key.chmod(0o600)
    results = []
    successful_release_images = set()

    acceptance_started=time.monotonic()

    def passed(name, condition=True):
        if not condition:
            raise AssertionError(name)
        results.append(name)
        print(json.dumps({"check": name, "status": "PASSED", "elapsed_seconds": round(time.monotonic()-acceptance_started,3)}), flush=True)

    def image(kind, revision, bad=False):
        tag = "noc113-test-" + kind + "-" + revision[:10]
        base = {"backend": args.backend, "frontend": args.frontend, "postgres": args.postgres, "redis": args.redis}[kind]
        failure = args.failure_stage if kind == "backend" else None
        if failure in ("migration", "interrupted"):
            shell = (
                'if [ "$1" = alembic ] && [ "$2" = upgrade ]; then '
                + ("sleep 300; " if failure == "interrupted" else "")
                + 'exit 42; fi; exec "$@"'
            )
            entry = "\nENTRYPOINT " + json.dumps(["sh", "-c", shell, "--"]) + "\n"
        else:
            entry = (
                "\nENTRYPOINT "
                + json.dumps(
                    [
                        "sh",
                        "-c",
                        'if [ "$1" = uvicorn ]; then exit 42; fi; exec "$@"',
                        "--",
                    ]
                )
                + "\n"
                if bad
                else "\n"
            )
        dockerfile = (
            f'FROM {base}\nLABEL org.opencontainers.image.revision="{revision}"\n'
            + entry
        )
        command(["docker", "build", "-t", tag, "-"], data=dockerfile.encode())
        identity = (
            command(["docker", "image", "inspect", "--format", "{{.Id}}", tag])
            .stdout.decode()
            .strip()
        )
        if not bad and not failure:
            successful_release_images.add(identity)
        return identity

    def approve(previous=(), bad=False):
        revision = git("rev-parse", "HEAD")
        value = {
            "format": 1,
            "repository": "treichhart1/nwo-noc",
            "purpose": "controlled-test",
            "approval_reference": "synthetic-disposable-acceptance",
            "commit": revision,
            "tree": git("rev-parse", "HEAD^{tree}"),
            "migration_head": "0031_wordpress_status",
            "upgrade_from": list(previous),
            "images": {
                "backend": image("backend", revision, bad),
                "frontend": image("frontend", revision),
                "postgres": image("postgres", revision),
                "redis": image("redis", revision),
            },
        }
        file = approvals / (revision + ".json")
        file.write_text(json.dumps(value))
        file.chmod(0o600)
        return revision, file

    def invoke(action, approved=None, secret=None, success=True, extra=()):
        argv = (
            [clone / (action + ".sh")]
            if action in ("install", "upgrade", "verify")
            else [
                "python3",
                clone / "scripts/host/manager.py",
                action,
            ]
        ) + ["--controlled-test"]
        if args.rehearsal:
            argv += ["--root", root, "--rehearsal"]
        if approved:
            argv += ["--approval", approved]
        if secret:
            argv += ["--password-stdin"]
        argv += list(extra)
        return command(
            argv,
            data=(secret + "\n").encode() if secret else None,
            success=success,
            cwd=clone,
            env=env,
        )

    command(["docker", "buildx", "version"])
    if args.failure_stage == "database":
        # Only this disposable clone changes: no real image, migration or development file is edited.
        import yaml

        file = clone / "deploy/compose.isolated.yml"
        config = yaml.safe_load(file.read_text())
        config["services"]["db"]["command"] = ["sh", "-c", "exit 42"]
        file.write_text(yaml.safe_dump(config))
        git("add", "deploy/compose.isolated.yml")
        git("commit", "--quiet", "-m", "Synthetic PostgreSQL startup failure fixture")
    first, first_approval = approve(bad=args.failure_stage == "startup")
    admin_secret = secrets.token_urlsafe(30)
    if args.failure_stage:
        if not args.rehearsal:
            raise RuntimeError(
                "Fresh failure matrix requires its disposable container/root"
            )
        from failure_acceptance import fresh_failure

        fresh_failure(
            args,
            clone,
            root,
            work,
            first_approval,
            admin_secret,
            env,
            command,
            invoke,
            passed,
        )
        return
    if args.extended:
        from failure_acceptance import preinstall_failures

        preinstall_failures(invoke, first_approval, admin_secret, root, passed)
    started = time.monotonic()
    if args.guided_evidence:
        if args.rehearsal or not args.pilot_checkpoint:
            raise RuntimeError("Guided acceptance requires the real independent disposable VM")
        from guided_acceptance import install as guided_install
        installed,first_approval,certificate,tls_key=guided_install(clone,args.guided_evidence,work,recipient,admin_secret,command,passed)
        successful_release_images.update(json.loads(first_approval.read_text())["images"].values())
    else:
        installed = invoke(
            "install",
            first_approval,
            admin_secret,
            extra=[
                "--backup-recipient",
                recipient,
                "--tls-cert",
                certificate,
                "--tls-key",
                tls_key,
                "--tls-ca",
                certificate,
            ],
        )
    passed("clean_complete_application_install", installed.returncode == 0)
    passed(
        "root_install_from_operator_owned_checkout",
        clone.stat().st_uid == 65534 and (clone / ".git").stat().st_uid == 65534,
    )
    install_seconds = round(time.monotonic() - started, 3)
    state_path = root / "var/lib/nwo-noc/installation.json"
    state = json.loads(state_path.read_text())
    passed(
        "administrator_password_not_retained",
        admin_secret.encode() not in installed.stdout
        and admin_secret.encode() not in installed.stderr
        and admin_secret not in (root / "etc/nwo-noc/runtime.env").read_text()
        and "ADMIN_PASSWORD=" in (root / "etc/nwo-noc/runtime.env").read_text(),
    )
    passed("ready_state", state["status"] == "ready" and state["commit"] == first)
    before = hashlib.sha256((root / "etc/nwo-noc/runtime.env").read_bytes()).hexdigest()
    repeated = invoke(
        "install", first_approval, secrets.token_urlsafe(30), success=False
    )
    passed("repeated_install_refused", repeated.returncode != 0)
    passed(
        "configuration_not_overwritten",
        before
        == hashlib.sha256((root / "etc/nwo-noc/runtime.env").read_bytes()).hexdigest(),
    )
    verified = invoke("verify")
    verification = json.loads(verified.stdout)
    if args.extended:
        print(
            json.dumps(
                {
                    "verification_output": verification,
                    "install_seconds": install_seconds,
                }
            ),
            flush=True,
        )
    passed(
        "verification_no_failed_checks",
        all(v["status"] != "FAILED" for v in verification["checks"]),
    )
    passed(
        "optional_integrations_not_configured",
        any(
            v["check"] == "live_integrations_public_status_and_external_delivery"
            and v["status"] == "NOT_CONFIGURED"
            for v in verification["checks"]
        ),
    )
    compose = [
        "docker",
        "compose",
        "-p",
        state["project"],
        "-f",
        root / "opt/nwo-noc/releases" / first / "compose.json",
    ]

    if args.pilot_checkpoint:
        # Measure the unchanged real policy in the installed, resource-limited
        # backend. No administrator credential/hash enters output or argv.
        cost_script = """
import json,time
from app.core.security import hash_password,verify_password,pwd_context
plain='synthetic-cryptographic-cost-only'
start=time.monotonic();hashed=hash_password(plain);hash_seconds=time.monotonic()-start
start=time.monotonic();matched=verify_password(plain,hashed);verify_seconds=time.monotonic()-start
start=time.monotonic();mismatched=verify_password(plain+'-wrong',hashed);wrong_seconds=time.monotonic()-start
print(json.dumps({'scheme':pwd_context.identify(hashed),'policy_rounds':pwd_context.handler('bcrypt').default_rounds,
 'hash_rounds':int(hashed.split('$')[2]),'hash_seconds':hash_seconds,'verify_seconds':verify_seconds,
 'wrong_seconds':wrong_seconds,'correct_password_accepted':matched,'incorrect_password_rejected':not mismatched}))
"""
        cost=json.loads(command([*compose,'exec','-T','backend','python','-c',cost_script]).stdout)
        passed('unchanged_bcrypt_policy_measured_in_installed_backend',
               cost['scheme']=='bcrypt' and cost['hash_rounds']==cost['policy_rounds']
               and cost['correct_password_accepted'] and cost['incorrect_password_rejected'])
        print(json.dumps({'authentication_cost':cost}),flush=True)

    context = ssl.create_default_context(cafile=str(certificate))
    browser = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
    )
    form = urllib.parse.urlencode(
        {"username": "admin", "password": admin_secret}
    ).encode()
    request = urllib.request.Request(
        "https://localhost:18090/api/auth/login",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with browser.open(request, timeout=15) as response:
        token = json.load(response)["access_token"]
    passed("first_administrator_authentication_over_verified_private_tls", bool(token))
    with browser.open(
        urllib.request.Request(
            "https://localhost:18090/api/auth/me",
            headers={"Authorization": "Bearer " + token},
        ),
        timeout=15,
    ) as response:
        passed("administrator_role", json.load(response)["role"] == "admin")
    with browser.open(
        urllib.request.Request(
            "https://localhost:18090/api/sites",
            headers={"Authorization": "Bearer " + token},
        ),
        timeout=15,
    ) as response:
        passed("no_demonstration_inventory_seeded", json.load(response) == [])
    if not args.rehearsal:
        passed(
            "real_systemd_service_active",
            command(["systemctl", "is-active", "nwo-noc.service"]).stdout.strip()
            == b"active",
        )
        passed(
            "dedicated_account",
            command(["id", "-u", "nwo-noc"]).stdout.strip() == b"10001",
        )
        passed(
            "dedicated_account_has_no_docker_or_extra_groups",
            command(["id", "-nG", "nwo-noc"]).stdout.decode().split() == ["nwo-noc"],
        )
        passed(
            "unit_syntax",
            command(
                [
                    "systemd-analyze",
                    "verify",
                    "/etc/systemd/system/nwo-noc.service",
                    "/etc/systemd/system/nwo-noc-ingress.service",
                    "/etc/systemd/system/nwo-noc-backup.service",
                    "/etc/systemd/system/nwo-noc-backup.timer",
                ]
            ).returncode
            == 0,
        )
        command(["systemctl", "restart", "nwo-noc.service"])
        passed("systemd_restart_and_verification", invoke("verify").returncode == 0)

    def db(sql):
        return (
            command(
                [
                    *compose,
                    "exec",
                    "-T",
                    "db",
                    "psql",
                    "-U",
                    "postgres",
                    "-d",
                    "nwo_noc",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-t",
                    "-A",
                ],
                data=sql.encode(),
            )
            .stdout.decode()
            .strip()
        )

    if args.extended:
        from failure_acceptance import installed_failures_and_smoke

        installed_failures_and_smoke(
            root, compose, invoke, command, browser, token, passed, db, install_seconds
        )

    passed(
        "integrated_schema",
        int(
            db(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"
            )
        )
        >= 171,
    )
    passed(
        "single_admin_and_audit",
        db("SELECT count(*) FROM users WHERE role='admin';") == "1"
        and db(
            "SELECT count(*) FROM audit_logs WHERE action='installer.admin_initialized';"
        )
        == "1",
    )
    # The stdin helper rejects repeated setup rather than resetting any account.
    private = json.loads(
        (root / "opt/nwo-noc/releases" / first / "release.json").read_text()
    )
    bootstrap = (
        root / "opt/nwo-noc/releases" / first / "scripts/host/initialize_admin.py"
    )
    duplicate = command(
        [
            *compose,
            "run",
            "--rm",
            "--no-deps",
            "--volume",
            f"{bootstrap}:/app/initialize_admin.py:ro",
            "--entrypoint",
            "python",
            "backend",
            "/app/initialize_admin.py",
        ],
        data=json.dumps(
            {
                "username": "admin",
                "email": "synthetic@example.invalid",
                "password": secrets.token_urlsafe(24),
                "installation_id": state["installation_id"],
            }
        ).encode(),
        success=False,
    )
    passed("administrator_bootstrap_one_time", duplicate.returncode != 0)
    sentinel = root / "var/lib/nwo-noc/documents" / "synthetic-preservation.txt"
    sentinel.write_text("synthetic document preservation")
    sentinel.chmod(0o600)
    os.chown(sentinel, 10001, 10001)
    # The history marker is operational data in an existing audit model, not a competing table.
    db(
        "INSERT INTO audit_logs(action,entity_type,entity_id,details,created_at) VALUES ('installer.synthetic_sentinel','installation','synthetic','{}',now());"
    )
    if args.pilot_checkpoint:
        command([*compose, 'exec', '-T', 'redis', 'redis-cli', 'SET', 'noc119:preservation', 'synthetic'])
        script = "from app.core.database import SessionLocal; from app.models.entities import Device,TelemetrySample; from datetime import datetime,timezone; db=SessionLocal(); d=Device(source='synthetic',external_id='noc119-recovery',name='Synthetic recovery device'); db.add(d); db.flush(); db.add(TelemetrySample(device_id=d.id,sampled_at=datetime.now(timezone.utc),raw_data={'synthetic_recovery':True})); db.commit(); db.close()"
        command([*compose, 'exec', '-T', 'backend', 'python', '-c', script])
    git(
        "commit",
        "--allow-empty",
        "--quiet",
        "-m",
        "Synthetic approved compatible packaging upgrade",
    )
    second, second_approval = approve([first])
    passed("upgrade_success", invoke("upgrade", second_approval).returncode == 0)
    passed("upgrade_verification", invoke("verify").returncode == 0)
    passed(
        "atomic_current_release",
        (root / "opt/nwo-noc/current").resolve().name == second,
    )
    passed(
        "document_preserved", sentinel.read_text() == "synthetic document preservation"
    )
    passed(
        "database_history_preserved",
        db(
            "SELECT count(*) FROM audit_logs WHERE action='installer.synthetic_sentinel';"
        )
        == "1",
    )
    backups = sorted((root / "var/backups/nwo-noc").glob("*.tar.age"))
    passed("encrypted_backup_exists", len(backups) == 1)
    metadata = json.loads(backups[0].with_suffix(".age.json").read_text())
    passed(
        "ciphertext_checksum",
        metadata["sha256"] == hashlib.sha256(backups[0].read_bytes()).hexdigest(),
    )
    passed(
        "no_plaintext_staging_retained",
        not list((root / "var/backups/nwo-noc").glob(".private-staging-*")),
    )
    archive = work / "restore-test.tar"
    with archive.open("xb") as target:
        archive.chmod(0o600)
        p = subprocess.run(
            ["age", "-d", "-i", identity, backups[0]],
            stdout=target,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    passed("encrypted_backup_decrypts", p.returncode == 0)
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        passed(
            "configuration_keys_release_documents_backed_up",
            all(
                v in names
                for v in [
                    "database.dump",
                    "redis.tar",
                    "configuration/runtime.env",
                    "configuration/keys/signup",
                    "release/release.json",
                    "data/documents/synthetic-preservation.txt",
                ]
            ),
        )
        dump = tar.extractfile("database.dump").read()
    restore = "restore_noc113_" + uuid4().hex[:10]
    command([*compose, "exec", "-T", "db", "createdb", "-U", "postgres", restore])
    command(
        [
            *compose,
            "exec",
            "-T",
            "db",
            "pg_restore",
            "-U",
            "postgres",
            "-d",
            restore,
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
        ],
        data=dump,
    )
    restored = (
        command(
            [
                *compose,
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "postgres",
                "-d",
                restore,
                "-t",
                "-A",
                "-c",
                "SELECT count(*) FROM audit_logs WHERE action='installer.synthetic_sentinel';",
            ]
        )
        .stdout.decode()
        .strip()
    )
    passed("isolated_restore_history", restored == "1")
    if args.extended:
        # Start the installed application against the restored database, never the source DB.
        db(f'GRANT CONNECT ON DATABASE "{restore}" TO noc_app;')
        command(
            [
                *compose,
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "postgres",
                "-d",
                restore,
                "-c",
                "GRANT USAGE ON SCHEMA public TO noc_app; GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO noc_app; GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO noc_app;",
            ]
        )
        runtime = dict(
            line.split("=", 1)
            for line in (root / "etc/nwo-noc/runtime.env").read_text().splitlines()
        )
        restored_url = runtime["DATABASE_URL"].rsplit("/", 1)[0] + "/" + restore
        script = "import asyncio,httpx; from app.main import app; from app.core.database import SessionLocal; from sqlalchemy import text; db=SessionLocal(); assert db.execute(text(\"SELECT count(*) FROM audit_logs WHERE action='installer.synthetic_sentinel'\")).scalar()==1; db.close(); exec(\"async def check():\\n async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://restored.invalid') as c:\\n  assert (await c.get('/health')).status_code==200\\n\"); asyncio.run(check())"
        command(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "-e",
                "DATABASE_URL",
                "--entrypoint",
                "python",
                "backend",
                "-c",
                script,
            ],
            env={**env, "DATABASE_URL": restored_url},
        )
        passed("restored_application_health_and_history")
    passed(
        "source_database_not_overwritten",
        db(
            "SELECT count(*) FROM audit_logs WHERE action='installer.synthetic_sentinel';"
        )
        == "1",
    )
    if args.pilot_checkpoint:
        if args.rehearsal or args.failure_stage:
            raise RuntimeError("Pilot checkpoint requires a real disposable systemd host")
        from scheduled_backup import acceptance as scheduled_backup_acceptance
        scheduled_backup_acceptance(command, invoke, passed, identity)
        from pilot_recovery import export_checkpoint
        container_ids=command([*compose,'ps','--all','--quiet']).stdout.decode().split()
        if not container_ids:raise AssertionError('Independent installed image identities missing')
        tested_images=sorted(successful_release_images|set(command(['docker','inspect','--format','{{.Image}}',*container_ids]).stdout.decode().split()))
        if not all(re.fullmatch(r'sha256:[a-f0-9]{64}',v) for v in tested_images):raise AssertionError('Invalid installed image identity')
        export_checkpoint(work, clone, first_approval, identity, backups[0], admin_secret, compose, state, tested_images, extra_images=[args.backend])
        print(json.dumps({"checks_passed": len(results), "environment": "independent-vm-checkpoint", "upgrade_and_restore": "passed", "production_authorization": False}), flush=True)
        return
    git("commit", "--allow-empty", "--quiet", "-m", "Synthetic approved release with startup failure")
    bad, bad_approval = approve([second], bad=True)
    failed = invoke("upgrade", bad_approval, success=False)
    passed("failed_upgrade_rejected", failed.returncode != 0)
    passed(
        "failed_upgrade_old_pointer_preserved",
        (root / "opt/nwo-noc/current").resolve().name == second,
    )
    recovery = json.loads((root / "var/lib/nwo-noc/recovery-required.json").read_text())
    passed(
        "postmigration_recovery_gate",
        recovery["phase"] == "migration_started" and recovery["previous"] == second,
    )
    passed(
        "failed_upgrade_verification_reports_failure",
        invoke("verify", success=False).returncode != 0,
    )
    passed(
        "no_automatic_old_release_start",
        invoke("service-start", success=False).returncode != 0,
    )
    passed(
        "no_automatic_database_restore",
        db(
            "SELECT count(*) FROM audit_logs WHERE action='installer.synthetic_sentinel';"
        )
        == "1",
    )
    passed(
        "failed_upgrade_backup_retained",
        len(list((root / "var/backups/nwo-noc").glob("*.tar.age"))) == 2,
    )
    # Bind post-install scanning to the exact containers exercised here, including
    # their synthetic revision labels. A separate rebuild is not the tested image.
    container_ids = command([*compose, "ps", "--all", "--quiet"]).stdout.decode().split()
    if not container_ids:
        raise AssertionError("Installed runtime container identities unavailable")
    tested_images = sorted(successful_release_images | set(command(
        ["docker", "inspect", "--format", "{{.Image}}", *container_ids]
    ).stdout.decode().split()))
    if not all(re.fullmatch(r"sha256:[a-f0-9]{64}", value) for value in tested_images):
        raise AssertionError("Installed runtime image identity invalid")
    (work / "runtime-images.json").write_text(json.dumps(tested_images))
    if args.rehearsal:
        invoke("service-stop")
    # Stop only this disposable installation. Preserve volumes, keys, encrypted backups and evidence.
    command([*compose, "stop"])
    if not args.rehearsal:
        command(["systemctl", "stop", "nwo-noc.service"])
        command(["systemctl", "disable", "nwo-noc.service"])
    print(
        json.dumps(
            {
                "checks_passed": len(results),
                "tested_runtime_images": tested_images,
                "checks": results,
                "os": "Ubuntu24.04",
                "environment": (
                    "userspace-container"
                    if args.rehearsal
                    else "disposable-systemd-host"
                ),
                "full_host_boot_tested": False,
                "data": "synthetic only",
                "external_integrations": "disabled; no device or provider access",
                "retained": "test databases, encrypted backups, configuration and release history",
            }
        )
    )


if __name__ == "__main__":
    main()
