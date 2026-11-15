"""Deterministic file provisioning: no remote Grafana writes, secrets or plugins."""

import json
from pathlib import Path
from .catalog import DASHBOARDS, METRICS

DS = "noc-readonly"
FILTER = "AND (CAST(${site:sqlstring} AS integer)=0 OR site_id=CAST(${site:sqlstring} AS integer)) AND (CAST(${device:sqlstring} AS integer)=0 OR device_id=CAST(${device:sqlstring} AS integer))"


# Variables are numeric SQL results, single-value only, no arbitrary text input.
def metric_sql(metric):
    # Long-range dashboards require prepared hourly bins; never scan months of raw samples.
    return f"""WITH points AS (
SELECT observed_at,site_id,device_id,value AS total,CASE WHEN value IS NULL THEN 0 ELSE 1 END AS n
FROM noc_analytics.metric_points WHERE metric='{metric}' AND $__timeFilter(observed_at)
AND ($__unixEpochTo()-$__unixEpochFrom())<=86400 {FILTER}
UNION ALL
SELECT bucket,site_id,device_id,value_sum,valid_count FROM noc_analytics.hourly h
WHERE EXISTS(SELECT 1 FROM noc_analytics.coverage c WHERE c.bucket=h.bucket AND c.complete) AND metric='{metric}' AND $__timeFilter(bucket) AND ($__unixEpochTo()-$__unixEpochFrom())>86400 {FILTER}
) SELECT $__timeGroupAlias(observed_at,'$__interval'),sum(total)/nullif(sum(n),0) AS value,coalesce(site_id,0)::text AS metric FROM points GROUP BY 1,3 ORDER BY 1 LIMIT 5000"""


def dashboard(uid):
    title, metrics = DASHBOARDS[uid]
    panels = []
    for i, metric in enumerate(metrics):
        panels.append(
            {
                "id": i + 1,
                "title": metric + " · recorded " + METRICS[metric][1],
                "type": "timeseries",
                "datasource": {"type": "grafana-postgresql-datasource", "uid": DS},
                "gridPos": {"x": (i % 2) * 12, "y": (i // 2) * 8, "w": 12, "h": 8},
                "fieldConfig": {
                    "defaults": {"custom": {"spanNulls": False}},
                    "overrides": [],
                },
                "targets": [
                    {
                        "refId": "A",
                        "format": "time_series",
                        "rawSql": metric_sql(metric),
                        "editorMode": "code",
                        "rawQuery": True,
                    }
                ],
                "maxDataPoints": 1000,
                "interval": "1m",
                "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
            }
        )

    def table(title, sql):
        panels.append(
            {
                "id": len(panels) + 1,
                "title": title,
                "type": "table",
                "datasource": {"type": "grafana-postgresql-datasource", "uid": DS},
                "gridPos": {"x": 0, "y": len(panels) * 8, "w": 24, "h": 8},
                "targets": [
                    {
                        "refId": "A",
                        "format": "table",
                        "rawSql": sql,
                        "editorMode": "code",
                        "rawQuery": True,
                    }
                ],
            }
        )

    table(
        "Hourly cache coverage · missing or incomplete hours are unavailable",
        "SELECT bucket,complete,source_points,refreshed_at FROM noc_analytics.coverage WHERE $__timeFilter(bucket) ORDER BY bucket DESC LIMIT 2160",
    )
    if uid in {"noc-network", "noc-towers", "noc-outages"}:
        table(
            "Authoritative NOC incident lifetimes · impact is a recorded estimate",
            f"SELECT id,site_id,started_at,restored_at,severity,status,recorded_customer_impact_estimate FROM noc_analytics.incidents WHERE started_at<=$__timeTo() AND coalesce(restored_at,$__timeTo())>=$__timeFrom() {FILTER} ORDER BY started_at DESC LIMIT 1000",
        )
    if uid == "noc-towers":
        table(
            "Installed equipment · no financial/credential fields",
            f"SELECT asset_id,device_id,state FROM noc_analytics.equipment WHERE (CAST(${{site:sqlstring}} AS integer)=0 OR site_id=CAST(${{site:sqlstring}} AS integer)) LIMIT 1000",
        )
        table(
            "Maintenance record references · details remain in authorized NOC",
            f"SELECT id,site_id,asset_id,work_order_id,serviced_at FROM noc_analytics.maintenance WHERE $__timeFilter(serviced_at) AND (CAST(${{site:sqlstring}} AS integer)=0 OR site_id=CAST(${{site:sqlstring}} AS integer)) LIMIT 1000",
        )
    if uid == "noc-sectors":
        table(
            "Planning comparison · separate from observed time series",
            "SELECT id,created_at,predicted_dbm,observed_dbm,error_db,observation_kind FROM noc_analytics.calibration WHERE $__timeFilter(created_at) ORDER BY created_at DESC LIMIT 500",
        )
    if uid == "noc-capacity":
        table(
            "Hourly recorded capacity · sample-weighted, not configured speed",
            f"SELECT bucket,site_id,metric,value_sum/nullif(valid_count,0) AS recorded_mean,valid_count,missing_count FROM noc_analytics.hourly h WHERE EXISTS(SELECT 1 FROM noc_analytics.coverage c WHERE c.bucket=h.bucket AND c.complete) AND $__timeFilter(bucket) AND metric IN ('rf_capacity_mbps','rf_utilization_percent','sector_utilization_percent') {FILTER} ORDER BY bucket DESC LIMIT 5000",
        )
    return {
        "uid": uid,
        "title": "NWO NOC · " + title,
        "tags": ["nwo-noc", "recorded-only", "private"],
        "schemaVersion": 39,
        "version": 1,
        "editable": False,
        "timezone": "browser",
        "refresh": "1m",
        "time": {"from": "now-24h", "to": "now"},
        "annotations": {"list": []},
        "templating": {
            "list": [
                {
                    "name": "site",
                    "label": "NOC site identifier",
                    "type": "query",
                    "datasource": {"type": "grafana-postgresql-datasource", "uid": DS},
                    "query": "SELECT '0' UNION SELECT DISTINCT coalesce(site_id,0)::text FROM noc_analytics.metric_points ORDER BY 1 LIMIT 1000",
                    "multi": False,
                    "includeAll": False,
                    "current": {"text": "0", "value": "0"},
                    "refresh": 1,
                },
                {
                    "name": "device",
                    "label": "NOC device identifier",
                    "type": "query",
                    "datasource": {"type": "grafana-postgresql-datasource", "uid": DS},
                    "query": "SELECT '0' UNION SELECT DISTINCT device_id::text FROM noc_analytics.metric_points ORDER BY 1 LIMIT 1000",
                    "multi": False,
                    "includeAll": False,
                    "current": {"text": "0", "value": "0"},
                    "refresh": 1,
                },
            ]
        },
        "panels": panels,
        "description": "Private read-only NOC observations. Missing data is unavailable. Financial data and customer PII excluded. Grafana creates no NOC incidents. Default sampled means are not time-based availability SLAs.",
    }


def bundle(uids):
    return {
        "datasource": {
            "apiVersion": 1,
            "datasources": [
                {
                    "name": "NOC recorded analytics",
                    "uid": DS,
                    "type": "grafana-postgresql-datasource",
                    "access": "proxy",
                    "url": "$NOC_GRAFANA_PG_HOST",
                    "user": "noc_grafana",
                    "isDefault": True,
                    "editable": False,
                    "jsonData": {
                        "database": "nwo_noc",
                        "sslmode": "$NOC_GRAFANA_PG_SSLMODE",
                        "postgresVersion": 1600,
                        "timescaledb": False,
                        "maxOpenConns": 3,
                        "maxIdleConns": 1,
                        "connMaxLifetime": 300,
                        "timeInterval": "1m",
                    },
                    "secureJsonData": {"password": "$NOC_GRAFANA_DB_PASSWORD"},
                }
            ],
        },
        "provider": {
            "apiVersion": 1,
            "providers": [
                {
                    "name": "NOC operational analytics",
                    "orgId": 1,
                    "folder": "NOC operational",
                    "type": "file",
                    "disableDeletion": True,
                    "allowUiUpdates": False,
                    "updateIntervalSeconds": 60,
                    "options": {"path": "/etc/grafana/provisioning/noc-dashboards"},
                }
            ],
        },
        "dashboards": {uid: dashboard(uid) for uid in uids},
    }


def write_files(root, uids):
    p = Path(root)
    if not p.is_absolute() or any(x.is_symlink() for x in [p, *p.parents]):
        raise ValueError("Owned absolute provisioning directory required")
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(p.iterdir()):
        raise ValueError(
            "Empty provisioning destination required; do not overwrite operator files"
        )
    data = bundle(uids)
    for name in ["datasources", "dashboards", "noc-dashboards"]:
        (p / name).mkdir(mode=0o700)
    # JSON is valid YAML1.2; Grafana provisioning accepts YAML files containing JSON.
    for rel, obj in [
        ("datasources/noc.yaml", data["datasource"]),
        ("dashboards/noc.yaml", data["provider"]),
        *[
            (f"noc-dashboards/{uid}.json", value)
            for uid, value in data["dashboards"].items()
        ],
    ]:
        f = p / rel
        f.write_text(json.dumps(obj, indent=2) + "\n")
        f.chmod(0o600)
    return {"files": 12, "dashboard_count": len(uids), "secrets_written": False}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(write_files(args.output, list(DASHBOARDS))))
