"""Opt-in local CPU worker. Durable PostgreSQL queue, leased/fenced jobs and bounded children."""

import sys

# The health command must observe the running worker, not import/initialize its
# SQLAlchemy models, RF schemas and native GIS stack on every bounded probe.
if __name__ == "__main__" and "--health" in sys.argv:
    from .health import main as health_main
    health_main()
    raise SystemExit(0)

import json, os, subprocess, sys, time
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, update, or_
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import entities  # register shared metadata
from app.field.models import now
from app.field.service import audit
from .models import Job, Dataset, Scenario, Tile
from .service import get, current


class Cancelled(Exception):
    pass


def claim(db):
    job = db.scalar(
        select(Job)
        .where(
            or_(
                Job.state == "queued",
                (Job.state == "running") & (Job.lease_until < now()),
            )
        )
        .order_by(Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not job:
        return None
    if job.cancel_requested or job.attempts >= 3:
        job.state = "cancelled" if job.cancel_requested else "failed"
        job.error_code = "cancelled" if job.cancel_requested else "attempt_limit"
        job.finished_at = now()
        if job.dataset_id:
            get(db, Dataset, job.dataset_id).state = "failed"
        audit(
            db,
            job.created_by,
            "planning.job_exhausted",
            "planning_jobs",
            job.id,
            {"error_code": job.error_code},
        )
        db.commit()
        return None
    recovered = job.state == "running"
    if job.dataset_id:
        get(db, Dataset, job.dataset_id).state = "processing"
    job.state = "running"
    job.lease_token = str(uuid4())
    job.lease_until = now() + timedelta(seconds=120)
    job.attempts += 1
    if recovered:
        audit(
            db,
            job.created_by,
            "planning.job_recovered",
            "planning_jobs",
            job.id,
            {"attempt": job.attempts},
        )
    db.commit()
    return job.id, job.lease_token


def progress(id, token, percent):
    with SessionLocal() as db:
        j = get(db, Job, id)
        if j.state != "running" or j.lease_token != token or j.cancel_requested:
            raise Cancelled()
        j.progress = max(j.progress, percent)
        db.commit()


def process(db, id, token, report=None):
    """Computes in isolated worker process; final publish uses a lease-token row lock."""
    from . import terrain

    j = get(db, Job, id)
    if j.state != "running" or j.lease_token != token or j.cancel_requested:
        raise Cancelled()
    report = report or (lambda p: progress(id, token, p))
    if j.kind == "import":
        d = get(db, Dataset, j.dataset_id)
        path = terrain.local(d.storage_key)
        if terrain.checksum(path) != d.sha256:
            raise ValueError("checksum")
        expected = d.metadata_json["expected_bbox"]
        extra = {}
        raster_key = d.storage_key
        if d.format in {"las", "laz"}:
            raster_key = d.identifier + "-" + token + ".tif"
            output = terrain.root() / raster_key
            extra = terrain.las_to_raster(path, output, d.kind, expected)
            import rasterio
            from rasterio.warp import transform_bounds

            with rasterio.open(output, driver="GTiff") as src:
                expected = list(transform_bounds(src.crs, "EPSG:4326", *src.bounds))
        report(40)
        metadata = terrain.validate_raster(terrain.local(raster_key), expected)
        tiles = metadata.pop("tiles")
        metadata.update(
            extra,
            raster_key=raster_key,
            raster_sha256=terrain.checksum(terrain.local(raster_key)),
            declared_bbox=d.metadata_json["expected_bbox"],
            source_format=d.format,
        )
        overlaps = db.scalars(
            select(Dataset.id)
            .where(
                Dataset.id != d.id,
                Dataset.state == "ready",
                Dataset.west < metadata["bbox"][2],
                Dataset.east > metadata["bbox"][0],
                Dataset.south < metadata["bbox"][3],
                Dataset.north > metadata["bbox"][1],
            )
            .limit(101)
        ).all()
        metadata["overlapping_dataset_ids"] = overlaps[:100]
        metadata["overlap_truncated"] = len(overlaps) > 100
        result = {"dataset_id": d.id, "validated": True}
    else:
        scenario = get(db, Scenario, j.scenario_id)
        if not current(db, scenario):
            raise ValueError("source_changed")
        from .compute import calculate

        result = calculate(scenario.inputs, report)
    report(99)
    # Discard old identity-map state before checking cancellation/fencing.
    db.rollback()
    j = db.scalar(select(Job).where(Job.id == id).with_for_update())
    if j.state != "running" or j.lease_token != token or j.cancel_requested:
        raise Cancelled()
    if j.kind == "import":
        d = get(db, Dataset, j.dataset_id)
        d.metadata_json = metadata
        d.west, d.south, d.east, d.north = metadata["bbox"]
        d.state = "ready"
        for t in tiles:
            db.add(Tile(dataset_id=d.id, **t))
    elif not current(db, get(db, Scenario, j.scenario_id)):
        raise ValueError("source_changed")
    j.result = result
    j.state = "completed"
    j.progress = 100
    j.finished_at = now()
    j.lease_until = None
    audit(
        db,
        j.created_by,
        "planning.job_completed",
        "planning_jobs",
        j.id,
        {"kind": j.kind},
    )
    db.commit()


def finish_failure(id, token, code):
    with SessionLocal() as db:
        j = db.scalar(select(Job).where(Job.id == id).with_for_update())
        if not j or j.lease_token != token or j.state != "running":
            return
        j.state = "cancelled" if j.cancel_requested or code == "cancelled" else "failed"
        j.error_code = code
        j.finished_at = now()
        j.lease_until = None
        if j.dataset_id:
            get(db, Dataset, j.dataset_id).state = "failed"
        audit(
            db,
            j.created_by,
            "planning.job_" + j.state,
            "planning_jobs",
            j.id,
            {"error_code": code},
        )
        db.commit()


def limits():
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (80, 85))
    resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024, 1536 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024 * 1024, 128 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (96, 96))


def once():
    if not settings.planning_worker_enabled:
        return False
    with SessionLocal() as db:
        lease = claim(db)
    if not lease:
        return False
    id, token = lease
    env = {
        **os.environ,
        "PROJ_NETWORK": "OFF",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "GDAL_NUM_THREADS": "1",
    }
    try:
        # No user-provided command, shell, path, URL or executable.
        p = subprocess.run(
            [sys.executable, "-m", "app.planning.worker", "child", str(id), token],
            env=env,
            timeout=90,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if p.returncode:
            finish_failure(
                id, token, "processing_failed_review_format_crs_bounds_and_limits"
            )
    except subprocess.TimeoutExpired:
        finish_failure(id, token, "processing_time_limit")
    return True


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "child":
        limits()
        try:
            with SessionLocal() as db:
                process(db, int(sys.argv[2]), sys.argv[3])
        except Cancelled:
            finish_failure(int(sys.argv[2]), sys.argv[3], "cancelled")
        except Exception:
            finish_failure(
                int(sys.argv[2]),
                sys.argv[3],
                "processing_failed_review_format_crs_bounds_and_limits",
            )
            raise SystemExit(1)
    elif "--once" in sys.argv:
        once()
    else:
        if not settings.planning_worker_enabled:
            raise SystemExit(
                "PLANNING_WORKER_ENABLED is false; explicitly enable this offline processing worker"
            )
        from .terrain import root

        while True:
            heartbeat = root() / "worker.heartbeat"
            if heartbeat.is_symlink():
                raise SystemExit("Unsafe planning heartbeat")
            heartbeat.touch(mode=0o600)
            try:
                worked = once()
            except Exception:
                worked = False  # DB interruptions retain leases; no secrets/raw errors logged.
            time.sleep(1 if worked else 5)
