"""Old release -> failed upgrade -> rollback -> upgrade -> isolated old restore.

No live networks, schema downgrades or overwriting an existing recovery database.
"""

import json, os, secrets, subprocess, sys, time, re
from pathlib import Path
from configure import generate
from validate_config import validate
from pin_images import capture, image_id

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "b5329506a34ce0fe5b6889b20a559ab4f1646a0c"
PROJECT = os.environ.get("NOC93_UPGRADE_PROJECT", "noc84-noc93upgrade")


def command(args, *, expect_failure=False):
    p = subprocess.run(
        args,
        cwd=ROOT,
        env=dict(os.environ, NOC_PROJECT=PROJECT),
        capture_output=True,
        text=True,
        timeout=240,
    )
    if (p.returncode == 0) == expect_failure:
        raise RuntimeError(
            "Isolated upgrade step failed; retain state and inspect isolated service health/logs"
        )
    return p.stdout


def manage(state, *args, expect_failure=False):
    return command(
        [str(ROOT / "scripts/release/manage.sh"), str(state), *args],
        expect_failure=expect_failure,
    )


def python(
    state, code, *, service="backend", running=True, overrides=(), expect_failure=False
):
    args = ["exec", "-T"] if running else ["run", "--rm", "--no-deps"]
    for value in overrides:
        args += ["-e", value]
    if not running:
        args += ["--entrypoint", "python"]
    args += [service]
    if running:
        args += ["python"]
    return manage(state, *args, "-c", code, expect_failure=expect_failure)


PROBE = """
import json,os,urllib.request,urllib.parse
from sqlalchemy import text
from app.core.database import SessionLocal
h=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5));assert h['status']=='ok'
form=urllib.parse.urlencode({'username':os.environ['ADMIN_USERNAME'],'password':os.environ['ADMIN_PASSWORD']}).encode()
token=json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/api/auth/login',data=form),timeout=5))['access_token']
r=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/api/rf/operations',headers={'Authorization':'Bearer '+token}),timeout=10);assert r.status==200
with SessionLocal() as db:
 revision=db.scalar(text('SELECT version_num FROM alembic_version'))
 assert db.scalar(text("SELECT count(*) FROM telemetry_samples WHERE raw_data::jsonb->>'noc93_marker'='preserved'"))==1
 assert db.scalar(text("SELECT count(*) FROM incidents WHERE findings='NOC93 retained findings'"))==1
 assert db.scalar(text("SELECT to_regclass('noc93_failed_upgrade')")) is None
 assert h['version']=={'0011_monitoring_reliability':'3.2.1-rc.2','0016_down_detector':'3.2.1-rc.3'}[revision]
print(json.dumps({'revision':revision,'health':'ok','authenticated_rf_operations':'ok','raw_and_findings':'preserved'}))
"""
SEED = """
from app.core.database import SessionLocal
from app.models.entities import Device,TelemetrySample,Incident
with SessionLocal() as db:
 d=Device(source='synthetic',external_id='noc93-upgrade',name='Synthetic upgrade marker');db.add(d);db.flush()
 db.add(TelemetrySample(device_id=d.id,raw_data={'noc93_marker':'preserved'}));db.add(Incident(title='Synthetic upgrade investigation',findings='NOC93 retained findings'));db.commit()
"""
FAIL_MIGRATION = """
from sqlalchemy import text
from app.core.database import engine
with engine.begin() as c:
 c.execute(text('CREATE TABLE noc93_failed_upgrade (value integer)'))
 c.execute(text('INSERT INTO noc93_failed_upgrade VALUES (1)'))
 print('NOC94_DDL_CHECKPOINT',flush=True)
 raise RuntimeError('Synthetic transactional upgrade failure')
"""


def main(state):
    state = Path(state)
    if not re.fullmatch(r"noc84-noc93upgrade(?:-[a-z0-9]{1,16})?", PROJECT):
        raise ValueError("Dedicated isolated upgrade project required")
    if not state.is_absolute() or state.exists():
        raise ValueError("New absolute state directory required")
    # Refuse to attach to any existing project, even if the requested path is new.
    existing = command(
        [
            "docker",
            "--host=unix:///var/run/docker.sock",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={PROJECT}",
        ]
    )
    volumes = command(
        [
            "docker",
            "--host=unix:///var/run/docker.sock",
            "volume",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={PROJECT}",
        ]
    )
    if existing.strip() or volumes.strip():
        raise ValueError(
            "Rehearsal project exists; retain it and choose a clean disposable environment"
        )
    start = time.monotonic()
    generate(state)
    old = {
        "NOC_BACKEND_IMAGE": image_id("noc93-backend:rollback-b532950"),
        "NOC_FRONTEND_IMAGE": image_id("noc93-frontend:rollback-b532950"),
        "NOC_FRONTEND_INTERNAL_PORT": "80",
    }
    original = validate(state / "acceptance.env")
    original.update(old)
    (state / "acceptance.env").write_text(
        "".join(f"{k}={v}\n" for k, v in original.items())
    )
    capture(state, BASELINE)
    checks = []
    command([str(ROOT / "scripts/release/rehearse-ubuntu.sh"), str(state)])
    python(state, SEED)
    # A real old-format private delivery is carried through the UID transition.
    spool_proof = json.loads(
        python(
            state,
            """
import hashlib,json
from datetime import datetime,timezone
from types import SimpleNamespace
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device
from app.services.monitoring_collectors import synthetic
from app.services.monitoring_spool import Delivery,put,target
settings.synthetic_monitoring_enabled=True
with SessionLocal() as db:
 d=Device(source='synthetic',external_id='noc94-spool',name='Synthetic retained spool',vendor='Cambium',enabled=True);db.add(d);db.commit()
 at=datetime.now(timezone.utc);j=SimpleNamespace(device_id=d.id,fixture={'mode':'normal'})
 delivery=Delivery(job_key='synthetic:'+str(d.id),slot_at=at,observation=synthetic(db,j,at))
 put(settings.rf_scheduler_spool_dir,delivery);p=target(settings.rf_scheduler_spool_dir,delivery.job_key,at)
 print(json.dumps({'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))
""",
            service="worker",
        )
    )
    assert json.loads(python(state, PROBE))["revision"] == "0011_monitoring_reliability"
    checks.append("old_release_authenticated_startup")
    dump = state / "before-upgrade.dump"
    command([str(ROOT / "scripts/release/backup.sh"), str(state), str(dump)])
    checks.append("preupgrade_backup")
    candidate = state / "candidate"
    candidate.mkdir(mode=0o700)
    values = validate(state / "acceptance.env")
    values.update(
        NOC_BACKEND_IMAGE=image_id("noc84-backend:rc"),
        NOC_FRONTEND_IMAGE=image_id("noc84-frontend:rc"),
        NOC_FRONTEND_INTERNAL_PORT="8080",
    )
    with open(
        candidate / "acceptance.env", "x", opener=lambda p, f: os.open(p, f, 0o600)
    ) as f:
        f.write("".join(f"{k}={v}\n" for k, v in values.items()))
    capture(candidate)
    manage(state, "stop", "worker", "backend")
    assert "NOC94_DDL_CHECKPOINT" in python(
        candidate, FAIL_MIGRATION, service="migrate", running=False, expect_failure=True
    )
    checks.append("failed_transactional_migration_rejected")
    python(
        candidate,
        "import app.main",
        running=False,
        overrides=("RF_SCHEDULER_ENABLED=true", "SYNTHETIC_MONITORING_ENABLED=false"),
        expect_failure=True,
    )
    checks.append("unsafe_candidate_startup_rejected")
    manage(
        state,
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "180",
        "backend",
        "worker",
        "frontend",
    )
    assert json.loads(python(state, PROBE))["revision"] == "0011_monitoring_reliability"
    checks.append("old_release_rollback_healthy_with_original_data")
    manage(state, "stop", "worker", "backend")
    command([str(ROOT / "scripts/release/prepare-runtime.sh"), str(candidate)])
    manage(candidate, "up", "-d", "--wait", "--wait-timeout", "180")
    assert json.loads(python(candidate, PROBE))["revision"] == "0016_down_detector"
    checks.append("candidate_upgrade_preserves_data")
    proof_code = (
        "import os,hashlib,json;from pathlib import Path;p=Path('/var/lib/nwo-noc/monitoring')/"
        + repr(spool_proof["name"])
        + ";assert os.getuid()==10001;assert p.stat().st_uid==10001;print(hashlib.sha256(p.read_bytes()).hexdigest())"
    )
    assert (
        python(candidate, proof_code, service="worker").strip() == spool_proof["sha256"]
    )
    checks.append("private_spool_payload_preserved_during_nonroot_upgrade")
    manage(candidate, "restart", "backend", "worker")
    manage(candidate, "up", "-d", "--wait", "backend", "worker")
    assert json.loads(python(candidate, PROBE))["revision"] == "0016_down_detector"
    checks.append("candidate_restart_healthy")
    target = "restore_noc93_" + secrets.token_hex(4)
    command(
        [str(ROOT / "scripts/release/restore.sh"), str(candidate), str(dump), target]
    )
    checks.append("old_backup_restored_without_overwrite")
    # Launch the actual OLD app on its matching restored schema, no host ports.
    url = values["DATABASE_URL"].rsplit("/", 1)[0] + "/" + target
    ident = (
        manage(state, "run", "-d", "--no-deps", "-e", "DATABASE_URL=" + url, "backend")
        .strip()
        .splitlines()[-1]
    )
    try:
        deadline = time.monotonic() + 45
        while True:
            p = subprocess.run(
                [
                    "docker",
                    "--host=unix:///var/run/docker.sock",
                    "exec",
                    ident,
                    "python",
                    "-c",
                    PROBE,
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if p.returncode == 0:
                break
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Restored old application failed health/auth/data checks"
                )
            time.sleep(1)
        assert json.loads(p.stdout)["revision"] == "0011_monitoring_reliability"
        checks.append("actual_old_application_runs_on_matching_restored_database")
    finally:
        command(["docker", "--host=unix:///var/run/docker.sock", "rm", "-f", ident])
    assert json.loads(python(candidate, PROBE))["revision"] == "0016_down_detector"
    checks.append("current_candidate_and_original_database_untouched_by_restore")
    return {
        "checks": checks,
        "checks_passed": len(checks),
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "old_schema": "0011_monitoring_reliability",
        "new_schema": "0016_down_detector",
        "restore_database": target,
        "network": "internal-only; no equipment access",
        "data": "synthetic only; volumes/backups/configurations retained",
    }


if __name__ == "__main__":
    try:
        print(json.dumps(main(sys.argv[1])), flush=True)
    except (
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        OSError,
        KeyError,
        json.JSONDecodeError,
    ):
        raise SystemExit(
            "Upgrade rehearsal failed: preserve state/volumes/backups; inspect the isolated project. No production or existing restore database was overwritten."
        )
