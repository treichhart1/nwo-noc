#!/usr/bin/env python3
"""Approved-release packaging over the existing NOC Compose/application architecture.

No device IO, production discovery, public listeners, database downgrade or restore.
"""

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import getpass
import grp
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import time
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy import (
    Reject,
    approval,
    BASELINE,
    DATA_DIRS,
    DISABLED,
    file_hash,
    password,
    private_file,
    resources,
    safe_members,
    validate_env,
    ubuntu_candidate,
    migration_environment,
)

SOURCE = Path(__file__).resolve().parents[2]
DOCKER = ["docker", "--host=unix:///var/run/docker.sock"]
SERVICES = ["frontend", "planning-worker", "worker", "backend"]


def run(
    argv,
    *,
    data=None,
    input_file=None,
    output=None,
    timeout=1800,
    label="Command",
    check=True,
):
    environment = {
        k: v for k, v in os.environ.items() if not k.startswith(("DOCKER_", "COMPOSE_"))
    }
    if argv and argv[0] == "apt-get":
        environment.update(DEBIAN_FRONTEND="noninteractive", NEEDRESTART_MODE="l")
    result = subprocess.run(
        argv,
        input=data,
        stdin=input_file,
        stdout=output or subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=environment,
    )
    if check and result.returncode:
        raise Reject(
            f"{label} failed (exit {result.returncode}); state preserved; inspect protected service logs"
        )
    return result


def git(*args):
    return (
        run(
            ["git", "-c", f"safe.directory={SOURCE}", "-C", str(SOURCE), *args],
            label="Approved source verification",
        )
        .stdout.decode()
        .strip()
    )


def atomic(path, content, mode=0o600):
    path = Path(path)
    if path.is_symlink():
        raise Reject("Refusing to replace a symlink")
    temporary = path.with_name(path.name + ".new-" + uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    os.fchmod(fd, mode)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def write_json(path, data):
    atomic(path, json.dumps(data, indent=2) + "\n")


def read_env(path):
    return dict(
        line.split("=", 1) for line in private_file(path).decode().splitlines() if line
    )


class Host:
    def __init__(self, args):
        self.args = args
        self.root = Path(args.root)
        if (
            not self.root.is_absolute()
            or self.root.is_symlink()
            or ".." in self.root.parts
        ):
            raise Reject("Absolute non-symlink installation root required")
        if self.root != Path("/") and not args.rehearsal:
            raise Reject(
                "Alternate roots are restricted to explicit disposable-container rehearsals"
            )
        if args.rehearsal and not (
            args.controlled_test
            and Path("/.dockerenv").exists()
            and os.environ.get("NOC_INSTALLER_REHEARSAL") == "1"
        ):
            raise Reject(
                "Rehearsal requires an explicit controlled test inside a disposable Ubuntu container"
            )
        self.etc = self.path("etc/nwo-noc")
        self.lib = self.path("var/lib/nwo-noc")
        self.opt = self.path("opt/nwo-noc")
        self.logs = self.path("var/log/nwo-noc")
        self.backups = self.path("var/backups/nwo-noc")
        self.state_path = self.lib / "installation.json"
        self.guard = self.lib / "recovery-required.json"

    def path(self, value):
        return self.root / value

    def environment(self):
        if os.geteuid() != 0:
            raise Reject(
                "Use sudo/root on a disposable or explicitly approved Ubuntu host"
            )
        release = dict(
            line.strip().split("=", 1)
            for line in Path("/etc/os-release").read_text().splitlines()
            if "=" in line
        )
        if (
            release.get("ID", "").strip('"') != "ubuntu"
            or release.get("VERSION_ID", "").strip('"') != "24.04"
        ):
            raise Reject("Ubuntu Server24.04LTS required; no host changes made")
        if not self.args.rehearsal and not Path("/run/systemd/system").is_dir():
            raise Reject(
                "Active systemd Ubuntu host required; containers must use explicit controlled rehearsal mode"
            )
        if os.uname().machine != "x86_64":
            raise Reject(
                "Only tested Linux amd64 packaging is supported; other architectures need acceptance"
            )

    def state(self):
        return json.loads(private_file(self.state_path))

    def event(self, name, **fields):
        value = {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": name,
            **fields,
        }
        fd = os.open(
            self.logs / "installer.jsonl",
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "a") as stream:
            stream.write(json.dumps(value) + "\n")
        print(json.dumps(value))

    def approved(self):
        a = approval(
            self.args.approval, controlled=self.args.controlled_test, source=SOURCE
        )
        if (
            git("status", "--porcelain", "--untracked-files=all")
            or git("rev-parse", "HEAD") != a["commit"]
            or git("rev-parse", "HEAD^{tree}") != a["tree"]
        ):
            raise Reject(
                "Clean checkout of the exact approved commit/tree required; arbitrary branches are not releases"
            )
        git("merge-base", "--is-ancestor", BASELINE, a["commit"])
        # Reuse the complete integration/migration graph audit; no omitted feature subset.
        run(
            ["python3", str(SOURCE / "scripts/release/integration_manifest.py")],
            label="Complete dependency/migration audit",
        )
        return a

    def capacity(self, *, extra=0):
        probe = self.root
        while not probe.exists():
            probe = probe.parent
        memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        resources(
            cpus=os.cpu_count() or 1,
            memory=memory,
            disk=shutil.disk_usage(probe).free - extra,
            rehearsal=self.args.rehearsal,
        )

    def dependencies(self):
        required = ["python3", "ca-certificates", "python3-yaml", "age", "logrotate", "openssl"]
        missing = [
            p
            for p in required
            if run(["dpkg-query", "-W", "-f=${Status}", p], check=False)
            .stdout.decode()
            .strip()
            != "install ok installed"
        ]
        if not shutil.which("docker"):
            missing += ["docker.io", "docker-compose-v2"]
        if missing:
            run(["apt-get", "-o", "APT::Update::Error-Mode=any", "update"], label="Ubuntu repository refresh")
            for package in missing:
                candidate = run(["apt-cache", "policy", package]).stdout.decode()
                ubuntu_candidate(candidate)
                if "Candidate: (none)" in candidate or "Candidate:" not in candidate:
                    raise Reject(
                        "Supported Ubuntu package unavailable; enable the official24.04 repositories"
                    )
            run(
                ["apt-get", "install", "-y", "--no-install-recommends", *missing],
                label="Supported system dependency installation",
            )
        if run([*DOCKER, "info"], check=False).returncode and not self.args.rehearsal:
            run(
                ["systemctl", "start", "docker.service"],
                label="Start required local Docker service",
            )
        # Respect an existing compatible Docker installation; never replace its config.
        version = (
            run(
                [*DOCKER, "version", "--format", "{{.Server.Version}}"],
                label="Local Docker daemon",
            )
            .stdout.decode()
            .strip()
        )
        compose = (
            run([*DOCKER, "compose", "version", "--short"], label="Compose plugin")
            .stdout.decode()
            .strip()
            .lstrip("v")
        )
        if int(version.split(".")[0]) < 24 or tuple(
            int(v) for v in compose.split(".")[:2]
        ) < (2, 24):
            raise Reject(
                "Docker>=24 and Compose>=2.24 required; existing installation left unchanged"
            )

    def image(self, ref):
        if run([*DOCKER, "image", "inspect", ref], check=False).returncode:
            # Only immutable artifact transport is retried. Application/migration
            # failures retain their original fail-closed behavior.
            for attempt in range(3):
                try:
                    pulled = run(
                        [*DOCKER, "pull", ref],
                        check=False,
                        timeout=600,
                        label="Approved immutable artifact download",
                    )
                except subprocess.TimeoutExpired:
                    pulled = None
                if pulled is not None and pulled.returncode == 0:
                    break
                if attempt == 2:
                    raise Reject(
                        "Approved immutable artifact download failed after 3 bounded attempts; "
                        "check registry connectivity/rate limits and protected Docker logs. "
                        "Existing application data is preserved."
                    )
                time.sleep(2 ** (attempt + 1))
        return json.loads(run([*DOCKER, "image", "inspect", ref]).stdout)[0]

    def images(self, a):
        expected = [("backend", "10001:10001"), ("frontend", "101:101")]
        approved_postgres = a["images"].get("postgres")
        if approved_postgres:
            expected.append(("postgres", ""))  # Vendor entrypoint drops root through patched gosu.
        for kind, user in expected:
            info = self.image(a["images"][kind])
            if (
                info["Config"]
                .get("Labels", {})
                .get("org.opencontainers.image.revision")
                != a["commit"]
                # Docker29 omits an empty image User; absence has the same OCI
                # default as "". Nonempty application UIDs remain mandatory,
                # and malformed explicit null/other identities still fail.
                or info["Config"].get("User", "") != user
            ):
                revision = info["Config"].get("Labels", {}).get("org.opencontainers.image.revision")
                actual_user = info["Config"].get("User")
                # Whitelist only public commit hashes/numeric identities; never
                # serialize arbitrary image configuration or credential metadata.
                safe_revision = revision if isinstance(revision, str) and re.fullmatch(r"[a-f0-9]{40}", revision) else "<missing/invalid>"
                safe_user = actual_user if isinstance(actual_user, str) and re.fullmatch(r"(?:[0-9]+(?::[0-9]+)?)?", actual_user) else "<missing/invalid>"
                raise Reject(
                    f"Approved {kind} image must match source revision and unprivileged runtime UID; "
                    f"expected_revision={a['commit']} observed_revision={safe_revision} "
                    f"expected_uid={user!r} observed_uid={safe_user!r}"
                )
        locks = json.loads(git("show", a["commit"] + ":deploy/image-lock.json"))[
            "images"
        ]
        for key, tag in (
            ("postgres", "postgres:16-alpine"),
            ("redis", "redis:7-alpine"),
        ):
            if key == "postgres" and approved_postgres:
                continue
            if key == "redis" and a["images"].get(key) and a["images"][key] != locks[tag]:
                raise Reject("Approved Redis image must match the exact source lock")
            a["images"][key] = locks[tag]
            self.image(locks[tag])

    def prepare_ingress(self):
        directory = self.path("run/nwo-noc-ingress")
        directory.mkdir(parents=True, exist_ok=True, mode=0o770)
        st = directory.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o007:
            raise Reject("Private owned ingress socket directory required")
        os.chown(directory, 0, 10001)
        os.chmod(directory, 0o770)

    def clear_stopped_frontend_socket(self):
        path = self.path("run/nwo-noc-ingress/frontend.sock")
        try:
            value = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(value.st_mode) or value.st_uid not in (0, 101):
            raise Reject(
                "Unexpected ingress entry retained; only the stopped frontend's owned Unix socket may be replaced"
            )
        path.unlink()

    def start_ingress(self, release, state):
        if not self.args.rehearsal:
            run(
                ["systemctl", "start", "nwo-noc-ingress.service"],
                label="Start unprivileged private ingress",
            )
        else:
            self.stop_ingress()
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(release / "scripts/host/ingress.py"),
                    "--socket",
                    str(self.path("run/nwo-noc-ingress/frontend.sock")),
                    "--port",
                    str(state["port"]),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            write_json(
                self.lib / "ingress-process.json",
                {
                    "pid": process.pid,
                    "script": str(release / "scripts/host/ingress.py"),
                },
            )
        for attempt in range(30):
            try:
                with socket.create_connection(("127.0.0.1", state["port"]), timeout=1):
                    return
            except OSError:
                time.sleep(0.2)
        raise Reject("Private ingress did not bind; inspect its protected service logs")

    def stop_ingress(self):
        if not self.args.rehearsal:
            run(
                ["systemctl", "stop", "nwo-noc-ingress.service"],
                label="Quiesce private ingress",
            )
        else:
            path = self.lib / "ingress-process.json"
            if path.exists():
                record = json.loads(private_file(path))
                process = Path("/proc") / str(record["pid"]) / "cmdline"
                if process.exists() and record[
                    "script"
                ].encode() in process.read_bytes().split(b"\x00"):
                    os.kill(record["pid"], signal.SIGTERM)
                path.unlink()

    def account(self):
        if self.args.rehearsal:
            return
        try:
            account = pwd.getpwnam("nwo-noc")
            if set(os.getgrouplist("nwo-noc", account.pw_gid)) != {10001}:
                raise Reject(
                    "Existing nwo-noc account has unrelated supplementary privileges; no groups changed"
                )
            if (
                account.pw_uid != 10001
                or account.pw_gid != 10001
                or account.pw_shell
                not in (
                    "/usr/sbin/nologin",
                    "/bin/false",
                )
            ):
                raise Reject(
                    "Existing nwo-noc account is incompatible; no account altered"
                )
        except KeyError:
            try:
                pwd.getpwuid(10001)
            except KeyError:
                try:
                    group = grp.getgrgid(10001)
                    if group.gr_name != "nwo-noc":
                        raise Reject(
                            "GID10001 belongs to an unrelated group; no group changed"
                        )
                except KeyError:
                    run(
                        ["groupadd", "--gid", "10001", "nwo-noc"],
                        label="Dedicated system group",
                    )
                run(
                    [
                        "useradd",
                        "--system",
                        "--no-create-home",
                        "--uid",
                        "10001",
                        "--gid",
                        "10001",
                        "--home-dir",
                        "/var/lib/nwo-noc",
                        "--shell",
                        "/usr/sbin/nologin",
                        "nwo-noc",
                    ],
                    label="Dedicated unprivileged account",
                )
            else:
                raise Reject(
                    "UID10001 already belongs to an unrelated account; operator review required"
                )

    def directories(self):
        for p in (
            self.opt,
            self.etc,
            self.lib,
            self.logs,
            self.backups,
            self.opt / "releases",
        ):
            p.mkdir(parents=True, mode=0o700, exist_ok=True)
            os.chmod(p, 0o700)
        os.chmod(self.opt, 0o755)
        os.chmod(self.opt / "releases", 0o755)
        for name in DATA_DIRS:
            p = self.lib / name
            p.mkdir(mode=0o700)
            os.chown(p, 10001, 10001)

    def configure(self):
        app = secrets.token_hex(24)
        owner = secrets.token_hex(24)
        admin = secrets.token_hex(24)
        values = {k: "false" for k in DISABLED}
        values.update(
            PLANNING_WORKER_ENABLED="true", MAINTENANCE_SCHEDULER_ENABLED="true"
        )
        values.update(
            APP_ENV="production",
            OFFLINE_MODE="true",
            SECRET_KEY=secrets.token_hex(32),
            ADMIN_PASSWORD=secrets.token_hex(32),
            ADMIN_USERNAME=self.args.admin_username,
            ADMIN_EMAIL=self.args.admin_email,
            DATABASE_URL=f"postgresql+psycopg://noc_app:{app}@db:5432/nwo_noc",
            REDIS_URL="redis://redis:6379/0",
            SIGNUP_TURNSTILE_MODE="disabled",
            SIGNUP_GEOCODER="disabled",
            RF_EXPLANATION_PROVIDER="disabled",
            CORS_ORIGINS=f"{'https://localhost' if self.args.tls_cert else 'http://127.0.0.1'}:{self.args.port}",
            FIELD_PUBLIC_BASE_URL="",
        )
        keys = self.etc / "keys"
        keys.mkdir(mode=0o700)
        for name, setting in (
            ("signup", "SIGNUP_PII_KEY_FILE"),
            ("notifications", "NOTIFICATIONS_VAULT_KEY_FILE"),
            ("fiber", "FIBER_DESIGNER_KEY_FILE"),
            ("status", "CUSTOMER_STATUS_RATE_KEY_FILE"),
        ):
            atomic(
                keys / name,
                base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
                0o400,
            )
            os.chown(keys / name, 10001, 10001)
            values[setting] = "/run/nwo-noc-keys/" + name
        os.chown(keys, 10001, 10001)
        os.chmod(keys, 0o500)
        validate_env(values)
        atomic(
            self.etc / "runtime.env", "".join(f"{k}={v}\n" for k, v in values.items())
        )
        atomic(
            self.etc / "migration.env",
            f"DATABASE_URL=postgresql+psycopg://noc_owner:{owner}@db:5432/nwo_noc\n",
        )
        atomic(self.etc / "db-owner-password", owner)
        atomic(self.etc / "app-password", app)
        atomic(self.etc / "db-bootstrap-password", admin)
        atomic(
            self.etc / "service.env",
            f"NOC_LISTEN_PORT={self.args.port}\n",
        )
        if self.args.backup_recipient:
            self.recipient(self.args.backup_recipient)
            atomic(self.etc / "backup-recipient", self.args.backup_recipient + "\n")
        if bool(self.args.tls_cert) != bool(self.args.tls_key):
            raise Reject("Both TLS certificate and private key are required")
        if self.args.tls_cert:
            # Preserve root-only parent directories; only nginx's container UID reads the mounted key.
            for source, name in (
                (self.args.tls_cert, "tls-cert.pem"),
                (self.args.tls_key, "tls-key.pem"),
            ):
                data = private_file(source)
                atomic(self.etc / name, data.decode(), 0o400)
                os.chown(self.etc / name, 101, 101)
            if self.args.tls_ca:
                atomic(self.etc / "tls-ca.pem", Path(self.args.tls_ca).read_text())

    def stage(self, a):
        target = self.opt / "releases" / a["commit"]
        if target.exists():
            raise Reject(
                "Release directory already exists; preserve it and review the previous attempt"
            )
        target.mkdir(mode=0o755)
        os.chmod(target, 0o755)
        archive = run(
            [
                "git",
                "-c",
                f"safe.directory={SOURCE}",
                "-C",
                str(SOURCE),
                "archive",
                a["commit"],
            ],
            label="Approved source archive",
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(target, members=safe_members(tar), filter="data")
        if a.get("purpose") == "release":
            from release_trust import capture
            capture(a, self.args.approval, target / "release-trust")
        inventory = {
            p.relative_to(target).as_posix(): file_hash(p)
            for p in target.rglob("*")
            if p.is_file()
        }
        write_json(target / "release.json", {"approval": a, "files": inventory})
        return target

    def compose_config(self, release, a, state):
        import yaml

        base = yaml.safe_load((release / "deploy/compose.isolated.yml").read_text())
        base.pop("name", None)
        base.pop("x-app", None)
        app_mounts = {
            "monitoring_spool": "monitoring",
            "maintenance_documents": "documents",
            "signup_private": "signup",
            "planning_terrain": "planning",
        }
        for name, svc in base["services"].items():
            svc["logging"] = {
                "driver": "json-file",
                "options": {"max-size": "10m", "max-file": "3"},
            }
            svc["pids_limit"] = 512
            if name != "migrate":
                svc["restart"] = "on-failure:3"
            if name in (
                "migrate",
                "backend",
                "worker",
                "planning-worker",
                "fiber-sync-worker",
            ):
                svc["image"] = a["images"]["backend"]
                svc["env_file"] = [str(self.etc / "runtime.env")]
                svc["environment"] = {}
                if name in ("planning-worker", "fiber-sync-worker"):
                    svc["environment"] = {
                        (
                            "PLANNING_WORKER_ENABLED"
                            if name == "planning-worker"
                            else "FIBER_DESIGNER_WORKER_ENABLED"
                        ): "true"
                    }
                svc["mem_limit"] = "1g"
                svc["volumes"] = [
                    f"{self.lib / directory}:/var/lib/nwo-noc/{directory}"
                    for directory in DATA_DIRS
                ] + [f"{self.etc / 'keys'}:/run/nwo-noc-keys:ro"]
            if name == "planning-worker":
                svc["volumes"] = [f"{self.lib / 'planning'}:/var/lib/nwo-noc/planning"]
            if name == "fiber-sync-worker":
                svc["volumes"] = [
                    f"{self.lib / 'documents'}:/var/lib/nwo-noc/documents",
                    f"{self.etc / 'keys/fiber'}:/run/nwo-noc-keys/fiber:ro",
                ]
            if name == "migrate":
                svc["volumes"] = []
                svc["env_file"].append(str(self.etc / "migration.env"))
                svc["profiles"] = ["migration"]
            if name == "backend":
                svc["command"] = [
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8000",
                    "--no-access-log",
                ]
                svc["depends_on"] = {
                    "db": {"condition": "service_healthy"},
                    "redis": {"condition": "service_healthy"},
                }
            if name == "db":
                svc["image"] = a["images"]["postgres"]
                svc["environment"] = {
                    "POSTGRES_DB": "nwo_noc",
                    "POSTGRES_USER": "postgres",
                    "POSTGRES_PASSWORD_FILE": "/run/secrets/bootstrap",
                }
                svc["volumes"].append(
                    f"{self.etc / 'db-bootstrap-password'}:/run/secrets/bootstrap:ro"
                )
                svc["healthcheck"]["test"] = [
                    "CMD-SHELL",
                    "pg_isready -h 127.0.0.1 -U postgres -d nwo_noc",
                ]
                svc["mem_limit"] = "2g"
            if name == "redis":
                svc["image"] = a["images"]["redis"]
                svc["mem_limit"] = "256m"
            if name == "frontend":
                svc["group_add"] = ["10001"]
                svc["image"] = a["images"]["frontend"]
                svc["command"] = [
                    "sh",
                    "-ec",
                    "if [ -S /ingress/frontend.sock ]; then rm /ingress/frontend.sock; elif [ -e /ingress/frontend.sock ]; then exit 1; fi; exec nginx -g 'daemon off;'",
                ]
                svc.pop("ports", None)
                svc["volumes"] = [
                    f"{release / 'nginx.conf'}:/etc/nginx/conf.d/default.conf:ro",
                    f"{release / 'nginx-main.conf'}:/etc/nginx/nginx.conf:ro",
                    f"{self.path('run/nwo-noc-ingress')}:/ingress",
                ]
                svc["mem_limit"] = "128m"
                svc["healthcheck"] = {
                    "test": [
                        "CMD",
                        "wget",
                        "-Y",
                        "off",
                        "-qO-",
                        "http://127.0.0.1:8080/",
                    ],
                    "interval": "3s",
                    "timeout": "5s",
                    "retries": 10,
                }
                if state["tls"]:
                    svc["volumes"] += [
                        f"{self.etc / 'tls-cert.pem'}:/etc/nginx/tls/cert.pem:ro",
                        f"{self.etc / 'tls-key.pem'}:/etc/nginx/tls/key.pem:ro",
                    ]
                    svc["healthcheck"] = {
                        "test": ["CMD", "nginx", "-t"],
                        "interval": "15s",
                        "timeout": "5s",
                        "retries": 3,
                    }
        base["volumes"] = {
            "database": {"name": state["project"] + "-database"},
            "redis_data": {"name": state["project"] + "-redis"},
        }
        base["networks"] = {"isolated": {"internal": True}}
        nginx = (
            (release / "deploy/host/nginx.conf.template")
            .read_text()
            .replace("__TLS__", " ssl" if state["tls"] else "")
            .replace(
                "__CERTIFICATES__",
                (
                    "ssl_certificate /etc/nginx/tls/cert.pem; ssl_certificate_key /etc/nginx/tls/key.pem; ssl_protocols TLSv1.2 TLSv1.3;"
                    if state["tls"]
                    else ""
                ),
            )
        )
        atomic(release / "nginx.conf", nginx, 0o444)
        atomic(
            release / "nginx-main.conf",
            (release / "frontend/nginx-main.conf")
            .read_text()
            .replace("worker_processes auto;", "worker_processes 2;"),
            0o444,
        )
        atomic(release / "compose.json", json.dumps(base, indent=2) + "\n")

    def compose(self, release, state, *args, **kwargs):
        return run(
            [
                *DOCKER,
                "compose",
                "--env-file",
                "/dev/null",
                "-p",
                state["project"],
                "-f",
                str(release / "compose.json"),
                *args,
            ],
            label="Private NOC Compose "
            + str(args[0])
            + " "
            + ",".join(
                v
                for v in args
                if v in ("backend", "worker", "frontend", "migrate", "db", "redis")
            ),
            **kwargs,
        )

    def image_preflight(self, release, a, state):
        self.prepare_ingress()
        self.compose(release, state, "config", "--quiet")
        script = "from alembic.config import Config; from alembic.script import ScriptDirectory; from app.core.config import settings; print(','.join(ScriptDirectory.from_config(Config('alembic.ini')).get_heads()))"
        result = run(
            [
                *DOCKER,
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--env-file",
                str(self.etc / "runtime.env"),
                "--entrypoint",
                "python",
                a["images"]["backend"],
                "-c",
                script,
            ],
            label="Application configuration and migration preflight",
        )
        if result.stdout.decode().strip() != a["migration_head"]:
            raise Reject("Approved image migration head differs from release manifest")
        run(
            [
                *DOCKER,
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--entrypoint",
                "sh",
                a["images"]["frontend"],
                "-c",
                "test -r /usr/share/nginx/html/index.html && test -d /usr/share/nginx/html/assets",
            ],
            label="Readable complete frontend artifact",
        )
        self.compose(
            release,
            state,
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "nginx",
            "frontend",
            "-t",
        )

    def psql(self, release, state, sql):
        return self.compose(
            release,
            state,
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
            data=sql.encode(),
        )

    def role_sql(self, release):
        sys.path.insert(0, str(release / "scripts/release"))
        from bootstrap_runtime_db import runtime_role_sql

        return runtime_role_sql(
            private_file(self.etc / "app-password").decode(), owner="noc_owner"
        )

    def initialize_database(self, release, state):
        count = (
            self.compose(
                release,
                state,
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "postgres",
                "-d",
                "nwo_noc",
                "-t",
                "-A",
                "-c",
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'",
            )
            .stdout.decode()
            .strip()
        )
        if count != "0":
            raise Reject(
                "Nonempty database detected; no ownership, migrations or data changed"
            )
        owner = private_file(self.etc / "db-owner-password").decode()
        if not re.fullmatch("[a-f0-9]{48}", owner):
            raise Reject("Generated database owner secret required")
        sql = f"CREATE ROLE noc_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{owner}'; ALTER DATABASE nwo_noc OWNER TO noc_owner; ALTER SCHEMA public OWNER TO noc_owner; REVOKE CREATE ON SCHEMA public FROM PUBLIC;"
        self.psql(release, state, sql)

    def migrate(self, release, state):
        self.compose(release, state, "run", "--rm", "--no-deps", "migrate")
        self.psql(release, state, self.role_sql(release))
        self.compose(
            release,
            state,
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "alembic",
            "migrate",
            "check",
        )

    def register(self, release):
        if self.args.rehearsal:
            return
        for name in (
            "nwo-noc.service",
            "nwo-noc-ingress.service",
            "nwo-noc-backup.service",
            "nwo-noc-backup.timer",
        ):
            destination = self.path("etc/systemd/system") / name
            content = (release / "deploy/host" / name).read_text()
            if destination.exists() and destination.read_text() != content:
                raise Reject(
                    "Existing unit differs from managed template; no unrelated unit overwritten"
                )
            atomic(destination, content, 0o644)
        logrotate = self.path("etc/logrotate.d/nwo-noc")
        content = (release / "deploy/host/logrotate.conf").read_text()
        if logrotate.exists() and logrotate.read_text() != content:
            raise Reject(
                "Existing unrelated log retention file retained; operator review required"
            )
        atomic(logrotate, content, 0o644)
        run(["systemctl", "daemon-reload"], label="Systemd unit registration")
        run(
            ["systemctl", "enable", "nwo-noc.service"],
            label="Boot service registration",
        )

    def switch(self, release):
        pending = self.opt / (".current-" + uuid4().hex)
        pending.symlink_to(release)
        os.replace(pending, self.opt / "current")

    def start(self, release, state):
        self.compose(release, state, "stop", "frontend")
        self.prepare_ingress()
        self.clear_stopped_frontend_socket()

        self.compose(
            release,
            state,
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            # Artifact test purpose must not shorten the actual production
            # startup contract on independently booted, resource-limited hosts.
            "180",
            "db",
            "redis",
            "backend",
            "worker",
            "frontend",
            "planning-worker",
        )

    def stop_apps(self, release, state):
        self.compose(release, state, "stop", *SERVICES)

    def install(self):
        self.environment()
        a = self.approved()
        self.capacity()
        for path in (self.etc, self.lib, self.opt, self.logs, self.backups):
            if path.is_symlink() or path.exists():
                raise Reject(
                    "Existing installation/path detected; fresh install never overwrites or upgrades. Use verify/upgrade or documented failure recovery"
                )
        if not self.args.rehearsal:
            for name in (
                "nwo-noc.service",
                "nwo-noc-ingress.service",
                "nwo-noc-backup.service",
                "nwo-noc-backup.timer",
            ):
                if self.path("etc/systemd/system").joinpath(name).exists():
                    raise Reject(
                        "Existing managed-name systemd unit requires operator review; no overwrite"
                    )
            try:
                occupant = pwd.getpwuid(10001)
                if occupant.pw_name != "nwo-noc":
                    raise Reject(
                        "UID10001 belongs to an unrelated account; no account changed"
                    )
            except KeyError:
                pass
            try:
                group = grp.getgrgid(10001)
                if group.gr_name != "nwo-noc":
                    raise Reject(
                        "GID10001 belongs to an unrelated group; no group altered"
                    )
            except KeyError:
                pass
        if bool(self.args.tls_cert) != bool(self.args.tls_key):
            raise Reject("Both TLS certificate and key required before installation")
        if self.args.tls_cert:
            private_file(self.args.tls_cert)
            private_file(self.args.tls_key)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.args.tls_cert, self.args.tls_key)
        if self.args.backup_recipient:
            self.recipient(self.args.backup_recipient)
        if not re.fullmatch(
            r"[a-zA-Z0-9_.-]{1,80}", self.args.admin_username
        ) or not re.fullmatch(
            r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9.-]+", self.args.admin_email
        ):
            raise Reject("Valid administrator username/email required")
        if not 1024 <= self.args.port <= 65535:
            raise Reject("Unprivileged loopback port1024–65535 required")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", self.args.port))
        if self.args.password_stdin:
            secret = password(sys.stdin.readline(1024).rstrip("\n"))
        elif sys.stdin.isatty():
            secret = password(
                getpass.getpass("New administrator password (never logged): ")
            )
            if secret != getpass.getpass("Confirm administrator password: "):
                raise Reject("Administrator passwords differ")
        else:
            raise Reject(
                "Interactive secure password prompt required, or explicitly pass --password-stdin from a protected credential broker"
            )
        self.dependencies()
        self.images(a)
        self.account()
        self.directories()
        self.configure()
        state = {
            "format": 1,
            "installation_id": uuid4().hex,
            "project": "noc113-" + uuid4().hex[:12],
            "commit": a["commit"],
            "migration_head": a["migration_head"],
            "port": self.args.port,
            "tls": bool(self.args.tls_cert),
            "status": "installing",
            "purpose": a["purpose"],
        }
        for name in (state["project"] + "-database", state["project"] + "-redis"):
            if run([*DOCKER, "volume", "inspect", name], check=False).returncode == 0:
                raise Reject(
                    "Existing data volume detected; refusing fresh database initialization"
                )
        write_json(self.state_path, state)
        try:
            release = self.stage(a)
            self.compose_config(release, a, state)
            self.image_preflight(release, a, state)
            self.compose(release, state, "up", "-d", "--wait", "db", "redis")
            self.initialize_database(release, state)
            self.migrate(release, state)
            body = json.dumps(
                {
                    "username": self.args.admin_username,
                    "email": self.args.admin_email,
                    "password": secret,
                    "installation_id": state["installation_id"],
                }
            ).encode()
            self.compose(
                release,
                state,
                "run",
                "--rm",
                "--no-deps",
                "--volume",
                f"{release / 'scripts/host/initialize_admin.py'}:/app/initialize_admin.py:ro",
                "--entrypoint",
                "python",
                "backend",
                "/app/initialize_admin.py",
                data=body,
            )
            del secret, body
            self.start(release, state)
            self.switch(release)
            self.register(release)
            state["status"] = "ready"
            write_json(self.state_path, state)
            if not self.args.rehearsal:
                run(
                    [
                        "systemctl",
                        "start",
                        "nwo-noc.service",
                        "nwo-noc-ingress.service",
                    ],
                    label="Systemd managed startup",
                )
            self.start_ingress(release, state)
            if self.verify():
                raise Reject(
                    "Installation verification failed; preserve state for diagnosis"
                )
            self.event("installation_completed", commit=a["commit"])
        except Exception:
            state["status"] = "installation_failed"
            write_json(self.state_path, state)
            self.event(
                "installation_failed",
                commit=a["commit"],
                recovery="preserve_database_and_configuration",
            )
            raise

    def recipient(self, value=None):
        value = value or private_file(self.etc / "backup-recipient").decode().strip()
        if not re.fullmatch(r"age1[0-9a-z]{58}", value):
            raise Reject(
                "Configured age X25519 public backup recipient required; no upgrade without encrypted backup"
            )
        return value

    def backup_budget(self, release, state):
        value = (
            self.compose(
                release,
                state,
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "postgres",
                "-d",
                "nwo_noc",
                "-t",
                "-A",
                "-c",
                "SELECT pg_database_size(current_database())",
            )
            .stdout.decode()
            .strip()
        )
        if not re.fullmatch(r"[0-9]{1,20}", value):
            raise Reject("Could not measure the dedicated database backup footprint")
        files = sum(
            p.stat().st_size
            for p in self.lib.rglob("*")
            if p.is_file() and not p.is_symlink()
        )
        return 2 * (int(value) + files)

    def backup(self, release=None, state=None):
        state = state or self.state()
        release = release or self.opt / "releases" / state["commit"]
        if state.get("purpose") == "release":
            from release_trust import fingerprint, verify
            verify(release / "release-trust", fingerprint(private_file(Path("/etc/nwo-noc-release.pub"))))
        recipient = self.recipient()
        self.capacity(extra=self.backup_budget(release, state))
        # Caller has stopped all application writers. Stop Redis before copying its durable volume.
        self.compose(release, state, "exec", "-T", "redis", "redis-cli", "SAVE")
        self.compose(release, state, "stop", "redis")
        output = self.backups / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
            + uuid4().hex
            + ".tar.age"
        )
        with tempfile.TemporaryDirectory(
            prefix=".private-staging-", dir=self.backups
        ) as temp:
            temp = Path(temp)
            dump = temp / "database.dump"
            with open(dump, "xb") as stream:
                os.chmod(dump, 0o600)
                self.compose(
                    release,
                    state,
                    "exec",
                    "-T",
                    "db",
                    "pg_dump",
                    "-U",
                    "postgres",
                    "-d",
                    "nwo_noc",
                    "-Fc",
                    "--no-owner",
                    "--no-privileges",
                    output=stream,
                )
            with open(dump, "rb") as stream:
                self.compose(
                    release,
                    state,
                    "exec",
                    "-T",
                    "db",
                    "pg_restore",
                    "--list",
                    input_file=stream,
                )
            redis_dump = temp / "redis.tar"
            metadata = json.loads((release / "release.json").read_text())
            image = metadata["approval"]["images"]["backend"]
            script = "import tarfile,sys; t=tarfile.open(fileobj=sys.stdout.buffer,mode='w|'); t.add('/volume',arcname='redis',recursive=True); t.close()"
            with open(redis_dump, "xb") as stream:
                os.chmod(redis_dump, 0o600)
                run(
                    [
                        *DOCKER,
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--read-only",
                        "--user",
                        "0:0",
                        "--mount",
                        f"type=volume,src={state['project']}-redis,dst=/volume,readonly",
                        "--entrypoint",
                        "python",
                        image,
                        "-c",
                        script,
                    ],
                    output=stream,
                    label="Durable Redis archive",
                )
            # Encryption subprocess receives only public recipient/path in argv. No private key is installed.
            with open(output, "xb") as encrypted:
                os.chmod(output, 0o600)
                proc = subprocess.Popen(
                    ["/usr/bin/age", "-r", recipient],
                    stdin=subprocess.PIPE,
                    stdout=encrypted,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    with tarfile.open(fileobj=proc.stdin, mode="w|") as tar:
                        tar.add(dump, arcname="database.dump")
                        tar.add(redis_dump, arcname="redis.tar")
                        tar.add(self.etc, arcname="configuration", recursive=True)
                        tar.add(self.state_path, arcname="installation.json")
                        tar.add(release, arcname="release", recursive=True)
                        for name in DATA_DIRS:
                            tar.add(
                                self.lib / name, arcname="data/" + name, recursive=True
                            )
                    proc.stdin.close()
                    if proc.wait(timeout=1800) != 0:
                        raise Reject(
                            "Backup encryption failed; partial ciphertext retained and upgrade blocked"
                        )
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
            if output.stat().st_size < 256:
                raise Reject("Encrypted backup is incomplete; upgrade blocked")
            manifest = {
                "format": 1,
                "ciphertext": output.name,
                "sha256": file_hash(output),
                "source_commit": state["commit"],
                "migration_head": state["migration_head"],
                "database_archive_list_verified": True,
                "restore_tested": False,
            }
            write_json(output.with_suffix(output.suffix + ".json"), manifest)
            if file_hash(output) != manifest["sha256"]:
                raise Reject("Encrypted backup checksum mismatch")
        self.event(
            "encrypted_backup_completed",
            file=output.name,
            source_commit=state["commit"],
            restore_tested=False,
        )
        return output

    def upgrade(self):
        self.environment()
        old = self.state()
        if self.guard.exists() or old["status"] != "ready":
            raise Reject("Unresolved installation/recovery state; upgrade forbidden")
        if self.verify():
            raise Reject(
                "Current installation must pass verification before upgrade staging"
            )
        a = self.approved()
        if a["commit"] == old["commit"]:
            self.event("upgrade_already_current", commit=a["commit"])
            return
        if old["commit"] not in a.get("upgrade_from", []):
            raise Reject("Current release is not an explicitly approved upgrade source")
        git("merge-base", "--is-ancestor", old["commit"], a["commit"])
        self.capacity(
            extra=self.backup_budget(self.opt / "releases" / old["commit"], old)
        )
        self.recipient()
        self.dependencies()
        self.images(a)
        validate_env(read_env(self.etc / "runtime.env"))
        migration_environment(
            read_env(self.etc / "migration.env"),
            private_file(self.etc / "db-owner-password").decode(),
        )
        release = self.stage(a)
        previous = self.opt / "releases" / old["commit"]
        for name in (
            "nwo-noc.service",
            "nwo-noc-ingress.service",
            "nwo-noc-backup.service",
            "nwo-noc-backup.timer",
            "logrotate.conf",
        ):
            if (previous / "deploy/host" / name).read_bytes() != (
                release / "deploy/host" / name
            ).read_bytes():
                raise Reject(
                    "Unit/retention protocol change requires an explicit supported migration plan"
                )
        self.compose_config(release, a, old)
        self.image_preflight(release, a, old)
        previous = self.opt / "releases" / old["commit"]
        migration_started = False
        write_json(self.guard, {"phase": "backup", "target": a["commit"]})
        try:
            if not self.args.rehearsal:
                run(
                    ["systemctl", "stop", "nwo-noc.service"],
                    label="Quiesce managed service before upgrade",
                )
            self.stop_ingress()
            self.stop_apps(previous, old)
            self.compose(previous, old, "up", "-d", "--wait", "db", "redis")
            backup = self.backup(previous, old)
            migration_started = True
            write_json(
                self.guard,
                {
                    "phase": "migration_started",
                    "target": a["commit"],
                    "backup": backup.name,
                    "previous": old["commit"],
                },
            )
            self.migrate(release, old)
            candidate = {
                **old,
                "commit": a["commit"],
                "migration_head": a["migration_head"],
            }
            self.start(release, candidate)
            # Health and database checks before atomic code switch. No automatic DB rollback.
            if self.verify(release=release, state=candidate, service=False):
                raise Reject("Candidate verification failed")
            self.switch(release)
            write_json(self.state_path, candidate)
            self.guard.unlink()
            if not self.args.rehearsal:
                run(
                    [
                        "systemctl",
                        "start",
                        "nwo-noc.service",
                        "nwo-noc-ingress.service",
                    ],
                    label="Restart managed upgraded release",
                )
            self.start_ingress(release, candidate)
            if self.verify():
                raise Reject(
                    "Post-switch ingress/service verification failed; recovery gate preserved"
                )
            self.event(
                "upgrade_completed",
                previous=old["commit"],
                commit=a["commit"],
                backup=backup.name,
            )
        except Exception:
            if migration_started and not self.guard.exists():
                write_json(
                    self.guard,
                    {
                        "phase": "migration_started",
                        "target": a["commit"],
                        "backup": backup.name,
                        "previous": old["commit"],
                        "failure": "post_switch_service_start",
                    },
                )
            self.stop_ingress()
            self.stop_apps(release, old)
            if not migration_started:
                self.guard.unlink(missing_ok=True)
            self.event(
                "upgrade_failed",
                target=a["commit"],
                migration_started=migration_started,
                recovery="manual_matching_release_and_isolated_restore; never overwrite newer data",
            )
            raise

    def verify(self, *, release=None, state=None, service=True):
        checks = []

        def check(name, call):
            try:
                value = call()
                if value is False:
                    raise Reject("Check rejected")
                checks.append({"check": name, "status": "PASSED"})
            except Exception:
                checks.append(
                    {
                        "check": name,
                        "status": "FAILED",
                        "action": "See VERIFY.md/TROUBLESHOOTING.md; preserve state",
                    }
                )

        check("ubuntu24_04_amd64_root", self.environment)
        try:
            state = state or self.state()
            release = release or self.opt / "releases" / state["commit"]
        except Exception:
            print(
                json.dumps(
                    {
                        "checks": checks
                        + [{"check": "installation_state", "status": "FAILED"}]
                    }
                )
            )
            return 1
        check("installation_state_ready", lambda: state.get("status") == "ready")
        check("recovery_gate", lambda: not self.guard.exists() if service else True)
        check(
            "private_configuration",
            lambda: (
                validate_env(read_env(self.etc / "runtime.env")),
                migration_environment(
                    read_env(self.etc / "migration.env"),
                    private_file(self.etc / "db-owner-password").decode(),
                ),
            ),
        )

        def permissions():
            for path in (self.etc, self.lib, self.logs, self.backups):
                st = path.lstat()
                if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o077:
                    return False
            for name in (
                "runtime.env",
                "migration.env",
                "db-bootstrap-password",
                "app-password",
                "db-owner-password",
            ):
                private_file(self.etc / name)
            for name in DATA_DIRS:
                st = (self.lib / name).lstat()
                if (
                    not stat.S_ISDIR(st.st_mode)
                    or st.st_uid != 10001
                    or st.st_mode & 0o077
                ):
                    return False
            return True

        check("file_permissions_and_storage", permissions)

        def inventory():
            metadata = json.loads(private_file(release / "release.json"))
            return metadata["approval"]["commit"] == state["commit"] and all(
                not (release / p).is_symlink() and file_hash(release / p) == h
                for p, h in metadata["files"].items()
            )

        check("approved_source_inventory", inventory)
        check(
            "compose_configuration",
            lambda: self.compose(release, state, "config", "--quiet"),
        )

        def runtime():
            ids = self.compose(release, state, "ps", "-q").stdout.decode().split()
            infos = json.loads(run([*DOCKER, "inspect", *ids]).stdout)
            if len(infos) != 6:
                return False
            networks = set()
            for item in infos:
                name = item["Config"]["Labels"]["com.docker.compose.service"]
                if item["State"].get("Health", {}).get("Status") != "healthy":
                    return False
                if (
                    name in ("backend", "worker")
                    and item["Config"]["User"] != "10001:10001"
                ):
                    return False
                if item["HostConfig"].get("PortBindings"):
                    return False
                for ports in item["NetworkSettings"].get("Ports", {}).values():
                    if ports and (
                        name != "frontend"
                        or any(v["HostIp"] != "127.0.0.1" for v in ports)
                    ):
                        return False
                if item["HostConfig"]["LogConfig"] != {
                    "Type": "json-file",
                    "Config": {"max-file": "3", "max-size": "10m"},
                }:
                    return False
                networks.update(item["NetworkSettings"]["Networks"])
            return len(networks) == 1 and all(
                n["Internal"]
                for n in json.loads(
                    run([*DOCKER, "network", "inspect", *networks]).stdout
                )
            )

        check(
            "healthy_services_private_bindings_internal_network_bounded_logs", runtime
        )
        script = "from app.core.database import SessionLocal; from sqlalchemy import text; import json; db=SessionLocal(); print(json.dumps(dict(db.execute(text(\"SELECT current_user AS username, (SELECT version_num FROM alembic_version) AS head, (SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls FROM pg_roles WHERE rolname=current_user) AS privileged, has_schema_privilege(current_user,'public','CREATE') AS schema_write\")).mappings().one()))); db.close()"

        def database():
            value = json.loads(
                self.compose(
                    release, state, "exec", "-T", "backend", "python", "-c", script
                ).stdout
            )
            return value == {
                "username": "noc_app",
                "head": state["migration_head"],
                "privileged": False,
                "schema_write": False,
            }

        check("postgres_connectivity_migration_head_least_privilege", database)
        check(
            "worker_heartbeat",
            lambda: self.compose(
                release,
                state,
                "exec",
                "-T",
                "worker",
                "python",
                "-m",
                "app.worker_health",
            ),
        )
        check(
            "nginx_configuration",
            lambda: self.compose(
                release, state, "exec", "-T", "frontend", "nginx", "-t"
            ),
        )

        def http():
            base = f"{'https://localhost' if state['tls'] else 'http://127.0.0.1'}:{state['port']}"
            context = ssl.create_default_context(
                cafile=(
                    str(self.etc / "tls-ca.pem")
                    if (self.etc / "tls-ca.pem").exists()
                    else None
                )
            )
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=context),
            )
            with opener.open(base + "/health", timeout=10) as response:
                value = json.load(response)
                if value.get("status") != "ok":
                    return False
            with opener.open(base + "/", timeout=10) as response:
                return response.status == 200 and "nosniff" in response.headers.get(
                    "X-Content-Type-Options", ""
                )

        if service:
            check("frontend_backend_and_security_headers", http)
        else:
            check(
                "candidate_internal_frontend_health",
                lambda: self.compose(
                    release,
                    state,
                    "exec",
                    "-T",
                    "backend",
                    "python",
                    "-c",
                    "import urllib.request,json; r=urllib.request.urlopen('http://frontend:8080/health',timeout=10); assert json.load(r)['status']=='ok'",
                ),
            )
        if service and not self.args.rehearsal:
            check(
                "systemd_service",
                lambda: run(["systemctl", "is-active", "--quiet", "nwo-noc.service"]),
            )
            check(
                "unprivileged_ingress_service",
                lambda: run(
                    ["systemctl", "is-active", "--quiet", "nwo-noc-ingress.service"]
                ),
            )
            check(
                "journald",
                lambda: bool(
                    run(
                        [
                            "journalctl",
                            "-u",
                            "nwo-noc.service",
                            "-n",
                            "1",
                            "--no-pager",
                            "-o",
                            "json",
                        ]
                    ).stdout
                ),
            )
        elif self.args.rehearsal:
            checks.append(
                {
                    "check": "systemd_and_boot",
                    "status": "WARNING",
                    "action": "Container rehearsal cannot validate systemd/host boot",
                }
            )
        try:
            self.recipient()
            checks.append(
                {"check": "encrypted_backup_configuration", "status": "PASSED"}
            )
        except Exception:
            checks.append(
                {
                    "check": "encrypted_backup_configuration",
                    "status": "NOT_CONFIGURED",
                    "action": "Configure age recipient before upgrades; off-host restore still requires operator testing",
                }
            )
        checks.append(
            {
                "check": "live_integrations_public_status_and_external_delivery",
                "status": "NOT_CONFIGURED",
                "action": "Intentionally disabled; separately approved integration procedures required",
            }
        )
        print(json.dumps({"checks": checks}, indent=2))
        return int(any(v["status"] == "FAILED" for v in checks))


def parser():
    p = argparse.ArgumentParser(
        description="Approved Ubuntu24.04 NOC packaging; no production or equipment access during development"
    )
    p.add_argument(
        "action",
        choices=[
            "install",
            "upgrade",
            "verify",
            "backup",
            "service-start",
            "service-stop",
        ],
    )
    p.add_argument("--approval", default="/etc/nwo-noc-release.json")
    p.add_argument("--root", default="/")
    p.add_argument("--controlled-test", action="store_true")
    p.add_argument("--rehearsal", action="store_true")
    p.add_argument("--password-stdin", action="store_true")
    p.add_argument("--admin-username", default="admin")
    p.add_argument("--admin-email", default="admin@localhost.invalid")
    p.add_argument("--port", type=int, default=18090)
    p.add_argument("--backup-recipient")
    p.add_argument("--tls-cert")
    p.add_argument("--tls-key")
    p.add_argument("--tls-ca")
    p.add_argument("--quiesce", action="store_true")
    return p


@contextmanager
def operation_lock(host):
    folder = host.path("run/lock")
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        folder / "nwo-noc-installer.lock",
        os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        st = os.fstat(fd)
        if st.st_uid != 0 or st.st_mode & 0o077:
            raise Reject("Privileged private installer lock required")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def main():
    os.umask(0o077)
    args = parser().parse_args()
    host = Host(args)
    host.environment()
    if args.action in ("service-start", "service-stop"):
        state = host.state()
        release = host.opt / "releases" / state["commit"]
        if args.action == "service-start":
            if host.guard.exists() or state["status"] != "ready":
                raise Reject(
                    "Recovery/incomplete installation gate prevents unattended startup; see TROUBLESHOOTING.md"
                )
            host.start(release, state)
        else:
            if args.rehearsal:
                host.stop_ingress()
            host.compose(
                release,
                state,
                "stop",
                "frontend",
                "planning-worker",
                "worker",
                "backend",
                "redis",
                "db",
            )
        return 0
    with operation_lock(host):
        if args.action == "verify":
            return host.verify()
        if args.action == "backup":
            if not args.quiesce:
                raise Reject("Backup requires explicit --quiesce downtime approval")
            if host.guard.exists():
                raise Reject("Recovery already pending; do not change existing state")
            state = host.state()
            release = host.opt / "releases" / state["commit"]
            write_json(host.guard, {"phase": "backup"})
            try:
                host.stop_ingress()
                host.stop_apps(release, state)
                host.backup(release, state)
            finally:
                host.guard.unlink()
                host.start(release, state)
                host.start_ingress(release, state)
        else:
            getattr(host, args.action)()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Reject as error:
        print("NOC packaging: " + str(error), file=sys.stderr)
        raise SystemExit(1)
    except (
        ValueError,
        OSError,
        KeyError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ):
        # Never echo provider stderr, SQL input, environment values or credential-bearing exceptions.
        print(
            "NOC packaging operation rejected/failed. Check approval, OS/resources, protected state and the installation runbooks. Existing data and backups are preserved.",
            file=sys.stderr,
        )
        raise SystemExit(1)
