"""Rollback-only40-POP synthetic CPU/raster/query timings, not real coverage accuracy."""

import json, time, resource, tempfile
from pathlib import Path
from uuid import uuid4
import numpy as np, rasterio
from rasterio.transform import from_origin
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Site, User
from .models import Dataset, Scenario
from .contracts import *
from .service import create_profile, create_scenario, get
from .terrain import validate_raster, checksum
from .compute import calculate


def main():
    old = settings.planning_storage_root
    try:
        with tempfile.TemporaryDirectory(prefix="noc105-benchmark-") as tmp:
            settings.planning_storage_root = tmp
            path = Path(tmp) / "ground.tif"
            with rasterio.open(
                path,
                "w",
                driver="GTiff",
                width=400,
                height=400,
                count=1,
                dtype="float32",
                crs="EPSG:4326",
                transform=from_origin(-83.6, 41.6, 0.0005, 0.0005),
                nodata=-9999,
            ) as out:
                out.write(np.full((400, 400), 200, dtype="float32"), 1)
            meta = validate_raster(path, [-83.6, 41.4, -83.4, 41.6])
            meta.pop("tiles")
            meta.update(raster_key=path.name, raster_sha256=checksum(path))
            with SessionLocal() as db:
                u = User(
                    username="planning-benchmark-" + str(uuid4()),
                    email="planning-benchmark@example.invalid",
                    password_hash="unusable",
                    role="admin",
                    is_active=True,
                )
                db.add(u)
                db.flush()
                d = Dataset(
                    name="Synthetic ground",
                    sha256=checksum(path),
                    storage_key=path.name,
                    format="tif",
                    kind="ground",
                    units="metres",
                    vertical_datum="Synthetic datum",
                    attribution="Synthetic benchmark only",
                    acquired_on="2025-01-01",
                    state="ready",
                    bytes=path.stat().st_size,
                    metadata_json=meta,
                    created_by=u.id,
                )
                db.add(d)
                db.flush()
                p = create_profile(
                    db,
                    u,
                    ProfileInput(
                        operation_key=uuid4(),
                        reason="Synthetic benchmark only",
                        name=str(uuid4()),
                        manufacturer="Synthetic",
                        model="Analytical test",
                        technology="wireless",
                        parameters=Parameters(
                            frequency_mhz=5800,
                            tx_power_dbm=20,
                            tx_gain_dbi=20,
                            tx_loss_db=1,
                            receiver_sensitivity_dbm=-90,
                        ),
                        evidence="Synthetic mathematical fixture",
                    ),
                )
                emitters = []
                for i in range(40):
                    site = Site(
                        name="Planning benchmark " + str(uuid4()),
                        latitude=41.48 + (i // 8) * 0.004,
                        longitude=-83.54 + (i % 8) * 0.004,
                    )
                    db.add(site)
                    db.flush()
                    emitters.append(
                        Emitter(
                            profile_id=p["id"],
                            site_id=site.id,
                            latitude=site.latitude,
                            longitude=site.longitude,
                            height_m=40,
                            azimuth_deg=90,
                            horizontal_beamwidth_deg=360,
                            evidence="Synthetic benchmark fixture",
                        )
                    )
                for grid in [8, 16]:
                    total = 0
                    start = time.perf_counter()
                    record_ms = 0
                    for first in range(0, 40, 16):
                        t = time.perf_counter()
                        r = create_scenario(
                            db,
                            u,
                            ScenarioInput(
                                operation_key=uuid4(),
                                reason="Synthetic benchmark only",
                                name="Synthetic40 POP group",
                                kind="coverage",
                                dataset_ids=[d.id],
                                emitters=emitters[first : first + 16],
                                bbox=[-83.53, 41.47, -83.47, 41.53],
                                grid_size=grid,
                                profile_samples=33,
                                receiver_height_m=40,
                                rx_gain_dbi=20,
                                rx_loss_db=1,
                            ),
                        )
                        record_ms += (time.perf_counter() - t) * 1000
                        sc = get(db, Scenario, r["id"])
                        result = calculate(sc.inputs)
                        total += len(result["points"]) * len(sc.inputs["emitters"])
                    elapsed = (time.perf_counter() - start) * 1000
                    print(
                        json.dumps(
                            {
                                "pops": 40,
                                "grid_per_group": grid,
                                "groups": 3,
                                "predicted_rays": total,
                                "profile_samples_per_ray": 33,
                                "elapsed_ms": round(elapsed, 2),
                                "record_creation_ms": round(record_ms, 2),
                                "rays_per_second": round(total / (elapsed / 1000), 2),
                                "peak_rss_kib": resource.getrusage(
                                    resource.RUSAGE_SELF
                                ).ru_maxrss,
                                "raster_pixels": 160000,
                                "external_calls": 0,
                                "scope": "Single-process synthetic ground-only predictions; no field accuracy or concurrent HTTP guarantee",
                            }
                        )
                    )
                db.rollback()
    finally:
        settings.planning_storage_root = old


if __name__ == "__main__":
    main()
