"""NOC113 packaging contracts; pure inputs or private temporary files, no host writes."""

import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
from types import SimpleNamespace
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/host"))
import policy as host_policy
import manager as host_manager


def manifest():
    return {
        "format": 1,
        "repository": "treichhart1/nwo-noc",
        "purpose": "controlled-test",
        "approval_reference": "isolated-acceptance",
        "commit": "a" * 40,
        "tree": "b" * 40,
        "migration_head": "0028_customer_status",
        "upgrade_from": [],
        "images": {"backend": "sha256:" + "c" * 64, "frontend": "sha256:" + "d" * 64},
    }


def test_approved_manifest_is_explicit_and_local_images_cannot_be_production(
    tmp_path, monkeypatch
):
    data = manifest()
    monkeypatch.setattr(
        host_policy, "private_file", lambda p: json.dumps(data).encode()
    )
    assert (
        host_policy.approval(tmp_path / "approval", controlled=True)["commit"]
        == "a" * 40
    )
    with pytest.raises(ValueError):
        host_policy.approval(tmp_path / "approval", controlled=False)
    data["purpose"] = "release"
    with pytest.raises(ValueError):
        host_policy.approval(tmp_path / "approval", controlled=False)


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", 2),
        ("repository", "someone/else"),
        ("commit", "main"),
        ("tree", "bad"),
        ("approval_reference", "REPLACE_ME"),
        ("migration_head", "head; DROP"),
        ("upgrade_from", ["branch"]),
        ("images", {"backend": "latest"}),
    ],
)
def test_invalid_approval_rejected(tmp_path, monkeypatch, field, value):
    data = manifest()
    data[field] = value
    monkeypatch.setattr(
        host_policy, "private_file", lambda p: json.dumps(data).encode()
    )
    with pytest.raises(ValueError):
        host_policy.approval(tmp_path / "approval", controlled=True)


def test_approval_cannot_be_part_of_source(tmp_path):
    with pytest.raises(ValueError):
        host_policy.approval(tmp_path / "source/approval", source=tmp_path / "source")


def test_private_file_rejects_public_permissions_and_symlinks(tmp_path):
    os.chmod(tmp_path, 0o700)
    target = tmp_path / "secret"
    target.write_text("synthetic")
    os.chmod(target, 0o600)
    assert host_policy.private_file(target, owner=os.getuid()) == b"synthetic"
    os.chmod(target, 0o644)
    with pytest.raises(ValueError):
        host_policy.private_file(target, owner=os.getuid())
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError):
        host_policy.private_file(link, owner=os.getuid())


@pytest.mark.parametrize(
    "cpus,memory,disk",
    [
        (1, 8 * 1024**3, 30 * 1024**3),
        (4, 1024**3, 30 * 1024**3),
        (4, 8 * 1024**3, 1024**3),
    ],
)
def test_insufficient_resources_fail_before_install(cpus, memory, disk):
    with pytest.raises(ValueError):
        host_policy.resources(cpus=cpus, memory=memory, disk=disk)


def test_resource_floor_is_explicitly_separate_for_rehearsal():
    host_policy.resources(cpus=2, memory=4 * 1024**3, disk=10 * 1024**3)
    host_policy.resources(cpus=1, memory=256 * 1024**2, disk=1024**3, rehearsal=True)


@pytest.mark.parametrize(
    "value", ["short", "x" * 73, "x" * 20 + "\n", "x" * 20 + "\x00"]
)
def test_no_default_or_invalid_administrator_password(value):
    with pytest.raises(ValueError):
        host_policy.password(value)


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("linked", tarfile.SYMTYPE),
        ("device", tarfile.CHRTYPE),
    ],
)
def test_archive_import_rejects_escape_links_and_devices(name, kind):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = "/etc/shadow"
        archive.addfile(member)
    buf.seek(0)
    with tarfile.open(fileobj=buf) as archive, pytest.raises(ValueError):
        host_policy.safe_members(archive)


def test_root_override_cannot_be_used_for_real_installation(tmp_path):
    args = host_manager.parser().parse_args(["verify", "--root", str(tmp_path)])
    with pytest.raises(ValueError):
        host_manager.Host(args)


def fake_host(tmp_path):
    host = host_manager.Host(host_manager.parser().parse_args(["verify"]))
    host.root = tmp_path
    host.etc = tmp_path / "etc/nwo-noc"
    host.lib = tmp_path / "var/lib/nwo-noc"
    host.opt = tmp_path / "opt/nwo-noc"
    host.etc.mkdir(parents=True)
    host.lib.mkdir(parents=True)
    return host


def test_compose_reuses_all_core_services_without_owner_secrets_or_public_ports(
    tmp_path,
):
    host = fake_host(tmp_path)
    release = tmp_path / "release"
    (release / "deploy/host").mkdir(parents=True)
    (release / "deploy/compose.isolated.yml").write_text(
        (ROOT / "deploy/compose.isolated.yml").read_text()
    )
    (release / "deploy/host/nginx.conf.template").write_text(
        (ROOT / "deploy/host/nginx.conf.template").read_text()
    )
    (release / "frontend").mkdir()
    (release / "frontend/nginx-main.conf").write_text(
        (ROOT / "frontend/nginx-main.conf").read_text()
    )
    data = manifest()
    data["images"].update(
        postgres="postgres@sha256:" + "e" * 64, redis="redis@sha256:" + "f" * 64
    )
    host.compose_config(
        release, data, {"project": "noc113-test", "port": 18090, "tls": False}
    )
    rendered = json.loads((release / "compose.json").read_text())
    assert set(rendered["services"]) == {
        "db",
        "redis",
        "migrate",
        "backend",
        "worker",
        "frontend",
        "planning-worker",
        "fiber-sync-worker",
    }
    assert rendered["networks"] == {"isolated": {"internal": True}}
    for name, service in rendered["services"].items():
        if name != "frontend":
            assert not service.get("ports")
        else:
            assert not service.get("ports")
            assert any("/ingress" in v for v in service["volumes"])
        if name in ("backend", "worker"):
            assert service["env_file"] == [str(host.etc / "runtime.env")]
            assert service["environment"] == {}
    assert "migrate" not in rendered["services"]["backend"]["depends_on"]
    assert "app.seed" not in json.dumps(rendered)
    assert "migration.env" in rendered["services"]["migrate"]["env_file"][-1]
    assert "x-app" not in rendered
    assert all(
        v["restart"] == "on-failure:3"
        for k, v in rendered["services"].items()
        if k != "migrate"
    )


def test_existing_install_is_never_an_implicit_upgrade(tmp_path, monkeypatch):
    host = fake_host(tmp_path)
    monkeypatch.setattr(host, "environment", lambda: None)
    monkeypatch.setattr(host, "approved", manifest)
    monkeypatch.setattr(host, "capacity", lambda: None)
    monkeypatch.setattr(
        host,
        "dependencies",
        lambda: pytest.fail("Dependencies changed before existing-path detection"),
    )
    with pytest.raises(ValueError, match="Existing installation"):
        host.install()


def test_failed_postmigration_upgrade_keeps_old_pointer_and_recovery_gate(
    tmp_path, monkeypatch
):
    host = fake_host(tmp_path)
    host.logs = tmp_path / "logs"
    host.logs.mkdir()
    host.guard = host.lib / "recovery-required.json"
    host.state_path = host.lib / "installation.json"
    old = {"status": "ready", "commit": "a" * 40, "project": "noc113-unit"}
    data = manifest()
    data["commit"] = "b" * 40
    data["upgrade_from"] = [old["commit"]]
    release = tmp_path / "target"
    release.mkdir()
    for folder in (release, host.opt / "releases" / old["commit"]):
        (folder / "deploy/host").mkdir(parents=True)
        for name in (
            "nwo-noc.service",
            "nwo-noc-ingress.service",
            "nwo-noc-backup.service",
            "nwo-noc-backup.timer",
            "logrotate.conf",
        ):
            (folder / "deploy/host" / name).write_text(
                (ROOT / "deploy/host" / name).read_text()
            )
    monkeypatch.setattr(host, "environment", lambda: None)
    monkeypatch.setattr(host, "state", lambda: old)
    monkeypatch.setattr(host, "verify", lambda **k: 0)
    monkeypatch.setattr(host, "approved", lambda: data)
    monkeypatch.setattr(host, "capacity", lambda **k: None)
    monkeypatch.setattr(host, "backup_budget", lambda *a: 0)
    for name in (
        "recipient",
        "dependencies",
        "images",
        "compose_config",
        "image_preflight",
        "stop_apps",
        "stop_ingress",
        "compose",
        "migrate",
    ):
        monkeypatch.setattr(host, name, lambda *a, **k: None)
    monkeypatch.setattr(host, "stage", lambda a: release)
    monkeypatch.setattr(host, "backup", lambda *a: tmp_path / "encrypted.age")
    monkeypatch.setattr(host_manager, "validate_env", lambda a: None)
    monkeypatch.setattr(host_manager, "read_env", lambda a: {})
    monkeypatch.setattr(host_manager, "migration_environment", lambda *a: None)
    monkeypatch.setattr(host_manager, "private_file", lambda *a: b"synthetic")
    monkeypatch.setattr(host_manager, "git", lambda *a: "")
    host.args.rehearsal = True
    monkeypatch.setattr(
        host,
        "start",
        lambda *a: (_ for _ in ()).throw(ValueError("Synthetic startup failure")),
    )
    monkeypatch.setattr(
        host, "switch", lambda a: pytest.fail("Failed target became current")
    )
    with pytest.raises(ValueError):
        host.upgrade()
    assert json.loads(host.guard.read_text())["phase"] == "migration_started"
    assert not host.state_path.exists()


def test_units_are_scoped_and_restrict_application_privileges():
    unit = (ROOT / "deploy/host/nwo-noc.service").read_text()
    assert "Requires=docker.service" in unit
    assert "NoNewPrivileges=true" in unit and "ProtectSystem=strict" in unit
    assert "Restart=on-failure" in unit and "StartLimitBurst=3" in unit
    assert "EnvironmentFile=-/etc/nwo-noc/service.env" in unit
    assert "nwo-noc/current/scripts/host/manager.py" in unit


def test_nginx_is_private_image_configuration_and_preserves_upload_paths():
    text = (ROOT / "deploy/host/nginx.conf.template").read_text()
    for value in [
        "X-Content-Type-Options nosniff",
        "frame-ancestors 'none'",
        "client_max_body_size 128m",
        "proxy_connect_timeout 5s",
        "location ~ /\\.",
    ]:
        assert value in text
    assert "letsencrypt" not in text.lower()


def test_ubuntu_package_candidate_must_be_official_noble():
    text = "Package:\n  Candidate: 1.2\n  Version table:\n     1.2 500\n        500 http://azure.archive.ubuntu.com/ubuntu noble/universe amd64 Packages\n"
    assert host_policy.ubuntu_candidate(text) == "1.2"
    with pytest.raises(ValueError):
        host_policy.ubuntu_candidate(
            text.replace("azure.archive.ubuntu.com", "unreviewed.invalid")
        )
    with pytest.raises(ValueError):
        host_policy.ubuntu_candidate(text.replace("noble/universe", "jammy/universe"))


def test_explicit_public_nonsecret_file_mode_survives_private_umask(tmp_path):
    old = os.umask(0o077)
    try:
        file = tmp_path / "nginx.conf"
        host_manager.atomic(file, "# non-secret configuration\n", 0o444)
        assert stat.S_IMODE(file.stat().st_mode) == 0o444
    finally:
        os.umask(old)


def test_migration_configuration_cannot_target_external_or_wrong_database():
    secret = "a" * 48
    host_policy.migration_environment(
        {"DATABASE_URL": f"postgresql+psycopg://noc_owner:{secret}@db:5432/nwo_noc"},
        secret,
    )
    with pytest.raises(ValueError):
        host_policy.migration_environment(
            {
                "DATABASE_URL": f"postgresql+psycopg://noc_owner:{secret}@unapproved.invalid:5432/nwo_noc"
            },
            secret,
        )
    with pytest.raises(ValueError):
        host_policy.migration_environment(
            {"DATABASE_URL": f"postgresql+psycopg://noc_owner:{secret}@db:5432/other"},
            secret,
        )


def test_loopback_unix_ingress_preserves_large_half_closed_stream(tmp_path):
    import socket
    import threading
    import ingress

    path = tmp_path / "frontend.sock"
    upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    upstream.bind(str(path))
    upstream.listen(1)
    received = []

    def echo():
        with upstream.accept()[0] as client:
            chunks = []
            while True:
                data = client.recv(8192)
                if not data:
                    break
                chunks.append(data)
            body = b"".join(chunks)
            received.append(body)
            client.sendall(body)
        upstream.close()

    thread = threading.Thread(target=echo, daemon=True)
    thread.start()
    server = ingress.Server(0, path)
    running = threading.Thread(target=server.serve_forever, daemon=True)
    running.start()
    payload = b"synthetic-stream-" * 20000
    try:
        assert server.server_address[0] == "127.0.0.1"
        with socket.create_connection(server.server_address, timeout=10) as client:
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                data = client.recv(8192)
                if not data:
                    break
                chunks.append(data)
        assert b"".join(chunks) == payload
        assert received == [payload]  # Never replay a request after uncertain response.
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_ingress_unit_has_no_docker_privileges():
    unit = (ROOT / "deploy/host/nwo-noc-ingress.service").read_text()
    assert "User=nwo-noc" in unit and "CapabilityBoundingSet=" in unit
    assert "RestrictAddressFamilies=AF_UNIX AF_INET" in unit
    assert "MemoryMax=128M" in unit and "TasksMax=64" in unit
    assert "IPAddressDeny=any" in unit and "IPAddressAllow=localhost" in unit
    assert "docker.sock" not in unit


def test_generated_unit_port_and_local_keys_do_not_require_demo_data(
    tmp_path, monkeypatch
):
    host = fake_host(tmp_path)
    host.args.port = 18443
    monkeypatch.setattr(host_manager.os, "chown", lambda *a, **k: None)
    host.configure()
    assert (host.etc / "service.env").read_text() == "NOC_LISTEN_PORT=18443\n"
    env = dict(
        line.split("=", 1)
        for line in (host.etc / "runtime.env").read_text().splitlines()
    )
    assert (
        env["APP_ENV"] == "production"
        and env["SYNTHETIC_MONITORING_ENABLED"] == "false"
    )
    assert env["SIGNUP_PII_KEY_FILE"] == "/run/nwo-noc-keys/signup"
    assert (
        env["PLANNING_WORKER_ENABLED"] == "true"
        and env["MAINTENANCE_SCHEDULER_ENABLED"] == "true"
    )
    host_policy.validate_env(env)
    with pytest.raises(ValueError):
        host_policy.validate_env({**env, "UISP_ENABLED": "true"})
    assert len(list((host.etc / "keys").iterdir())) == 4


def test_staged_source_is_readable_by_unprivileged_ingress_under_private_umask(
    tmp_path, monkeypatch
):
    host = fake_host(tmp_path)
    (host.opt / "releases").mkdir(parents=True)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        data = b"# synthetic source\n"
        member = tarfile.TarInfo("entry.py")
        member.size = len(data)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(data))
    monkeypatch.setattr(
        host_manager, "run", lambda *a, **k: SimpleNamespace(stdout=buf.getvalue())
    )
    old = os.umask(0o077)
    try:
        path = host.stage(manifest())
        assert stat.S_IMODE(path.stat().st_mode) == 0o755
        assert stat.S_IMODE((path / "release.json").stat().st_mode) == 0o600
    finally:
        os.umask(old)


def test_missing_dependencies_use_signed_ubuntu_without_replacing_compatible_docker(
    tmp_path, monkeypatch
):
    host = fake_host(tmp_path)
    commands = []

    def execute(argv, **kwargs):
        commands.append(argv)
        out = b""
        if argv[0] == "dpkg-query":
            return SimpleNamespace(returncode=1, stdout=out)
        if argv[:2] == ["apt-cache", "policy"]:
            out = b"Package:\n Candidate: 1.2\n Version table:\n    1.2 500\n       500 http://archive.ubuntu.com/ubuntu noble/universe amd64 Packages\n"
        if "version" in argv and "{{.Server.Version}}" in argv:
            out = b"29.0.0"
        if "compose" in argv and "version" in argv:
            out = b"2.40.0"
        return SimpleNamespace(returncode=0, stdout=out)

    monkeypatch.setattr(host_manager, "run", execute)
    monkeypatch.setattr(host_manager.shutil, "which", lambda name: "/usr/bin/" + name)
    host.dependencies()
    install = next(a for a in commands if a[:2] == ["apt-get", "install"])
    assert "python3-yaml" in install and "age" in install and "logrotate" in install
    assert "docker.io" not in install and "docker-compose-v2" not in install
    assert not any(a[0] == "systemctl" for a in commands)
