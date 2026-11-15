"""Guarded synthetic datasets and actual resource-limited worker subprocesses."""

import json
from uuid import uuid4
import numpy as np, rasterio
from rasterio.transform import from_origin
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import User
from .models import *
from .contracts import *
from . import service as s, terrain, worker

R = "Synthetic offline RF planning acceptance; not field measurements"


def b(cls, **kw):
    return cls(operation_key=uuid4(), reason=R, **kw)


def seed():
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Isolated synthetic staging required")
    settings.planning_worker_enabled = True
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == "acceptance"))
        if not admin or db.scalar(select(Scenario.id)):
            raise ValueError(
                "Acceptance administrator and empty planning fixture required"
            )
        datasets = []
        for kind in ["ground", "surface"]:
            path = terrain.root() / ("synthetic-" + kind + ".tif")
            v = np.full((100, 100), 200 if kind == "ground" else 201, dtype="float32")
            with rasterio.open(
                path,
                "w",
                driver="GTiff",
                width=100,
                height=100,
                count=1,
                dtype="float32",
                crs="EPSG:4326",
                transform=from_origin(-83.6, 41.6, 0.002, 0.002),
                nodata=-9999,
            ) as out:
                out.write(v, 1)
            m = b(
                DatasetInput,
                name="NOC105 synthetic " + kind,
                kind=kind,
                units="metres",
                vertical_datum="Synthetic common datum",
                attribution=R,
                acquired_on="2025-01-01",
                expected_bbox=[-83.6, 41.4, -83.4, 41.6],
            )
            r = s.upload_record(
                db,
                admin,
                m,
                path.name,
                terrain.checksum(path),
                path.stat().st_size,
                "tif",
            )
            datasets.append(r["id"])
            db.commit()
        for _ in datasets:
            worker.once()
        db.expire_all()
        assert all(
            db.get(Dataset, id).state == "ready" for id in datasets
        ), "Terrain subprocess failed"
        p = s.create_profile(
            db,
            admin,
            b(
                ProfileInput,
                name="Synthetic test radio",
                manufacturer="Synthetic",
                model="Analytical fixture",
                technology="wireless",
                parameters=Parameters(
                    frequency_mhz=5800,
                    tx_power_dbm=20,
                    tx_gain_dbi=20,
                    tx_loss_db=1,
                    receiver_sensitivity_dbm=-90,
                    polarization="dual",
                ),
                evidence=R,
            ),
        )
        e = Emitter(
            profile_id=p["id"],
            latitude=41.5,
            longitude=-83.55,
            height_m=40,
            azimuth_deg=90,
            horizontal_beamwidth_deg=120,
            evidence=R,
        )
        link = s.create_scenario(
            db,
            admin,
            b(
                ScenarioInput,
                name="Synthetic LOS",
                kind="link",
                dataset_ids=datasets,
                emitters=[e],
                target=Point(latitude=41.5, longitude=-83.45, height_m=40, evidence=R),
                rx_gain_dbi=20,
                rx_loss_db=1,
                rx_polarization="dual",
            ),
        )
        db.commit()
        cov = s.create_scenario(
            db,
            admin,
            b(
                ScenarioInput,
                name="Synthetic sector coverage",
                kind="coverage",
                dataset_ids=datasets,
                emitters=[e, e],
                bbox=[-83.52, 41.49, -83.48, 41.51],
                grid_size=8,
                receiver_height_m=40,
                rx_gain_dbi=20,
                rx_loss_db=1,
                rx_polarization="dual",
                qualification_enabled=True,
            ),
        )
        db.commit()
        worker.once()
        worker.once()
        db.expire_all()
        for id in [link["job_id"], cov["job_id"]]:
            assert db.get(Job, id).state == "completed", db.get(Job, id).error_code
        return {
            "datasets": datasets,
            "profile_id": p["id"],
            "link_job_id": link["job_id"],
            "coverage_job_id": cov["job_id"],
            "actual_subprocesses": 4,
            "external_calls": 0,
        }


if __name__ == "__main__":
    print(json.dumps(seed()))
