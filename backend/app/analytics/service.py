"""Recorded-data queries only. No collector, network control or incident writes."""

import csv, io, json, math, hashlib, hmac, stat, os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlencode
from sqlalchemy import select, func, text, delete
from sqlalchemy.dialects.postgresql import insert
from app.core.config import settings
from app.field import service as field
from app.field.models import now
from app.models.entities import (
    Device,
    Site,
    MonitoringWorker,
    TelemetrySample,
    RFAnalyticsPoint,
)
from app.equipment.models import Asset, DeviceBinding
from .catalog import METRICS, DASHBOARDS, DEFAULT_METRICS
from .models import (
    IntegrationConfig,
    HealthCheck,
    HourlyBin,
    ProjectionState,
    ReportSnapshot,
    Provision,
    ScrapeBudget,
    HourCoverage,
)
from .contracts import ConfigInput, RangeInput

SYNTHETIC_ORIGIN = "https://grafana.synthetic.invalid"
VIEW_NAMES = (
    "metric_points",
    "hourly",
    "incidents",
    "maintenance",
    "equipment",
    "ipam_counts",
    "topology_counts",
    "calibration",
    "alerts",
    "coverage",
)


def isolated():
    return (
        settings.app_env == "staging"
        and settings.offline_mode
        and settings.synthetic_monitoring_enabled
    )


def reader(u):
    if u.role not in {"admin", "operator"}:
        raise PermissionError("Monitoring operator access required")


def admin(u):
    field.admin(u)


def get(db, cls, id):
    return field.get(db, cls, id)


def digest(x):
    return hashlib.sha256(
        json.dumps(x, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def view(x):
    return {
        c.name: (v.isoformat() if hasattr(v, "isoformat") else v)
        for c in x.__table__.columns
        for v in [getattr(x, c.name)]
    }


def event(db, u, action, id, details=None):
    field.audit(
        db,
        u.id,
        "analytics." + action,
        "private_analytics",
        id,
        details or {"recorded_data_only": True},
    )


def origin(url):
    if any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise ValueError("Control characters forbidden in Grafana URL")
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.query
        or p.fragment
        or p.port not in (None, 443, 3000, 8443)
        or "%" in url
        or "\\" in url
        or any(s in {".", ".."} for s in p.path.split("/"))
        or any(not (c.isalnum() or c in "/_-") for c in p.path)
    ):
        raise ValueError(
            "Approved HTTPS Grafana origin and safe optional subpath required"
        )
    o = p.scheme + "://" + p.netloc
    allowed = {
        a.strip() for a in settings.grafana_allowed_origins.split(",") if a.strip()
    }
    if o not in allowed and not (isolated() and o == SYNTHETIC_ORIGIN):
        raise ValueError("Grafana origin not explicitly allowlisted")
    return url.rstrip("/")


def config(db):
    r = db.get(IntegrationConfig, 1)
    if not r:
        return {
            "id": 1,
            "enabled": False,
            "server_url": "",
            "auth_mode": "session",
            "embed_enabled": False,
            "dashboards": list(DASHBOARDS),
            "export_device_ids": [],
            "export_metrics": list(DEFAULT_METRICS),
            "retention_days": 90,
            "stale_seconds": 900,
            "revision": 0,
        }
    return view(r)


def configure(db, u, b):
    admin(u)
    old = config(db)
    if old["revision"] != b.expected_revision:
        raise ValueError("Stale configuration revision")
    if b.server_url:
        origin(b.server_url)
    if b.enabled and not b.server_url:
        raise ValueError("Grafana HTTPS server required")
    if b.embed_enabled and not settings.grafana_embed_approved:
        raise ValueError("Reviewed proxy/CSP embedding approval required")
    for id in b.export_device_ids:
        get(db, Device, id)
    r = db.get(IntegrationConfig, 1)
    if not r:
        r = IntegrationConfig(id=1, updated_by=u.id)
        db.add(r)
    for k, v in b.model_dump(
        exclude={"operation_key", "reason", "expected_revision"}
    ).items():
        setattr(r, k, v)
    r.revision = old["revision"] + 1
    r.updated_by = u.id
    r.updated_at = now()
    db.flush()
    return view(r)


def bounds(q, cfg=None):
    end = q.end.astimezone(timezone.utc) if q.preset == "custom" else now()
    start = (
        q.start.astimezone(timezone.utc)
        if q.preset == "custom"
        else end
        - timedelta(
            seconds={
                "1h": 3600,
                "24h": 86400,
                "7d": 604800,
                "30d": 2592000,
                "90d": 7776000,
            }[q.preset]
        )
    )
    cutoff = now() - timedelta(days=(cfg or {}).get("retention_days", 90))
    if start < cutoff - timedelta(seconds=2):
        start = max(start, cutoff)
    if start >= end:
        raise ValueError("Range outside retained analytics window")
    return start, end


def scope(db, q):
    if q.site_id:
        get(db, Site, q.site_id)
    if q.device_id:
        d = get(db, Device, q.device_id)
        if q.site_id and d.wisp_site_id != q.site_id:
            raise ValueError(
                "Device and site selection conflict; review inventory mapping"
            )


def dashboard_link(db, u, b):
    reader(u)
    cfg = config(db)
    if (
        not cfg["enabled"]
        or b.dashboard_uid not in cfg["dashboards"]
        or b.dashboard_uid not in DASHBOARDS
    ):
        raise ValueError("Integration disabled or dashboard unapproved")
    url = origin(cfg["server_url"])
    q = b.query
    if b.asset_id:
        get(db, Asset, b.asset_id)
        binding = db.scalar(
            select(DeviceBinding).where(
                DeviceBinding.asset_id == b.asset_id, DeviceBinding.ended_at.is_(None)
            )
        )
        if not binding:
            raise ValueError("Asset has no current monitored-device binding")
        if q.device_id and q.device_id != binding.device_id:
            raise ValueError("Asset/device mapping conflict")
        q = q.model_copy(update={"device_id": binding.device_id})
    scope(db, q)
    start, end = bounds(q, cfg)
    if b.embed and not (
        cfg["embed_enabled"]
        and cfg["auth_mode"] == "auth_proxy"
        and settings.grafana_embed_approved
    ):
        raise ValueError("Embedding not approved; use authenticated dashboard link")
    params = {
        "orgId": "1",
        "from": str(int(start.timestamp() * 1000)),
        "to": str(int(end.timestamp() * 1000)),
        "var-site": str(q.site_id or 0),
        "var-device": str(q.device_id or 0),
    }
    # No returned Grafana URL is trusted. Paths/IDs come only from the local catalog.
    path = ("/d-solo/" if b.embed else "/d/") + b.dashboard_uid + "/noc"
    if b.embed:
        params.update(panelId="1", theme="dark")
    else:
        params["kiosk"] = "tv"
    result = {
        "url": url + path + "?" + urlencode(params),
        "embed": b.embed,
        "credentials_in_url": False,
        "authentication": "Existing private Grafana session or reviewed auth proxy; NOC token is never forwarded",
    }
    event(
        db,
        u,
        "dashboard_link",
        b.dashboard_uid,
        {"site_id": q.site_id, "device_id": q.device_id, "embed": b.embed},
    )
    return result


def series(db, q):
    scope(db, q)
    cfg = config(db)
    start, end = bounds(q, cfg)
    interval = max(
        60, int(math.ceil((end - start).total_seconds() / q.max_points / 60)) * 60
    )
    group_col = {"site": "coalesce(site_id,0)", "device": "device_id", "network": "0"}[
        q.group
    ]
    params = {
        "start": start,
        "end": end,
        "metric": q.metric,
        "site": q.site_id,
        "device": q.device_id,
        "seconds": interval,
    }
    filter_sql = "metric=:metric AND observed_at>=:start AND observed_at<:end AND (CAST(:site AS integer) IS NULL OR site_id=:site) AND (CAST(:device AS integer) IS NULL OR device_id=:device)"
    # Raw budgets are explicit. Long windows use already-derived hourly bins when populated.
    closed_start = start.replace(minute=0, second=0, microsecond=0)
    if closed_start < start:
        closed_start += timedelta(hours=1)
    closed_end = end.replace(minute=0, second=0, microsecond=0)
    expected = max(0, int((closed_end - closed_start).total_seconds() / 3600))
    covered = db.scalar(
        select(func.count())
        .select_from(HourCoverage)
        .where(
            HourCoverage.bucket >= closed_start,
            HourCoverage.bucket < closed_end,
            HourCoverage.complete.is_(True),
        )
    )
    use_rollup = (
        (end - start) > timedelta(days=1) and expected > 0 and covered == expected
    )
    if use_rollup:
        src = "(SELECT bucket AS observed_at,device_id,site_id,sector_id,link_id,metric,valid_count,missing_count,value_sum,minimum,maximum,last_observed FROM noc_analytics.hourly WHERE bucket>=:closed_start AND bucket<:closed_end UNION ALL SELECT observed_at,device_id,site_id,sector_id,link_id,metric,CASE WHEN value IS NULL THEN 0 ELSE 1 END,CASE WHEN value IS NULL THEN 1 ELSE 0 END,value,value,value,observed_at FROM noc_analytics.metric_points WHERE observed_at<:closed_start OR observed_at>=:closed_end) combined"
        params.update(closed_start=closed_start, closed_end=closed_end)
        time = "observed_at"
        sum_expr = "sum(value_sum)"
        count_expr = "sum(valid_count)"
        missing_expr = "sum(missing_count)"
        min_expr = "min(minimum)"
        max_expr = "max(maximum)"
        last = "max(last_observed)"
        budget = 250000
    else:
        src = "noc_analytics.metric_points"
        time = "observed_at"
        sum_expr = "sum(value)"
        count_expr = "count(value)"
        missing_expr = "count(*)-count(value)"
        min_expr = "min(value)"
        max_expr = "max(value)"
        last = "max(observed_at)"
        budget = 100000
    n = db.scalar(
        text(
            f"SELECT count(*) FROM (SELECT 1 FROM {src} WHERE {filter_sql} LIMIT {budget+1}) bounded"
        ),
        params,
    )
    if n > budget:
        raise ValueError(
            "Historical query budget exceeded; select a tower/device or prepare hourly rollups"
        )
    sql = f"SELECT date_bin(make_interval(secs=>:seconds),{time},'1970-01-01'::timestamptz) AS time,{group_col} AS scope_id,{sum_expr} AS total,{count_expr} AS valid_count,{missing_expr} AS missing_count,{min_expr} AS minimum,{max_expr} AS maximum,{last} AS last_observed FROM {src} WHERE {filter_sql} GROUP BY 1,2 ORDER BY 1,2 LIMIT 5001"
    rows = [dict(r) for r in db.execute(text(sql), params).mappings()]
    if len(rows) > 5000:
        raise ValueError(
            "Series cardinality budget exceeded; narrow filters or reduce points"
        )
    points = []
    for r in rows:
        points.append(
            {
                "time": r["time"].isoformat(),
                "scope_id": r["scope_id"],
                "value": r["total"] / r["valid_count"] if r["valid_count"] else None,
                "minimum": r["minimum"],
                "maximum": r["maximum"],
                "samples": int(r["valid_count"] or 0),
                "missing_samples": int(r["missing_count"] or 0),
                "last_observed": r["last_observed"].isoformat(),
                "state": "available" if r["valid_count"] else "missing_or_invalid",
            }
        )
    return {
        "metric": q.metric,
        "family": METRICS[q.metric][0],
        "unit": METRICS[q.metric][1],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "bucket_seconds": interval,
        "group": q.group,
        "points": points,
        "storage": "hourly_derived" if use_rollup else "native_recorded_views",
        "closed_hour_coverage": {
            "covered": covered,
            "expected": expected,
            "cache_complete": use_rollup,
        },
        "aggregation": "sample-weighted mean of recorded values; not a time-based SLA",
        "coverage": "Recorded observations only; absent buckets/metrics are unavailable, not zero or healthy",
        "mapping_caution": "Core/interface/monitor site filters use current inventory; RF/tower samples carry recorded site references",
        "automatic_actions": False,
    }


def outages(db, q):
    scope(db, q)
    start, end = bounds(q, config(db))
    params = {"start": start, "end": end, "site": q.site_id, "device": q.device_id}
    rows = (
        db.execute(
            text(
                "SELECT * FROM noc_analytics.incidents WHERE started_at<:end AND coalesce(restored_at,:end)>:start AND (CAST(:site AS integer) IS NULL OR site_id=:site) AND (CAST(:device AS integer) IS NULL OR device_id=:device) ORDER BY started_at DESC,id DESC LIMIT 1001"
            ),
            params,
        )
        .mappings()
        .all()
    )
    if len(rows) > 1000:
        raise ValueError("Incident report budget exceeded; narrow range")
    items = []
    for row in rows:
        r = dict(row)
        a = max(start, r["started_at"])
        z = min(end, r["restored_at"] or end)
        r["duration_in_range_seconds"] = (z - a).total_seconds() if z >= a else None
        r = {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in r.items()}
        r["impact_basis"] = (
            "Recorded NOC estimate; zero does not establish verified no-impact"
        )
        items.append(r)
    counts = {}
    for r in items:
        counts[r["site_id"]] = counts.get(r["site_id"], 0) + 1
    return {
        "items": items,
        "count": len(items),
        "recurring_sites": [
            {"site_id": id, "incidents": count}
            for id, count in counts.items()
            if count >= 3 and id is not None
        ],
        "source": "Authoritative NOC incidents; no Grafana alerts/incidents created",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "duration_basis": "Window-clipped recorded incident lifetimes; overlapping incidents are not summed into an SLA",
    }


def report(db, q):
    result = series(db, q)
    inc = outages(db, q)
    points = result["points"]
    recommendations = []
    if not points or not any(p["samples"] for p in points):
        recommendations.append(
            {
                "action": "Investigate missing telemetry or retention coverage",
                "evidence": {
                    "metric": q.metric,
                    "range": [result["start"], result["end"]],
                },
                "uncertainty": "No observations do not establish healthy service or an outage",
            }
        )
    if inc["recurring_sites"]:
        recommendations.append(
            {
                "action": "Investigate recurring recorded outages",
                "evidence": {
                    "sites": inc["recurring_sites"],
                    "incident_ids": [r["id"] for r in inc["items"]],
                    "range": [inc["start"], inc["end"]],
                },
                "uncertainty": "Incident recurrence does not establish physical root cause",
            }
        )
    valid = [p for p in points if p["value"] is not None]
    # Group-specific consecutive bucket evidence, not a mix of different sites/devices.
    groups = {}
    for p in valid:
        groups.setdefault(p["scope_id"], []).append(p)
    for id, group in list(groups.items())[:40]:
        recent = group[-3:]
        consecutive = len(recent) == 3 and all(
            (
                datetime.fromisoformat(recent[i]["time"])
                - datetime.fromisoformat(recent[i - 1]["time"])
            ).total_seconds()
            <= result["bucket_seconds"] * 1.5
            for i in (1, 2)
        )
        if not consecutive:
            continue
        action = None
        if q.metric in {
            "rf_utilization_percent",
            "sector_utilization_percent",
            "airtime_percent",
        } and all(p["value"] >= 85 for p in recent):
            action = "Investigate recorded congestion and documented capacity"
        if q.metric in {"rssi_dbm", "snr_db"} and all(
            p["value"] < (-75 if q.metric == "rssi_dbm" else 20) for p in recent
        ):
            action = "Review repeated RF degradation evidence"
        if q.metric == "ac_present" and len({p["value"] for p in group}) > 1:
            action = "Review recorded power-state changes"
        if action:
            recommendations.append(
                {
                    "action": action,
                    "evidence": {
                        "scope_id": id,
                        "metric": q.metric,
                        "points": recent,
                        "range": [result["start"], result["end"]],
                    },
                    "uncertainty": "Screening based on recorded samples; confirm collection quality, topology and field evidence; no remediation",
                }
            )
    return {
        "series": result,
        "outages": inc,
        "recommendations": recommendations,
        "created_at": now().isoformat(),
        "remediation_enabled": False,
        "financial_data_exported": False,
    }


def snapshot(db, u, b):
    r = report(db, b.query)
    sha = digest({k: v for k, v in r.items() if k != "created_at"})
    old = db.scalar(select(ReportSnapshot).where(ReportSnapshot.digest == sha))
    if old:
        return view(old)
    x = ReportSnapshot(digest=sha, result=r, created_by=u.id)
    db.add(x)
    db.flush()
    return view(x)


def rollup(db, u, b):
    admin(u)
    if b.start < now() - timedelta(days=config(db)["retention_days"]):
        raise ValueError("Rollup range outside configured retention")
    db.execute(text("SELECT pg_advisory_xact_lock(1080108)"))
    total = 0
    at = b.start
    while at < b.end:
        db.execute(
            text("SELECT pg_advisory_xact_lock(108,:hour)"),
            {"hour": int(at.timestamp() // 3600)},
        )
        end = at + timedelta(hours=1)
        n = db.scalar(
            text(
                "SELECT count(*) FROM (SELECT 1 FROM noc_analytics.metric_points WHERE observed_at>=:a AND observed_at<:z LIMIT 250001) b"
            ),
            {"a": at, "z": end},
        )
        if n > 250000:
            raise ValueError(
                "Hourly source budget exceeded; operator partitioning required"
            )
        # A closed-hour recomputation is idempotent and never increments replayed samples.
        rows = (
            db.execute(
                text(
                    "SELECT :a AS bucket,device_id,metric,component,min(family) AS family,min(site_id) AS site_id,min(sector_id) AS sector_id,min(link_id) AS link_id,min(mapping_basis) AS mapping_basis,count(value)::integer AS valid_count,(count(*)-count(value))::integer AS missing_count,sum(value) AS value_sum,min(value) AS minimum,max(value) AS maximum,max(observed_at) AS last_observed FROM noc_analytics.metric_points WHERE observed_at>=:a AND observed_at<:z GROUP BY device_id,metric,component LIMIT 25001"
                ),
                {"a": at, "z": end},
            )
            .mappings()
            .all()
        )
        if len(rows) > 25000:
            raise ValueError("Hourly cardinality budget exceeded")
        # Empty source cannot prove old retained aggregates were false after raw pruning.
        if rows:
            families = {r["family"] for r in rows}
            for family in families:
                current = [r for r in rows if r["family"] == family]
                previous = list(
                    db.scalars(
                        select(HourlyBin).where(
                            HourlyBin.bucket == at, HourlyBin.family == family
                        )
                    )
                )
                if sum(r.valid_count + r.missing_count for r in previous) > sum(
                    r["valid_count"] + r["missing_count"] for r in current
                ):
                    continue  # Raw pruning must not erase previously retained aggregate evidence.
                db.execute(
                    delete(HourlyBin).where(
                        HourlyBin.bucket == at, HourlyBin.family == family
                    )
                )
                for row in current:
                    db.add(HourlyBin(**dict(row)))
                total += len(current)
        coverage = db.get(HourCoverage, at)
        if not coverage:
            coverage = HourCoverage(bucket=at)
            db.add(coverage)
        coverage.complete = True
        coverage.source_points = n
        coverage.refreshed_at = now()
        at = end
    state = db.get(ProjectionState, 1)
    if not state:
        state = ProjectionState(id=1)
        db.add(state)
    state.last_success = now()
    state.last_bucket = b.end - timedelta(hours=1)
    state.last_error = None
    state.rows = total
    db.flush()
    return {
        "id": 1,
        "rows": total,
        "source": "existing recorded views",
        "device_polls": 0,
        "raw_records_deleted": 0,
        "closed_hours": int((b.end - b.start).total_seconds() / 3600),
    }


def health(db):
    cfg = config(db)
    last = db.scalar(select(HealthCheck).order_by(HealthCheck.id.desc()).limit(1))
    proj = db.get(ProjectionState, 1)
    latest = db.scalar(select(func.max(TelemetrySample.sampled_at)))
    rf_latest = db.scalar(select(func.max(RFAnalyticsPoint.sampled_at)))
    workers = list(db.scalars(select(MonitoringWorker).limit(1001)))
    return {
        "configuration": cfg,
        "last_check": view(last) if last else None,
        "grafana_live_verified": bool(
            last
            and last.mode == "live"
            and last.status == "ok"
            and last.config_revision == cfg["revision"]
            and now() - last.checked_at < timedelta(seconds=300)
        ),
        "metrics_latest": latest.isoformat() if latest else None,
        "metrics_state": (
            "unavailable"
            if not latest
            else (
                "stale"
                if now() - latest > timedelta(seconds=cfg["stale_seconds"])
                else "recorded_recently"
            )
        ),
        "rf_projection_latest": rf_latest.isoformat() if rf_latest else None,
        "rollup": view(proj) if proj else None,
        "worker_count": len(workers),
        "stale_workers": sum(
            now() - w.heartbeat_at > timedelta(seconds=90) for w in workers
        ),
        "queue_backlog": sum(w.spool_pending for w in workers),
        "native_retention": {
            "raw_days": settings.rf_history_retention_days,
            "raw_pruning_enabled": settings.rf_history_pruning_enabled,
            "rf_projection_days": settings.rf_analytics_retention_days,
            "rf_projection_pruning_enabled": settings.rf_analytics_retention_enabled,
            "derived_days": cfg["retention_days"],
        },
        "device_polling_added": False,
        "financial_metrics_shared": False,
        "embedding_preconditions_verified": False,
    }


def private_secret(path):
    p = Path(path)
    if not p.is_absolute() or any(x.is_symlink() for x in [p, *p.parents]):
        raise ValueError("Private owned credential file required")
    try:
        st = p.lstat()
    except OSError:
        raise ValueError("Private service credential unavailable") from None
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_uid != os.getuid()
        or st.st_mode & 0o077
        or st.st_size > 4096
    ):
        raise ValueError("Private0600 service credential required")
    value = p.read_text().strip()
    if len(value) < 32:
        raise ValueError("Bounded random service credential required")
    return value


def scrape_auth(db, token):
    expected = private_secret(settings.analytics_metrics_token_file)
    if not token or not hmac.compare_digest(token, expected):
        raise PermissionError("Metrics credential unavailable")
    minute = now().replace(second=0, microsecond=0)
    stmt = (
        insert(ScrapeBudget)
        .values(id=1, minute=minute, count=1)
        .on_conflict_do_update(
            index_elements=[ScrapeBudget.id],
            set_={
                "minute": minute,
                "count": __import__("sqlalchemy").case(
                    (
                        ScrapeBudget.minute == minute,
                        func.least(ScrapeBudget.count + 1, 61),
                    ),
                    else_=1,
                ),
            },
        )
        .returning(ScrapeBudget.count)
    )
    n = db.scalar(stmt)
    db.commit()
    if n > 60:
        raise ValueError("Metrics request budget exceeded")


def prometheus(db):
    cfg = config(db)
    ids = cfg["export_device_ids"]
    metrics = cfg["export_metrics"]
    if not ids:
        return "# No explicitly approved devices; telemetry unavailable\n"
    params = {
        "ids": ids,
        "metrics": metrics,
        "after": now() - timedelta(seconds=cfg["stale_seconds"]),
    }
    rows = (
        db.execute(
            text(
                "SELECT DISTINCT ON(device_id,metric,component) device_id,site_id,metric,component,value,observed_at FROM noc_analytics.metric_points WHERE device_id=ANY(:ids) AND metric=ANY(:metrics) AND observed_at>=:after ORDER BY device_id,metric,component,observed_at DESC,source_id DESC LIMIT 5001"
            ),
            params,
        )
        .mappings()
        .all()
    )
    if len(rows) > 5000:
        raise ValueError(
            "Metrics cardinality budget exceeded; reduce approved devices or metrics"
        )
    lines = [
        "# Recorded NOC telemetry only. Missing/stale values omitted; no customer labels."
    ]
    for metric in metrics:
        name = "noc_" + metric
        counter = metric.startswith("interface_") and metric.endswith(
            ("bytes", "errors")
        )
        lines += [
            "# HELP "
            + name
            + " Recorded "
            + metric
            + " ("
            + METRICS[metric][1]
            + "); missing is unavailable.",
            "# TYPE " + name + (" counter" if counter else " gauge"),
        ]
        for r in rows:
            if r["metric"] != metric or r["value"] is None:
                continue
            labels = f'device_id="{r["device_id"]}",site_id="{r["site_id"] or 0}",component="{r["component"]}"'
            lines.append(f'{name}{{{labels}}} {r["value"]}')
            lines.append(
                f'noc_metric_last_observed_seconds{{{labels},metric="{metric}"}} {r["observed_at"].timestamp()}'
            )
    return "\n".join(lines) + "\n"


def csv_report(r):
    f = io.StringIO()
    w = csv.writer(f)
    w.writerow(
        [
            "time_utc",
            "scope_id",
            "metric",
            "value",
            "unit",
            "recorded_samples",
            "missing_samples",
        ]
    )
    for p in r["series"]["points"]:
        w.writerow(
            [
                p["time"],
                p["scope_id"],
                r["series"]["metric"],
                p["value"] if p["value"] is not None else "",
                r["series"]["unit"],
                p["samples"],
                p["missing_samples"],
            ]
        )
    return f.getvalue()
