"""NOC114: controlled failures and measurements in disposable installations only.

No production endpoint, provider call, test bypass or destructive restore is used.
Failure-stage images are synthetic fixtures, not distributable releases.
"""

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.error
import urllib.request


def preinstall_failures(invoke, approval, secret, root, passed):
    probes = [
        ("invalid_loopback_port", approval, secret, ["--port", "80"]),
        ("missing_administrator_secret", approval, "short", []),
        ("missing_release_approval", approval.with_name("absent.json"), secret, []),
        ("incomplete_tls_configuration", approval, secret, ["--tls-cert", approval]),
    ]
    for name, file, password, extra in probes:
        p = invoke("install", file, password, success=False, extra=extra)
        passed(
            name + "_refused_without_installation",
            p.returncode != 0
            and not (root / "etc/nwo-noc").exists()
            and b"NOC packaging" in p.stderr,
        )
    approval.chmod(0o644)
    try:
        p = invoke("install", approval, secret, success=False)
        passed(
            "public_approval_permissions_refused",
            p.returncode != 0 and not (root / "etc/nwo-noc").exists(),
        )
    finally:
        approval.chmod(0o600)


def fresh_failure(
    args, clone, root, work, approval, secret, env, command, invoke, passed
):
    """Run actual install.sh with injected container failures; retain every database."""
    state_path = root / "var/lib/nwo-noc/installation.json"
    if args.failure_stage in ("resources", "dependencies"):
        if args.failure_stage == "dependencies":
            # Remove only the disposable container package; denied egress makes reinstall fail safely.
            command(["dpkg", "--remove", "logrotate"])
        p = invoke("install", approval, secret, success=False)
        # With apt indexes absent and egress denied, failure can occur during candidate
        # provenance validation before apt install. Both are required fail-closed paths.
        expected = (
            (b"Insufficient free disk",)
            if args.failure_stage == "resources"
            else (
                b"Ubuntu repository refresh failed",
                b"Required Ubuntu24.04 package candidate unavailable",
                b"Candidate must come from an official signed Ubuntu noble repository",
                b"Supported system dependency installation failed",
            )
        )
        print(
            json.dumps(
                {
                    "failure_stage": args.failure_stage,
                    "sanitized_installer_errors": [
                        line
                        for line in p.stderr.decode(errors="replace").splitlines()
                        if line.startswith("NOC packaging")
                    ],
                }
            ),
            flush=True,
        )
        passed(
            args.failure_stage + "_actual_preflight_failure",
            p.returncode != 0
            and any(message in p.stderr for message in expected)
            and not state_path.exists()
            and not (root / "etc/nwo-noc").exists(),
        )
        return
    if args.failure_stage == "interrupted":
        argv = [
            str(clone / "install.sh"),
            "--controlled-test",
            "--rehearsal",
            "--root",
            str(root),
            "--approval",
            str(approval),
            "--password-stdin",
        ]
        # The process is interrupted only after its disposable migration container exists.
        output = work / "interrupted-command.log"
        with output.open("xb") as log:
            output.chmod(0o600)
            child = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=log,
                cwd=clone,
                env=env,
                start_new_session=True,
            )
            child.stdin.write((secret + "\n").encode())
            child.stdin.close()
            found = False
            try:
                until = time.monotonic() + 150
                while child.poll() is None and time.monotonic() < until:
                    if state_path.exists():
                        state = json.loads(state_path.read_text())
                        p = command(
                            [
                                "docker",
                                "ps",
                                "-q",
                                "--filter",
                                "label=com.docker.compose.project=" + state["project"],
                                "--filter",
                                "label=com.docker.compose.service=migrate",
                            ]
                        )
                        for ident in p.stdout.decode().split():
                            cmd = command(
                                [
                                    "docker",
                                    "inspect",
                                    "--format",
                                    "{{json .Config.Cmd}}",
                                    ident,
                                ]
                            ).stdout
                            if json.loads(cmd) == ["alembic", "upgrade", "head"]:
                                found = True
                                break
                        if found:
                            break
                    time.sleep(0.5)
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=20)
            passed(
                "interrupted_during_actual_migration", found and child.returncode != 0
            )
    else:
        p = invoke("install", approval, secret, success=False)
        passed(
            args.failure_stage + "_fresh_install_rejected",
            p.returncode != 0 and b"NOC packaging" in p.stderr,
        )
    state = json.loads(state_path.read_text())
    passed(
        "failed_or_interrupted_state_preserved",
        state["status"] in ("installing", "installation_failed"),
    )
    config = root / "etc/nwo-noc/runtime.env"
    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    passed(
        "incomplete_install_cannot_boot",
        invoke("service-start", success=False).returncode != 0,
    )
    passed(
        "incomplete_install_verification_fails",
        invoke("verify", success=False).returncode != 0,
    )
    passed(
        "incomplete_install_rerun_refused",
        invoke("install", approval, secret, success=False).returncode != 0,
    )
    passed(
        "failed_configuration_not_overwritten",
        digest == hashlib.sha256(config.read_bytes()).hexdigest(),
    )
    # Stop only containers carrying this generated installation project, including one-offs.
    ids = (
        command(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                "label=com.docker.compose.project=" + state["project"],
            ]
        )
        .stdout.decode()
        .split()
    )
    if ids:
        command(["docker", "stop", "--time", "10", *ids])
    print(
        json.dumps(
            {
                "failure_stage": args.failure_stage,
                "os": "Ubuntu24.04",
                "execution": "actual install.sh in isolated container with approved synthetic failure fixture",
                "persistent_state_retained": True,
                "network": "internal-only application network; no equipment access",
            }
        ),
        flush=True,
    )


SMOKE_PATHS = (
    "/api/dashboard",
    "/api/sites",
    "/api/devices",
    "/api/links",
    "/api/sectors",
    "/api/integrations",
    "/api/incidents",
    "/api/rf/analytics/options",
    "/api/monitoring/reliability",
    "/api/ipam/vrfs",
    "/api/equipment/assets",
    "/api/field/labels",
    "/api/field/work-orders",
    "/api/maintenance-ops/schedules",
    "/api/maintenance-ops/documents",
    "/api/applications/plans",
    "/api/unified/graph",
    "/api/planning/catalog",
    "/api/fiber-designer/v1/contract",
    "/api/finance/v1/periods",
    "/api/analytics/v1/health",
    "/api/customer-status/v1/policy",
)


def installed_failures_and_smoke(
    root, compose, invoke, command, browser, token, passed, db, install_seconds
):
    timings = []
    for path in SMOKE_PATHS:
        start = time.monotonic()
        request = urllib.request.Request(
            "https://localhost:18090" + path,
            headers={"Authorization": "Bearer " + token},
        )
        with browser.open(request, timeout=20) as response:
            json.load(response)
            passed("installed_smoke:" + path, response.status == 200)
        timings.append(
            {"path": path, "milliseconds": round((time.monotonic() - start) * 1000, 3)}
        )
    for path in (
        "/api/sites",
        "/api/maintenance-ops/documents",
        "/api/finance/v1/periods",
        "/api/customer-status/v1/policy",
    ):
        try:
            browser.open("https://localhost:18090" + path, timeout=10)
        except urllib.error.HTTPError as error:
            passed("unauthenticated_private_api_denied:" + path, error.code == 401)
        else:
            raise AssertionError("Unauthenticated private API succeeded")
    config = root / "etc/nwo-noc/runtime.env"
    original, mode = config.read_bytes(), config.stat().st_mode & 0o777
    try:
        config.chmod(0o644)
        passed(
            "verification_rejects_public_secret_permissions",
            invoke("verify", success=False).returncode != 0,
        )
        config.chmod(mode)
        config.write_bytes(
            b"\n".join(
                line
                for line in original.splitlines()
                if not line.startswith(b"SECRET_KEY=")
            )
        )
        passed(
            "verification_rejects_missing_secret",
            invoke("verify", success=False).returncode != 0,
        )
    finally:
        config.write_bytes(original)
        config.chmod(mode)
    for service in ("db", "redis", "worker"):
        command([*compose, "stop", service])
        try:
            passed(
                "verification_detects_stopped_" + service,
                invoke("verify", success=False).returncode != 0,
            )
        finally:
            command(
                [
                    *compose,
                    "up",
                    "-d",
                    "--wait",
                    "--wait-timeout",
                    "120",
                    "db",
                    "redis",
                    "backend",
                    "worker",
                    "frontend",
                    "planning-worker",
                ]
            )
        passed(
            "safe_recovery_after_" + service + "_restart",
            invoke("verify").returncode == 0,
        )
    ids = command([*compose, "ps", "-q"]).stdout.decode().split()
    resources = command(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids]
    ).stdout.decode()
    # Container names and numeric resource figures only; never dump inspect/config/environment.
    rows = [
        {k: row[k] for k in ("Name", "CPUPerc", "MemUsage", "BlockIO", "PIDs")}
        for row in map(json.loads, resources.splitlines())
    ]
    disk = (
        command(["du", "-sb", root / "var/lib/nwo-noc", root / "opt/nwo-noc"])
        .stdout.decode()
        .splitlines()
    )
    query = json.loads(
        db(
            "EXPLAIN (ANALYZE, FORMAT JSON) SELECT id FROM devices ORDER BY id LIMIT 100;"
        )
    )
    print(
        json.dumps(
            {
                "host_measurements": {
                    "install_seconds": install_seconds,
                    "api_single_requests": timings,
                    "container_resource_snapshot": rows,
                    "persistent_and_release_bytes": [line.split()[0] for line in disk],
                    "query_execution_ms": query[0]["Execution Time"],
                    "scope": "clean host empty application after controlled restarts; single snapshots, not load p95 or lifetime peaks; representative synthetic scale is measured separately by existing RF/PostgreSQL benchmarks",
                }
            }
        ),
        flush=True,
    )
