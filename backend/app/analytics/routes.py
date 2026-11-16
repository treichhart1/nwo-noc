from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from app.core.database import get_db
from app.field.routes import access, run
from app.field import service as field
from . import service as s, grafana, provision
from .models import *
from .contracts import *
from .catalog import METRICS, DASHBOARDS

router = APIRouter(
    prefix="/api/analytics/v1",
    tags=["Private monitoring analytics"],
    dependencies=[Depends(access)],
)
scrape_router = APIRouter(
    prefix="/api/analytics-scrape/v1", tags=["Scoped monitoring metrics"]
)


def read(db, u, fn):
    return run(db, u, lambda: (s.reader(u), fn())[1])


def change(db, u, b, name, fn, administrator=False):
    def call():
        (s.admin if administrator else s.reader)(u)
        return field.mutation(db, u, "analytics:" + name, b, fn)

    return run(db, u, call, True)


@router.get("/me")
def me(u=Depends(access)):
    s.reader(u)
    return {
        "role": u.role,
        "device_polling_added": False,
        "mock_health_available": s.isolated(),
        "financial_metrics_shared": False,
        "live_health_enabled": s.settings.grafana_live_health_enabled
        and not s.settings.offline_mode,
    }


@router.get("/catalog")
def catalog(db=Depends(get_db), u=Depends(access)):
    return read(
        db,
        u,
        lambda: {
            "metrics": {k: {"family": v[0], "unit": v[1]} for k, v in METRICS.items()},
            "dashboards": [{"uid": id, "title": v[0]} for id, v in DASHBOARDS.items()],
        },
    )


@router.get("/configuration")
def configuration(db=Depends(get_db), u=Depends(access)):
    return read(db, u, lambda: s.config(db))


@router.post("/configuration")
def configure(b: ConfigInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "configure", lambda: s.configure(db, u, b), True)


@router.get("/health")
def health(db=Depends(get_db), u=Depends(access)):
    return read(db, u, lambda: s.health(db))


@router.post("/health")
def check(b: HealthInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "health", lambda: grafana.check(db, u, b), True)


@router.post("/dashboard-link")
def link(b: LinkInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "link", lambda: s.dashboard_link(db, u, b))


@router.post("/report")
def report(b: RangeInput, db=Depends(get_db), u=Depends(access)):
    def call():
        r = s.report(db, b)
        s.event(
            db,
            u,
            "query",
            b.metric,
            {"preset": b.preset, "site_id": b.site_id, "device_id": b.device_id},
        )
        db.commit()
        return r

    return read(db, u, call)


@router.post("/reports")
def snapshot(b: ReportInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "snapshot", lambda: s.snapshot(db, u, b))


@router.get("/reports")
def reports(
    offset: int = Query(0, ge=0, le=10000), db=Depends(get_db), u=Depends(access)
):
    return read(
        db,
        u,
        lambda: {
            "items": [
                s.view(x)
                for x in db.scalars(
                    select(ReportSnapshot)
                    .order_by(ReportSnapshot.id.desc())
                    .offset(offset)
                    .limit(25)
                )
            ]
        },
    )


@router.post("/export")
def export(b: RangeInput, db=Depends(get_db), u=Depends(access)):
    def call():
        r = s.report(db, b)
        s.event(
            db,
            u,
            "csv_export",
            b.metric,
            {"start": r["series"]["start"], "end": r["series"]["end"]},
        )
        db.commit()
        return Response(
            s.csv_report(r),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="noc-recorded-analytics.csv"',
                "Cache-Control": "no-store",
            },
        )

    return read(db, u, call)


@router.get("/metrics")
def metrics(db=Depends(get_db), u=Depends(access)):
    return read(
        db,
        u,
        lambda: Response(
            s.prometheus(db),
            media_type="text/plain; version=0.0.4",
            headers={"Cache-Control": "no-store"},
        ),
    )


@scrape_router.get("/metrics")
def scrape(request: Request, db=Depends(get_db)):
    token = request.headers.get("authorization", "")
    try:
        if not token.startswith("Bearer "):
            raise PermissionError()
        s.scrape_auth(db, token[7:])
        db.execute(text("SET LOCAL statement_timeout='3000ms'"))
        return Response(
            s.prometheus(db),
            media_type="text/plain; version=0.0.4",
            headers={"Cache-Control": "no-store"},
        )
    except (PermissionError, ValueError, OSError):
        db.rollback()
        raise HTTPException(
            403, "Scoped metrics credential or export configuration unavailable"
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(503, "Recorded metrics storage unavailable") from None


@router.post("/rollups")
def rollups(b: RollupInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "rollup", lambda: s.rollup(db, u, b), True)


@router.post("/retention")
def retention(b: Mutation, db=Depends(get_db), u=Depends(access)):
    def call():
        cutoff = s.now() - __import__("datetime").timedelta(
            days=s.config(db)["retention_days"]
        )
        n = db.execute(
            text(
                "DELETE FROM analytics_hourly_bins WHERE ctid IN (SELECT ctid FROM analytics_hourly_bins WHERE bucket<:cutoff ORDER BY bucket LIMIT 1000)"
            ),
            {"cutoff": cutoff},
        ).rowcount
        return {
            "id": 1,
            "derived_rows_deleted": n,
            "raw_rows_deleted": 0,
            "incidents_deleted": 0,
        }

    return change(db, u, b, "retention", call, True)


@router.post("/provision")
def bundle(b: Mutation, db=Depends(get_db), u=Depends(access)):
    def call():
        cfg = s.config(db)
        data = provision.bundle(cfg["dashboards"])
        sha = s.digest(data)
        p = Provision(
            digest=sha,
            config_revision=cfg["revision"],
            dashboard_uids=cfg["dashboards"],
            state="exported_only",
            created_by=u.id,
        )
        db.add(p)
        db.flush()
        return {
            "id": p.id,
            "digest": sha,
            "state": "exported_only",
            "bundle": data,
            "remote_writes": 0,
            "secrets_included": False,
        }

    return change(db, u, b, "provision", call, True)


@router.get("/context")
def context(
    site_id: int | None = None,
    device_id: int | None = None,
    db=Depends(get_db),
    u=Depends(access),
):
    def call():
        q = RangeInput(site_id=site_id, device_id=device_id)
        s.scope(db, q)
        p = {"site": site_id, "device": device_id}
        return {
            "equipment": [
                dict(r)
                for r in db.execute(
                    text(
                        "SELECT * FROM noc_analytics.equipment WHERE (CAST(:site AS integer) IS NULL OR site_id=:site) AND (CAST(:device AS integer) IS NULL OR device_id=:device) LIMIT 200"
                    ),
                    p,
                ).mappings()
            ],
            "ipam": [
                dict(r)
                for r in db.execute(
                    text(
                        "SELECT * FROM noc_analytics.ipam_counts WHERE (CAST(:site AS integer) IS NULL OR site_id=:site) LIMIT 100"
                    ),
                    p,
                ).mappings()
            ],
            "maintenance": [
                dict(r)
                for r in db.execute(
                    text(
                        "SELECT * FROM noc_analytics.maintenance WHERE (CAST(:site AS integer) IS NULL OR site_id=:site) ORDER BY serviced_at DESC LIMIT 50"
                    ),
                    p,
                ).mappings()
            ],
            "topology": [
                dict(r)
                for r in db.execute(
                    text(
                        "SELECT * FROM noc_analytics.topology_counts WHERE (CAST(:site AS integer) IS NULL OR site_id=:site) LIMIT 100"
                    ),
                    p,
                ).mappings()
            ],
            "calibration": (
                [
                    dict(r)
                    for r in db.execute(
                        text(
                            "SELECT * FROM noc_analytics.calibration ORDER BY created_at DESC LIMIT 50"
                        )
                    ).mappings()
                ]
                if u.role == "admin" and site_id is None and device_id is None
                else []
            ),
            "profitability_url": "/?network-roi=home" if u.role == "admin" else None,
            "financial_values_in_grafana": False,
        }

    return read(db, u, call)
