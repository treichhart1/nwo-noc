import hashlib, json, os
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, Depends, Query, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from app.core.database import get_db
from app.field.routes import access, run
from app.field import service as field
from app.core.config import settings
from .models import *
from .contracts import *
from . import service as s, terrain

router = APIRouter(
    prefix="/api/planning", tags=["Private RF planning"], dependencies=[Depends(access)]
)


def read(db, u, fn):
    return run(db, u, lambda: (s.reader(u), fn())[1])


def change(db, u, b, name, fn):
    def call():
        field.admin(u)
        return field.mutation(db, u, "planning_" + name, b, fn)

    return run(db, u, call, True)


@router.get("/catalog")
def catalog(db=Depends(get_db), u=Depends(access)):
    return read(
        db,
        u,
        lambda: {
            "profiles": [
                s.view(p)
                for p in db.scalars(
                    select(Profile).order_by(Profile.id).limit(501)
                ).all()[:500]
            ],
            "equipment_families": [
                {
                    "manufacturer": a,
                    "model": b,
                    "specifications": "Operator-supplied validated specifications required",
                }
                for a, b in s.CATALOG
            ],
            "worker_enabled": settings.planning_worker_enabled,
            "models": ["fspl", "dominant_knife_edge"],
            "predictions_only": True,
        },
    )


@router.get("/inventory")
def inventory(db=Depends(get_db), u=Depends(access)):
    def call():
        from app.models.entities import Site, Sector

        return {
            "sites": [
                {
                    "id": x.id,
                    "name": x.name,
                    "latitude": x.latitude,
                    "longitude": x.longitude,
                    "tower_height_m": (
                        None
                        if x.tower_height_ft is None
                        else x.tower_height_ft * 0.3048
                    ),
                    "height_source": "Existing inventory feet converted to metres",
                }
                for x in db.scalars(select(Site).order_by(Site.id).limit(1000))
            ],
            "sectors": [
                {
                    "id": x.id,
                    "name": x.name,
                    "site_id": x.site_id,
                    "device_id": x.device_id,
                    "frequency_mhz": x.frequency_mhz,
                    "channel_width_mhz": x.channel_width_mhz,
                    "azimuth_deg": x.azimuth,
                    "horizontal_beamwidth_deg": x.beamwidth,
                }
                for x in db.scalars(select(Sector).order_by(Sector.id).limit(1000))
            ],
        }

    return read(db, u, call)


@router.post("/profiles")
def profile(b: ProfileInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "profile", lambda: s.create_profile(db, u, b))


@router.get("/datasets")
def datasets(
    offset: int = Query(0, ge=0, le=10000), db=Depends(get_db), u=Depends(access)
):
    return read(db, u, lambda: s.datasets(db, offset))


@router.post("/datasets")
def upload(
    metadata: str = Form(max_length=8000),
    file: UploadFile = File(),
    db=Depends(get_db),
    u=Depends(access),
):
    if u.role != "admin":
        raise HTTPException(403, "Administrator terrain import required")
    try:
        b = DatasetInput.model_validate_json(metadata)
    except ValueError:
        raise HTTPException(
            422, "Invalid terrain metadata, units, CRS coverage or acquisition date"
        ) from None
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in {"tif", "tiff", "las", "laz"}:
        raise HTTPException(415, "Only local GeoTIFF, LAS or LAZ uploads accepted")
    size = 0
    h = hashlib.sha256()
    temp = terrain.root() / (str(uuid4()) + ".part")
    try:
        with open(temp, "xb") as dst:
            os.chmod(temp, 0o600)
            for block in iter(lambda: file.file.read(1024 * 1024), b""):
                size += len(block)
                if size > settings.planning_max_upload_bytes:
                    raise HTTPException(
                        413, "Terrain upload exceeds configured limit; pre-tile locally"
                    )
                h.update(block)
                dst.write(block)
        if size < 32:
            raise HTTPException(422, "Terrain file is empty or truncated")
        # Reject virtual XML and remote/archive formats before any GDAL access.
        with open(temp, "rb") as src:
            magic = src.read(4)
        if (
            ext in {"tif", "tiff"}
            and magic not in {b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"}
            or ext in {"las", "laz"}
            and magic != b"LASF"
        ):
            raise HTTPException(
                415, "File signature does not match supported local format"
            )
        sha = h.hexdigest()
        key = sha + "." + ext
        dest = terrain.root() / key
        if dest.exists():
            if dest.is_symlink() or terrain.checksum(dest) != sha:
                raise HTTPException(409, "Stored terrain integrity conflict")
        else:
            os.rename(temp, dest)

        class BoundUpload(DatasetInput):
            content_sha256: str

        bound = BoundUpload(**b.model_dump(), content_sha256=sha)
        return change(
            db,
            u,
            bound,
            "terrain_upload",
            lambda: s.upload_record(db, u, b, key, sha, size, ext),
        )
    finally:
        if temp.exists():
            temp.unlink()
        file.file.close()


@router.post("/datasets/{id}/retire")
def retire(id: int, b: Mutation, db=Depends(get_db), u=Depends(access)):
    def call():
        d = s.get(db, Dataset, id)
        if d.state not in {"ready", "retired"}:
            raise ValueError("Only ready terrain may be retired")
        d.state = "retired"
        return {"id": id, "state": "retired", "historical_results_retained": True}

    return change(db, u, b, "retire_terrain", call)


@router.post("/scenarios")
def scenario(b: ScenarioInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "scenario", lambda: s.create_scenario(db, u, b))


@router.get("/scenarios")
def scenarios(
    offset: int = Query(0, ge=0, le=10000), db=Depends(get_db), u=Depends(access)
):
    return read(
        db,
        u,
        lambda: {
            "items": [
                s.view(r)
                for r in db.scalars(
                    select(Scenario)
                    .order_by(Scenario.id.desc())
                    .offset(offset)
                    .limit(50)
                )
            ]
        },
    )


@router.get("/jobs")
def jobs(offset: int = Query(0, ge=0, le=10000), db=Depends(get_db), u=Depends(access)):
    return read(
        db,
        u,
        lambda: {
            "items": [
                {k: v for k, v in s.view(r).items() if k != "result"}
                for r in db.scalars(
                    select(Job).order_by(Job.id.desc()).offset(offset).limit(50)
                )
            ]
        },
    )


@router.get("/jobs/{id}")
def job(id: int, db=Depends(get_db), u=Depends(access)):
    return read(db, u, lambda: s.job_view(db, id))


@router.post("/jobs/{id}/cancel")
def cancel(id: int, b: Mutation, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "cancel", lambda: s.cancel(db, u, id))


@router.post("/scenarios/{id}/retry")
def retry(id: int, b: Mutation, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, b, "retry", lambda: s.view(s.queue(db, u, s.get(db, Scenario, id)))
    )


@router.get("/compare")
def compare(
    a: int = Query(ge=1), b: int = Query(ge=1), db=Depends(get_db), u=Depends(access)
):
    return read(db, u, lambda: s.compare(db, a, b))


@router.get("/qualify")
def qualify(
    latitude: float = Query(ge=-85, le=85, allow_inf_nan=False),
    longitude: float = Query(ge=-180, le=180, allow_inf_nan=False),
    db=Depends(get_db),
    u=Depends(access),
):
    return read(db, u, lambda: s.qualification(db, latitude, longitude))


@router.get("/jobs/{id}/report")
def report(id: int, db=Depends(get_db), u=Depends(access)):
    data = read(db, u, lambda: s.job_view(db, id))
    return Response(
        json.dumps(data, sort_keys=True, allow_nan=False),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="rf-planning-{id}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/calibrations")
def calibration(b: CalibrationInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "calibration", lambda: s.calibration(db, u, b))


@router.get("/calibrations")
def calibrations(
    offset: int = Query(0, ge=0, le=10000), db=Depends(get_db), u=Depends(access)
):
    return read(
        db,
        u,
        lambda: {
            "items": [
                s.view(r)
                for r in db.scalars(
                    select(Calibration)
                    .order_by(Calibration.id.desc())
                    .offset(offset)
                    .limit(50)
                )
            ]
        },
    )


@router.post("/datasets/{id}/review-retry")
def retry_import(id: int, b: DatasetInput, db=Depends(get_db), u=Depends(access)):
    def call():
        d = db.scalar(select(Dataset).where(Dataset.id == id).with_for_update())
        if not d:
            raise LookupError("Dataset unavailable")
        if d.state != "failed":
            raise ValueError(
                "Only failed imports may receive an explicit metadata correction"
            )
        previous = {
            "name": d.name,
            "kind": d.kind,
            "units": d.units,
            "vertical_datum": d.vertical_datum,
            "attribution": d.attribution,
            "acquired_on": d.acquired_on,
            "metadata": d.metadata_json,
        }
        if len(json.dumps(previous)) > 30000:
            raise ValueError(
                "Import correction history limit; archive failed source for reviewed offline reimport"
            )
        d.name = b.name
        d.kind = b.kind
        d.units = b.units
        d.vertical_datum = b.vertical_datum
        d.attribution = b.attribution
        d.acquired_on = b.acquired_on.isoformat()
        d.metadata_json = {
            "expected_bbox": b.expected_bbox,
            "previous_failed_import": previous,
        }
        d.state = "queued"
        j = Job(kind="import", dataset_id=d.id, created_by=u.id)
        db.add(j)
        db.flush()
        return {"id": d.id, "job_id": j.id, "state": "queued"}

    return change(db, u, b, "review_retry_import", call)
