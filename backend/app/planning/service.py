import hashlib, json, math
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, func
from app.field.models import now
from app.field import service as audit
from app.models.entities import Site, Sector, Device, RFAnalyticsPoint
from app.equipment.models import Asset
from .models import Dataset, Tile, Profile, Scenario, Job, Calibration
from .contracts import ScenarioInput
from .physics import ENGINE, G

CATALOG = (
    [
        ("Ubiquiti", m)
        for m in ["Rocket AC/Gen2", "PowerBeam", "AirFiber AF11", "Wave Pro", "Wave LR"]
    ]
    + [("Cambium", m) for m in ["ePMP 4600c", "ePMP 4625"]]
    + [("Racom", "Ray3"), ("Aviat", "Microwave")]
)


def digest(data):
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def get(db, cls, id):
    r = db.get(cls, id)
    if r is None:
        raise LookupError("Planning record unavailable")
    return r


def reader(u):
    if u.role not in {"admin", "operator"}:
        raise PermissionError("Private RF planning requires operator access")


def view(row):
    # Never expose filesystem paths, leases or private keys.
    return {
        c.name: (
            getattr(row, c.name).isoformat()
            if hasattr(getattr(row, c.name), "isoformat")
            else getattr(row, c.name)
        )
        for c in row.__table__.columns
        if c.name not in {"storage_key", "lease_token", "lease_until"}
    }


def datasets(db, offset=0):
    rows = db.scalars(
        select(Dataset).order_by(Dataset.id.desc()).offset(offset).limit(51)
    ).all()
    return {
        "items": [view(x) for x in rows[:50]],
        "next_offset": offset + 50 if len(rows) > 50 else None,
    }


def create_profile(db, u, b):
    r = Profile(
        name=b.name,
        manufacturer=b.manufacturer,
        model=b.model,
        technology=b.technology,
        parameters=b.parameters.model_dump(),
        evidence=b.evidence,
        created_by=u.id,
    )
    db.add(r)
    db.flush()
    return view(r)


def upload_record(db, u, b, key, sha, size, format):
    old = db.scalar(select(Dataset).where(Dataset.sha256 == sha))
    if old:
        if any(
            [
                old.kind != b.kind,
                old.units != b.units,
                old.vertical_datum != b.vertical_datum,
                old.acquired_on != b.acquired_on.isoformat(),
            ]
        ):
            raise ValueError("Duplicate terrain with conflicting metadata")
        return {"id": old.id, "duplicate": True, "state": old.state}
    if (
        db.scalar(
            select(func.count())
            .select_from(Dataset)
            .where(Dataset.state.in_(["queued", "processing"]))
        )
        >= 8
    ):
        raise ValueError("Terrain import queue full")
    r = Dataset(
        name=b.name,
        storage_key=key,
        sha256=sha,
        bytes=size,
        format=format,
        kind=b.kind,
        units=b.units,
        vertical_datum=b.vertical_datum,
        attribution=b.attribution,
        acquired_on=b.acquired_on.isoformat(),
        created_by=u.id,
        metadata_json={"expected_bbox": b.expected_bbox},
    )
    db.add(r)
    db.flush()
    j = Job(kind="import", dataset_id=r.id, created_by=u.id)
    db.add(j)
    db.flush()
    return {"id": r.id, "job_id": j.id, "state": r.state, "duplicate": False}


def source_snapshot(db, emitter):
    e = dict(emitter)
    refs = {}
    site = get(db, Site, e["site_id"]) if e.get("site_id") else None
    if site:
        refs["site"] = {
            "id": site.id,
            "latitude": site.latitude,
            "longitude": site.longitude,
            "tower_height_ft": site.tower_height_ft,
        }
        if site.latitude is None or site.longitude is None:
            raise ValueError(
                "Existing site coordinates unavailable; use separate proposed scenario"
            )
        if (
            abs(site.latitude - e["latitude"]) > 1e-5
            or abs(site.longitude - e["longitude"]) > 1e-5
        ):
            raise ValueError(
                "Existing site location mismatch; proposed coordinates require unlinked scenario"
            )
    if e.get("sector_id"):
        r = get(db, Sector, e["sector_id"])
        if not site or r.site_id != site.id:
            raise ValueError("Sector/site mismatch")
        if e.get("device_id") and r.device_id != e["device_id"]:
            raise ValueError("Sector/device mismatch")
        refs["sector"] = {
            k: getattr(r, k)
            for k in [
                "id",
                "site_id",
                "device_id",
                "frequency_mhz",
                "channel_width_mhz",
                "azimuth",
                "beamwidth",
                "capacity_mbps",
            ]
        }
    if e.get("device_id"):
        r = get(db, Device, e["device_id"])
        if site and r.site_id != site.id:
            raise ValueError("Device/site mismatch")
        refs["device"] = {"id": r.id, "site_id": r.site_id}
    if e.get("asset_id"):
        r = get(db, Asset, e["asset_id"])
        if site and audit.asset_site(db, r) != site.id:
            raise ValueError(
                "Asset installation site differs from emitter; use a separate proposed profile"
            )
        refs["asset"] = {
            "id": r.id,
            "revision": r.revision,
            "location_id": r.location_id,
            "state": r.state,
        }
    return refs


def create_scenario(db, u, b):
    inputs = b.model_dump(mode="json", exclude={"operation_key", "reason", "name"})
    sources = []
    for id in b.dataset_ids:
        d = get(db, Dataset, id)
        if d.state != "ready":
            raise ValueError("Only validated ready datasets can be used")
        sources.append(
            {
                "id": d.id,
                "sha256": d.sha256,
                "raster_key": d.metadata_json["raster_key"],
                "raster_sha256": d.metadata_json["raster_sha256"],
                "kind": d.kind,
                "units": d.units,
                "vertical_datum": d.vertical_datum,
            }
        )
    if len({d["vertical_datum"] for d in sources}) > 1:
        raise ValueError("Vertical datums differ; approved transformation required")
    # Priority is explicitly the submitted dataset order, never invented resolution precedence.
    inputs["terrain"] = sources
    inputs["profiles"] = []
    inputs["inventory"] = []
    inputs["engine"] = ENGINE
    for e in inputs["emitters"]:
        p = get(db, Profile, e["profile_id"])
        inputs["profiles"].append(
            {
                "id": p.id,
                "parameters": p.parameters,
                "evidence": p.evidence,
                "model": p.model,
            }
        )
        inputs["inventory"].append(source_snapshot(db, e))
    r = Scenario(
        name=b.name, inputs=inputs, fingerprint=digest(inputs), created_by=u.id
    )
    db.add(r)
    db.flush()
    j = queue(db, u, r)
    return {"id": r.id, "job_id": j.id, "fingerprint": r.fingerprint}


def current(db, scenario):
    i = scenario.inputs
    for d in i["terrain"]:
        r = get(db, Dataset, d["id"])
        if (
            r.state != "ready"
            or r.sha256 != d["sha256"]
            or r.metadata_json.get("raster_sha256") != d["raster_sha256"]
        ):
            return False
    return all(
        source_snapshot(db, e) == refs for e, refs in zip(i["emitters"], i["inventory"])
    )


def queue(db, u, scenario):
    if not current(db, scenario):
        raise ValueError("Source inventory/terrain changed; create a reviewed scenario")
    old = db.scalar(
        select(Job)
        .where(
            Job.scenario_id == scenario.id,
            Job.state.in_(["queued", "running", "completed"]),
        )
        .order_by(Job.id.desc())
        .limit(1)
    )
    if old:
        return old
    if (
        db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.state.in_(["queued", "running"]))
        )
        >= 16
    ):
        raise ValueError("Planning queue full")
    r = Job(kind="calculate", scenario_id=scenario.id, created_by=u.id)
    db.add(r)
    db.flush()
    return r


def cancel(db, u, id):
    r = db.scalar(select(Job).where(Job.id == id).with_for_update())
    if r is None:
        raise LookupError("Planning job unavailable")
    if r.state in {"completed", "failed", "cancelled"}:
        return view(r)
    r.cancel_requested = True
    if r.state == "queued":
        r.state = "cancelled"
        r.finished_at = now()
        if r.dataset_id:
            get(db, Dataset, r.dataset_id).state = "failed"
    return view(r)


def job_view(db, id):
    j = get(db, Job, id)
    out = view(j)
    if j.scenario_id:
        scenario = get(db, Scenario, j.scenario_id)
        try:
            valid = current(db, scenario)
        except (LookupError, ValueError):
            valid = False
        out["sources_current"] = valid
        out["scenario_name"] = scenario.name
        out["qualification_enabled"] = scenario.inputs["qualification_enabled"]
        # Result remains historical evidence, but never a current qualification after invalidation.
    return out


def capacity(db, emitter):
    from app.unified.models import Node, Edge
    from app.unified.service import capacity as capacity_view

    sid = emitter.get("sector_id")
    did = emitter.get("device_id")
    sector = db.get(Sector, sid) if sid else None
    from sqlalchemy import or_

    predicates = []
    if sid:
        predicates.append(Node.sector_id == sid)
    if did:
        predicates.append(Node.device_id == did)
    if emitter.get("site_id"):
        predicates.append(Node.site_id == emitter["site_id"])
    ids = (
        db.scalars(select(Node.id).where(or_(*predicates))).all() if predicates else []
    )
    edges = (
        db.scalars(
            select(Edge.id)
            .where((Edge.a_id.in_(ids)) | (Edge.z_id.in_(ids)))
            .limit(101)
        ).all()
        if ids
        else []
    )
    # Existing engine provides measured/configured/estimated separation; no polling.
    evidence = capacity_view(db, edges[:100]) if edges else []
    congested = any(
        v.get("measurement_state") == "fresh"
        and v.get("utilization_pct") is not None
        and v["utilization_pct"] >= 90
        for v in evidence
    )
    return {
        "capacity_limitation_evidence": congested,
        "sector_id": sid,
        "configured_sector_capacity_mbps": sector.capacity_mbps if sector else None,
        "unified_capacity": evidence,
        "scope_truncated": len(edges) > 100,
        "meaning": "Configured capacity is not measured spare demand or guaranteed customer speed",
    }


def qualification(db, lat, lon):
    rows = db.execute(
        select(Job, Scenario)
        .join(Scenario, Scenario.id == Job.scenario_id)
        .where(Job.state == "completed")
        .order_by(Job.id.desc())
        .limit(101)
    ).all()
    candidates = []
    for j, s in rows[:100]:
        i = s.inputs
        if not i["qualification_enabled"] or i["kind"] != "coverage":
            continue
        b = i["bbox"]
        if not (b[0] <= lon <= b[2] and b[1] <= lat <= b[3]):
            continue
        try:
            valid = current(db, s)
        except (LookupError, ValueError):
            valid = False
        if not valid:
            continue
        points = j.result.get("points", [])
        if not points:
            continue
        p = min(
            points,
            key=lambda p: (p["longitude"] - lon) ** 2 + (p["latitude"] - lat) ** 2,
        )
        capacity_evidence = [capacity(db, e) for e in i["emitters"]]
        candidates.append(
            {
                "job_id": j.id,
                "scenario_id": s.id,
                "predicted": True,
                "status": (
                    "capacity_limitation"
                    if any(c["capacity_limitation_evidence"] for c in capacity_evidence)
                    else p["status"]
                ),
                "predicted_dbm": p["best_predicted_dbm"],
                "sample_location": [p["longitude"], p["latitude"]],
                "grid_is_approximate": True,
                "capacity": capacity_evidence,
                "uncertainty": [
                    "Nearest modeled grid point, not exact premises LOS; physical survey required",
                    "No automatic application approval, coverage guarantee or provisioning",
                ],
            }
        )
        if len(candidates) >= 3:
            break
    return {
        "preliminary": True,
        "candidates": candidates,
        "status": "site_survey_recommended" if candidates else "insufficient_data",
        "search_truncated": len(rows) > 100,
        "engine": ENGINE,
    }


def compare(db, a, b):
    ja = get(db, Job, a)
    jb = get(db, Job, b)
    if (
        ja.state != "completed"
        or jb.state != "completed"
        or not ja.scenario_id
        or not jb.scenario_id
    ):
        raise ValueError("Completed coverage jobs required")
    sa = get(db, Scenario, ja.scenario_id)
    sb = get(db, Scenario, jb.scenario_id)
    if any(
        sa.inputs[k] != sb.inputs[k]
        for k in [
            "kind",
            "bbox",
            "grid_size",
            "receiver_height_m",
            "rx_gain_dbi",
            "rx_loss_db",
        ]
    ):
        raise ValueError("Comparison requires matching grid/receiver geometry")
    if sa.inputs["kind"] != "coverage":
        raise ValueError("Coverage comparison only")
    points = []
    for x, y in zip(ja.result["points"], jb.result["points"]):
        if x["longitude"] != y["longitude"] or x["latitude"] != y["latitude"]:
            raise ValueError("Grid mismatch")
        points.append(
            {
                "longitude": x["longitude"],
                "latitude": x["latitude"],
                "before": x["status"],
                "after": y["status"],
                "delta_predicted_db": (
                    None
                    if x["best_predicted_dbm"] is None
                    or y["best_predicted_dbm"] is None
                    else round(y["best_predicted_dbm"] - x["best_predicted_dbm"], 3)
                ),
            }
        )
    return {
        "predicted": True,
        "a": a,
        "b": b,
        "points": points,
        "assumptions_differ": [k for k in sa.inputs if sa.inputs[k] != sb.inputs[k]],
        "no_field_validation": True,
    }


def calibration(db, u, b):
    j = get(db, Job, b.job_id)
    if j.state != "completed" or not j.scenario_id:
        raise ValueError("Completed prediction required")
    s = get(db, Scenario, j.scenario_id)
    try:
        p = j.result["points"][b.point_index]["rays"][b.emitter_index]
    except (IndexError, KeyError):
        raise ValueError("Prediction sample unavailable")
    point = j.result["points"][b.point_index]
    distance = G.inv(
        point["longitude"], point["latitude"], b.measured_longitude, b.measured_latitude
    )[2]
    if distance > 25:
        raise ValueError(
            "Measured receiver must align within25m of selected prediction"
        )
    pred = p["predicted_dbm"]
    if pred is None:
        raise ValueError("No predicted signal to calibrate")
    if b.rf_point_id:
        r = get(db, RFAnalyticsPoint, b.rf_point_id)
        emitter = s.inputs["emitters"][b.emitter_index]
        # Operator must identify a receiver series explicitly; TX-local RSSI is not remote receive signal.
        if (
            not r.valid
            or r.signal_dbm is None
            or b.observed_dbm is not None
            or r.device_id != b.receiver_device_id
            or r.sampled_at > now()
        ):
            raise ValueError(
                "Valid original measured RF signal required; do not override"
            )
        observed = r.signal_dbm
    else:
        from app.signup.models import Survey, Application
        from app.signup import crypto

        r = get(db, Survey, b.survey_id)
        details = crypto.unseal(
            get(db, Application, r.application_id), r.details_ciphertext
        )
        if (
            not details.get("signal_observation")
            or details.get("latitude") is None
            or details.get("longitude") is None
        ):
            raise ValueError("Survey has no measured signal/location evidence")
        if (
            G.inv(
                details["longitude"],
                details["latitude"],
                b.measured_longitude,
                b.measured_latitude,
            )[2]
            > 25
        ):
            raise ValueError("Survey receiver location mismatch")
        if b.observed_dbm is None:
            raise ValueError("Explicit technician-measured survey RSSI required")
        observed = b.observed_dbm
    row = Calibration(
        job_id=b.job_id,
        point_index=b.point_index,
        emitter_index=b.emitter_index,
        rf_point_id=b.rf_point_id,
        survey_id=b.survey_id,
        predicted_dbm=pred,
        observed_dbm=observed,
        error_db=observed - pred,
        evidence=b.evidence,
        created_by=u.id,
    )
    db.add(row)
    db.flush()
    return {
        **view(row),
        "meaning": "Operator-reviewed alignment of receiver, location, time and model; no automatic parameter tuning",
    }
