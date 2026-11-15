"""Synthetic local terrain and analytical acceptance: no equipment or downloads."""

import json, math
from types import SimpleNamespace
from uuid import uuid4
from datetime import timedelta
import numpy as np
import pytest, rasterio
from rasterio.transform import from_origin
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.models.entities import User, Site, Device, RFAnalyticsPoint
from app.field.models import now
from app.planning import terrain, physics, service as s, worker
from app.planning.models import Dataset, Tile, Profile, Scenario, Job, Calibration
from app.planning.contracts import *
from app.planning.compute import calculate
from test_rf_postgres_integration import postgres_db

R = "Synthetic reviewed offline RF planning documentation"


def b(cls, **kw):
    return cls(operation_key=uuid4(), reason=R, **kw)


def raster(path, values=None, crs="EPSG:4326", transform=None):
    values = np.full((100, 100), 200, dtype="float32") if values is None else values
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform or from_origin(-83.6, 41.6, 0.002, 0.002),
        nodata=-9999,
    ) as dst:
        dst.write(values, 1)
    return path


@pytest.fixture
def x(postgres_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "planning_storage_root", str(tmp_path))
    db = postgres_db
    u = User(
        username="planning-admin",
        email="planning@example.invalid",
        password_hash="unusable",
        role="admin",
        is_active=True,
    )
    db.add(u)
    db.flush()
    return SimpleNamespace(db=db, u=u, root=tmp_path)


def dataset(x, kind="ground", units="metres", values=None):
    p = raster(x.root / (str(uuid4()) + ".tif"), values)
    sha = terrain.checksum(p)
    meta = b(
        DatasetInput,
        name="Synthetic terrain",
        kind=kind,
        units=units,
        vertical_datum="Synthetic datum",
        attribution=R,
        acquired_on="2025-01-01",
        expected_bbox=[-83.6, 41.4, -83.4, 41.6],
    )
    result = s.upload_record(x.db, x.u, meta, p.name, sha, p.stat().st_size, "tif")
    x.db.commit()
    lease = worker.claim(x.db)
    worker.process(x.db, *lease, report=lambda p: None)
    return x.db.get(Dataset, result["id"])


def profile(x, **kw):
    return s.create_profile(
        x.db,
        x.u,
        b(
            ProfileInput,
            name=str(uuid4()),
            manufacturer="Synthetic",
            model="Synthetic test radio",
            technology="wireless",
            parameters=Parameters(
                **{
                    "frequency_mhz": 5800,
                    "tx_power_dbm": 20,
                    "tx_gain_dbi": 20,
                    "tx_loss_db": 1,
                    "receiver_sensitivity_dbm": -90,
                    "polarization": "dual",
                    **kw,
                }
            ),
            evidence=R,
        ),
    )


def scenario(x, d=None, **kw):
    p = profile(x)
    data = dict(
        name="Synthetic proposal",
        kind="link",
        dataset_ids=[d.id] if d else [],
        emitters=[
            Emitter(
                profile_id=p["id"],
                latitude=41.5,
                longitude=-83.55,
                height_m=40,
                azimuth_deg=90,
                horizontal_beamwidth_deg=360,
                evidence=R,
            )
        ],
        target=Point(latitude=41.5, longitude=-83.45, height_m=40, evidence=R),
        rx_gain_dbi=20,
        rx_loss_db=1,
        rx_polarization="dual",
    )
    data.update(kw)
    r = s.create_scenario(x.db, x.u, b(ScenarioInput, **data))
    x.db.commit()
    return x.db.get(Scenario, r["id"]), x.db.get(Job, r["job_id"])


def run(x, j):
    lease = worker.claim(x.db)
    assert lease[0] == j.id
    worker.process(x.db, *lease, report=lambda p: None)
    return x.db.get(Job, j.id)


@pytest.mark.parametrize(
    "frequency,distance,expected",
    [(1000, 1000, 92.447783), (5800, 10000, 127.716343), (11000, 1000, 113.275637)],
)
def test_fspl_known_cases(frequency, distance, expected):
    assert physics.fspl(distance, frequency) == pytest.approx(expected, abs=0.001)


@pytest.mark.parametrize("k", [0.5, 1, 4 / 3, 3])
def test_curvature_and_fresnel(k):
    class Flat:
        def value(self, kind, lon, lat):
            return (0, 1) if kind == "ground" else (None, None)

    i = b(
        ScenarioInput,
        name="Analytical",
        kind="link",
        emitters=[
            Emitter(
                profile_id=1,
                latitude=0,
                longitude=0,
                height_m=50,
                azimuth_deg=90,
                horizontal_beamwidth_deg=360,
                evidence=R,
            )
        ],
        target=Point(latitude=0, longitude=0.1, height_m=50, evidence=R),
        rx_gain_dbi=0,
        rx_loss_db=0,
        refraction_k=k,
    ).model_dump()
    p = Parameters(
        frequency_mhz=1000, tx_power_dbm=0, tx_gain_dbi=0, tx_loss_db=0
    ).model_dump()
    r = physics.ray(i["emitters"][0], i["target"], p, i, Flat(), True)
    mid = r["profile"][31]
    d = r["distance_m"] / 2
    assert mid["curvature_m"] == pytest.approx(d * d / (2 * k * 6371008.8), abs=0.01)
    assert mid["fresnel_m"] == pytest.approx(math.sqrt(0.299792458 * d / 2), abs=0.01)
    assert "ground_only" in r["terrain_basis"]


@pytest.mark.parametrize("v,expected", [(-1, 0), (0, 6.03285), (1, 13.9257)])
def test_single_edge(v, expected):
    assert physics.knife_edge(v) == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize("units,factor", list(terrain.FACTORS.items()))
def test_units_and_window_cache(x, units, factor):
    d = dataset(x, units=units)
    sampler = terrain.Sampler(
        [{"id": d.id, "kind": d.kind, "units": d.units, **d.metadata_json}]
    )
    assert sampler.value("ground", -83.5, 41.5)[0] == pytest.approx(200 * factor)
    assert sampler.value("surface", -83.5, 41.5) == (None, None)
    assert len(sampler.cache) == 1
    sampler.close()


def test_import_tiles_duplicate_and_metadata_conflict(x):
    d = dataset(x)
    assert d.state == "ready"
    assert d.metadata_json["crs"] == "EPSG:4326"
    assert (
        x.db.scalar(
            select(func.count()).select_from(Tile).where(Tile.dataset_id == d.id)
        )
        == 1
    )
    m = b(
        DatasetInput,
        name="Same source",
        kind="ground",
        units="metres",
        vertical_datum=d.vertical_datum,
        attribution=R,
        acquired_on=d.acquired_on,
        expected_bbox=[-83.6, 41.4, -83.4, 41.6],
    )
    assert s.upload_record(x.db, x.u, m, d.storage_key, d.sha256, d.bytes, "tif")[
        "duplicate"
    ]
    with pytest.raises(ValueError):
        s.upload_record(
            x.db,
            x.u,
            m.model_copy(update={"kind": "surface"}),
            d.storage_key,
            d.sha256,
            d.bytes,
            "tif",
        )


def test_crs_bounds_and_large_raster_guard(x, monkeypatch):
    p = raster(x.root / "no-crs.tif", crs=None)
    with pytest.raises(Exception):
        terrain.validate_raster(p, [-83.6, 41.4, -83.4, 41.6])
    p = raster(x.root / "bounds.tif")
    with pytest.raises(ValueError, match="bounds"):
        terrain.validate_raster(p, [0, 0, 1, 1])
    monkeypatch.setattr(terrain, "MAX_PIXELS", 100)
    with pytest.raises(ValueError, match="bounded"):
        terrain.validate_raster(p, [-83.6, 41.4, -83.4, 41.6])


def test_nodata_and_checksum(x):
    d = dataset(x, values=np.full((100, 100), -9999, dtype="float32"))
    snap = {"id": d.id, "kind": d.kind, "units": d.units, **d.metadata_json}
    sampler = terrain.Sampler([snap])
    assert sampler.value("ground", -83.5, 41.5) == (None, None)
    sampler.close()
    with open(terrain.local(d.storage_key), "ab") as f:
        f.write(b"alteration")
    with pytest.raises(ValueError, match="checksum"):
        terrain.Sampler([snap])


@pytest.mark.parametrize(
    "bad",
    [
        "../escape.tif",
        "https://example.invalid/a.tif",
        "/vsicurl/http://x",
        "file:///tmp/a",
    ],
)
def test_no_external_paths(bad, x):
    with pytest.raises(ValueError):
        terrain.local(bad)


def test_clear_los_and_missing_obstructions(x):
    d = dataset(x)
    sc, j = scenario(x, d)
    r = run(x, j).result["points"][0]["rays"][0]
    assert r["status"] == "potentially_serviceable"
    assert r["minimum_clearance_m"] > 0
    assert any("vegetation" in v for v in r["uncertainty"])
    assert len(r["profile"]) == 63


def test_surface_obstruction_and_diffraction(x):
    ground = dataset(x)
    values = np.full((100, 100), 200, dtype="float32")
    values[:, 49:51] = 400
    surface = dataset(x, "surface", values=values)
    sc, j = scenario(
        x, ground, dataset_ids=[ground.id, surface.id], model="dominant_knife_edge"
    )
    r = run(x, j).result["points"][0]["rays"][0]
    assert r["status"] == "obstruction_risk"
    assert r["terrain_basis"] == "ground_and_surface"
    assert r["diffraction_db"] > 0


def test_missing_terrain_and_parameters(x):
    sc, j = scenario(x)
    assert run(x, j).result["points"][0]["status"] == "insufficient_data"
    d = dataset(x)
    sc, j = scenario(x, d)
    params = dict(sc.inputs["profiles"][0]["parameters"])
    params["tx_gain_dbi"] = None
    assert (
        calculate({**sc.inputs, "profiles": [{"parameters": params}]})["points"][0][
            "best_predicted_dbm"
        ]
        is None
    )


def test_multi_sector_comparison_qualification_and_invalidation(x):
    d = dataset(x)
    p = profile(x)
    e = Emitter(
        profile_id=p["id"],
        latitude=41.5,
        longitude=-83.55,
        height_m=40,
        azimuth_deg=90,
        horizontal_beamwidth_deg=360,
        evidence=R,
    )
    sc, j = scenario(
        x,
        d,
        kind="coverage",
        target=None,
        bbox=[-83.52, 41.49, -83.48, 41.51],
        grid_size=3,
        emitters=[e, e],
        qualification_enabled=True,
    )
    j = run(x, j)
    assert len(j.result["points"]) == 9
    assert all(v["coverage_overlap"] == 2 for v in j.result["points"])
    assert s.compare(x.db, j.id, j.id)["points"][0]["delta_predicted_db"] == 0
    assert s.qualification(x.db, 41.5, -83.5)["candidates"][0]["predicted"]
    d.state = "retired"
    x.db.flush()
    assert s.qualification(x.db, 41.5, -83.5)["status"] == "insufficient_data"
    assert not s.job_view(x.db, j.id)["sources_current"]


def test_link_comparison_rejected(x):
    sc, j = scenario(x)
    run(x, j)
    with pytest.raises(ValueError, match="Coverage"):
        s.compare(x.db, j.id, j.id)


def test_inventory_source_changes_invalidate_without_changing_inventory(x):
    site = Site(
        name="Synthetic planning tower",
        latitude=41.5,
        longitude=-83.55,
        tower_height_ft=150,
    )
    x.db.add(site)
    x.db.flush()
    p = profile(x)
    sc, j = scenario(
        x,
        emitters=[
            Emitter(
                profile_id=p["id"],
                site_id=site.id,
                latitude=site.latitude,
                longitude=site.longitude,
                height_m=30,
                evidence=R,
            )
        ],
    )
    assert s.current(x.db, sc)
    site.latitude = 41.51
    x.db.flush()
    with pytest.raises(ValueError):
        s.current(x.db, sc)
    assert site.tower_height_ft == 150


def test_cancellation_and_fenced_restart_recovery(x):
    sc, j = scenario(x)
    lease = worker.claim(x.db)
    j = x.db.get(Job, j.id)
    j.lease_until = now() - timedelta(seconds=1)
    x.db.commit()
    second = worker.claim(x.db)
    assert second[1] != lease[1]
    assert x.db.get(Job, j.id).attempts == 2
    with pytest.raises(worker.Cancelled):
        worker.process(x.db, *lease, report=lambda p: None)
    s.cancel(x.db, x.u, j.id)
    x.db.commit()
    with pytest.raises(worker.Cancelled):
        worker.process(x.db, *second, report=lambda p: None)


def test_duplicate_jobs_and_native_immutable_scenario(x):
    sc, j = scenario(x)
    assert s.queue(x.db, x.u, sc).id == j.id
    with pytest.raises(DBAPIError):
        with x.db.begin_nested():
            x.db.execute(
                text("UPDATE planning_scenarios SET name=:name WHERE id=:id"),
                {"name": "mutated", "id": sc.id},
            )


@pytest.mark.parametrize(
    "change",
    [
        {"refraction_k": 0},
        {"profile_samples": 2000},
        {"grid_size": 100},
        {"bbox": [0, 0, 2, 2], "kind": "coverage", "target": None},
        {"rx_gain_dbi": float("nan")},
        {"atmospheric_loss_db_per_km": -1},
    ],
)
def test_invalid_planning_inputs(change):
    data = dict(
        name="Invalid test",
        kind="link",
        emitters=[
            Emitter(
                profile_id=1, latitude=41.5, longitude=-83.5, height_m=30, evidence=R
            )
        ],
        target=Point(latitude=41.5, longitude=-83.4, height_m=2, evidence=R),
    )
    data.update(change)
    with pytest.raises(ValidationError):
        b(ScenarioInput, **data)


def test_mount_height_and_source_bounds_validation():
    with pytest.raises(ValidationError):
        Emitter(
            profile_id=1,
            latitude=41,
            longitude=-83,
            height_m=100,
            tower_height_m=40,
            evidence=R,
        )
    with pytest.raises(ValidationError):
        b(
            DatasetInput,
            name="Bad source",
            kind="ground",
            units="metres",
            vertical_datum="test",
            attribution=R,
            acquired_on="2999-01-01",
            expected_bbox=[-83, 41, -84, 42],
        )


def auth(u):
    return {"Authorization": "Bearer " + create_access_token(u.username, user_id=u.id)}


@pytest.mark.parametrize("role", ["technician", "viewer", "operator", "admin"])
def test_api_authorization(x, role):
    x.u.role = role
    x.db.commit()
    app.dependency_overrides[get_db] = lambda: x.db
    try:
        with TestClient(app) as c:
            assert c.get("/api/planning/datasets").status_code == 401
            assert c.get("/api/planning/datasets", headers=auth(x.u)).status_code == (
                200 if role in {"operator", "admin"} else 403
            )
            p = b(
                ProfileInput,
                name="API Profile",
                manufacturer="Test",
                model="Test radio",
                technology="wireless",
                parameters=Parameters(),
                evidence=R,
            )
            assert c.post(
                "/api/planning/profiles",
                json=p.model_dump(mode="json"),
                headers=auth(x.u),
            ).status_code == (200 if role == "admin" else 403)
    finally:
        app.dependency_overrides.clear()


def test_upload_signature_limits_and_replay(x, monkeypatch):
    x.db.commit()
    app.dependency_overrides[get_db] = lambda: x.db
    p = raster(x.root / "upload.tif")
    meta = b(
        DatasetInput,
        name="API terrain",
        kind="ground",
        units="metres",
        vertical_datum="Synthetic datum",
        attribution=R,
        acquired_on="2025-01-01",
        expected_bbox=[-83.6, 41.4, -83.4, 41.6],
    )
    try:
        with TestClient(app) as c:

            def upload(data):
                return c.post(
                    "/api/planning/datasets",
                    headers=auth(x.u),
                    data={"metadata": meta.model_dump_json()},
                    files={"file": ("terrain.tif", data, "application/octet-stream")},
                )

            assert (
                upload(b"<VRTDataset>bad</VRTDataset>" + b" " * 40).status_code == 415
            )
            monkeypatch.setattr(settings, "planning_max_upload_bytes", 1024)
            assert upload(p.read_bytes()).status_code == 413
            monkeypatch.setattr(settings, "planning_max_upload_bytes", 67108864)
            a = upload(p.read_bytes())
            assert a.status_code == 200
            assert upload(p.read_bytes()).json() == a.json()
            assert not list(x.root.glob("*.part"))
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("extension", ["las", "laz"])
def test_classified_lidar_processing(x, extension):
    import laspy
    from pyproj import CRS
    from rasterio.warp import transform_bounds

    h = laspy.LasHeader(point_format=3, version="1.2")
    h.add_crs(CRS.from_epsg(32617))
    h.offsets = [280000, 4600000, 0]
    h.scales = [0.01, 0.01, 0.01]
    las = laspy.LasData(h)
    las.x = np.array([280000, 280001, 280002])
    las.y = np.array([4600000, 4600001, 4600002])
    las.z = np.array([200, 210, 220])
    las.classification = np.array([2, 2, 5], dtype="uint8")
    path = x.root / ("classified." + extension)
    las.write(path)
    expected = transform_bounds(
        "EPSG:32617", "EPSG:4326", 280000, 4600000, 280002, 4600002
    )
    result = terrain.las_to_raster(path, x.root / "derived.tif", "ground", expected)
    assert result["accepted_points"] == 2
    with rasterio.open(x.root / "derived.tif") as src:
        assert (src.read(1) != -9999).sum() == 2
    with pytest.raises(ValueError):
        terrain.las_to_raster(path, x.root / "bad.tif", "surface", [0, 0, 1, 1])


def test_interrupted_calculation_never_publishes_partial_results(x):
    d = dataset(x)
    sc, j = scenario(x, d)
    lease = worker.claim(x.db)

    def interrupted(p):
        raise worker.Cancelled()

    with pytest.raises(worker.Cancelled):
        worker.process(x.db, *lease, report=interrupted)
    assert x.db.get(Job, j.id).state == "running"
    assert x.db.get(Job, j.id).result == {}


def test_calibration_original_measurements_and_alignment(x):
    from app.services.rf_persistence import store_rf_sample

    d = dataset(x)
    sc, j = scenario(x, d)
    run(x, j)
    receiver = Device(
        name="Synthetic measured receiver",
        source="manual",
        external_id="planning-receiver",
        enabled=False,
    )
    x.db.add(receiver)
    x.db.flush()
    sample = store_rf_sample(x.db, receiver, {"rssi_dbm": -65}, sampled_at=now())
    x.db.flush()
    point = RFAnalyticsPoint(
        source_sample_id=sample.id,
        device_id=receiver.id,
        series_key="radio",
        vendor="ubiquiti",
        source_profile="synthetic",
        mapping_digest="a" * 64,
        sampled_at=now(),
        valid=True,
        signal_dbm=-65,
    )
    x.db.add(point)
    x.db.flush()
    body = b(
        CalibrationInput,
        job_id=j.id,
        point_index=0,
        emitter_index=0,
        rf_point_id=point.id,
        receiver_device_id=receiver.id,
        alignment_verified=True,
        measured_latitude=41.5,
        measured_longitude=-83.45,
        evidence=R,
    )
    result = s.calibration(x.db, x.u, body)
    assert result["observed_dbm"] == -65
    assert result["error_db"] == pytest.approx(-65 - result["predicted_dbm"])
    assert point.signal_dbm == -65
    with pytest.raises(ValueError, match="align"):
        s.calibration(x.db, x.u, body.model_copy(update={"measured_latitude": 42}))
    with pytest.raises(ValueError):
        s.calibration(x.db, x.u, body.model_copy(update={"receiver_device_id": 99999}))


def test_oversized_coverage_budget():
    e = Emitter(profile_id=1, latitude=41.5, longitude=-83.5, height_m=30, evidence=R)
    with pytest.raises(ValidationError):
        b(
            ScenarioInput,
            name="Over budget",
            kind="coverage",
            emitters=[e] * 16,
            bbox=[-83.6, 41.4, -83.4, 41.6],
            grid_size=32,
        )


def test_import_failed_metadata_correction_is_explicit_and_audited(x):
    d = dataset(x)
    d.state = "failed"
    x.db.commit()
    app.dependency_overrides[get_db] = lambda: x.db
    meta = b(
        DatasetInput,
        name="Corrected source",
        kind="ground",
        units="metres",
        vertical_datum="Synthetic datum",
        attribution=R,
        acquired_on="2025-01-01",
        expected_bbox=[-83.6, 41.4, -83.4, 41.6],
    )
    try:
        with TestClient(app) as c:
            r = c.post(
                "/api/planning/datasets/" + str(d.id) + "/review-retry",
                headers=auth(x.u),
                json=meta.model_dump(mode="json"),
            )
            assert r.status_code == 200
            x.db.expire_all()
            assert (
                x.db.get(Dataset, d.id).metadata_json["previous_failed_import"]["name"]
                == "Synthetic terrain"
            )
            assert (
                c.post(
                    "/api/planning/datasets/" + str(d.id) + "/review-retry",
                    headers=auth(x.u),
                    json=meta.model_dump(mode="json"),
                ).json()
                == r.json()
            )
    finally:
        app.dependency_overrides.clear()


def test_preparser_upload_bounds_and_worker_disabled(x):
    with TestClient(app) as c:
        assert c.post("/api/planning/datasets", content=b"x" * 30).status_code == 401
        assert (
            c.post(
                "/api/planning/datasets",
                headers={**auth(x.u), "content-length": "999999999"},
                content=b"x",
            ).status_code
            == 413
        )
    assert not settings.planning_worker_enabled
    assert worker.once() is False


def test_calculation_cancelled_before_claim_has_no_result(x):
    sc, j = scenario(x)
    s.cancel(x.db, x.u, j.id)
    x.db.commit()
    assert worker.claim(x.db) is None
    assert x.db.get(Job, j.id).state == "cancelled"
    assert not x.db.get(Job, j.id).result
