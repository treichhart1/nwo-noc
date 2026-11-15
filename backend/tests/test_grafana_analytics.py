"""Disposable PostgreSQL and synthetic Grafana; no real network/device integrations."""

import json, os
from datetime import timedelta, timezone, datetime
from uuid import uuid4
from pathlib import Path
from types import SimpleNamespace
import pytest, httpx
from pydantic import ValidationError
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from app.core.database import get_db
from app.core.config import settings
from app.core.security import create_access_token
from app.main import app
from app.models.entities import (
    User,
    Site,
    Device,
    TelemetrySample,
    RFAnalyticsPoint,
    Incident,
    MonitorCheck,
    AuditLog,
)
from app.tower_health.models import TowerEvent, TowerSample
from app.analytics import service as s, grafana, provision, reader_role, worker
from app.analytics.models import *
from app.analytics.contracts import *
from app.analytics.catalog import DASHBOARDS
from test_rf_postgres_integration import postgres_db

R = "Synthetic recorded monitoring analytics; no polling or external service writes"


def b(cls, **kw):
    return cls(operation_key=uuid4(), reason=R, **kw)


@pytest.fixture
def x(postgres_db, monkeypatch, tmp_path):
    db = postgres_db
    u = User(
        username="analytics-admin",
        email="analytics@example.invalid",
        role="admin",
        password_hash="unusable",
        is_active=True,
    )
    site = Site(name="Synthetic POP private-name")
    db.add_all([u, site])
    db.flush()
    d = Device(
        source="synthetic",
        external_id="analytics-synthetic-device",
        name="PRIVATE-NAME-SHOULD-NOT-EXPORT",
        ip_address="192.0.2.88",
        vendor="Synthetic",
        wisp_site_id=site.id,
    )
    db.add(d)
    db.flush()
    for key, value in {
        "app_env": "staging",
        "offline_mode": True,
        "synthetic_monitoring_enabled": True,
        "grafana_allowed_origins": s.SYNTHETIC_ORIGIN,
        "grafana_embed_approved": False,
        "grafana_live_health_enabled": False,
        "analytics_rollup_enabled": False,
    }.items():
        monkeypatch.setattr(settings, key, value)
    for key in ["grafana_service_token_file", "analytics_metrics_token_file"]:
        file = tmp_path / key
        file.write_text("a" * 64)
        file.chmod(0o600)
        monkeypatch.setattr(settings, key, str(file))
    s.configure(
        db,
        u,
        b(
            ConfigInput,
            expected_revision=0,
            enabled=True,
            server_url=s.SYNTHETIC_ORIGIN,
            export_device_ids=[d.id],
            export_metrics=[
                "cpu_percent",
                "rssi_dbm",
                "reachability",
                "interface_rx_bytes",
            ],
        ),
    )
    return SimpleNamespace(db=db, u=u, site=site, d=d, path=tmp_path)


def seed(x, at=None, cpu=42):
    at = at or s.now() - timedelta(minutes=2)
    t = TelemetrySample(
        device_id=x.d.id,
        sampled_at=at,
        cpu_percent=cpu,
        ram_percent=51,
        raw_data={
            "mikrotik_v1": {
                "identity": "PRIVATE-IDENTITY",
                "uptime_seconds": 1000,
                "interfaces": [
                    {
                        "id": "*1",
                        "name": "PRIVATE-CUSTOMER-NAME",
                        "running": True,
                        "rx_bytes": 125000,
                        "tx_bytes": 50000,
                        "rx_errors": 2,
                        "tx_errors": 0,
                    }
                ],
                "ospf_neighbors": [{"router_id": "192.0.2.9", "state": "Full"}],
            },
            "private_token": "SYNTHETIC-NEVER-EXPORT",
        },
    )
    x.db.add(t)
    x.db.flush()
    p = RFAnalyticsPoint(
        source_sample_id=t.id,
        device_id=x.d.id,
        site_id=x.site.id,
        link_id=None,
        series_key="radio:SYNTHETIC-PRIVATE",
        vendor="ubiquiti",
        source_profile="synthetic",
        mapping_digest="a" * 64,
        sampled_at=at,
        valid=True,
        signal_dbm=-65,
        snr_db=25,
        capacity_mbps=200,
        utilization_percent=90,
        radio_state="connected",
    )
    x.db.add(p)
    x.db.flush()
    return t, p


def query(x, **kw):
    return RangeInput(site_id=x.site.id, **kw)


def api(x, role="admin"):
    u = x.u
    if role != "admin":
        u = User(
            username="analytics-" + role,
            email=role + "@example.invalid",
            role=role,
            password_hash="unusable",
            is_active=True,
        )
        x.db.add(u)
        x.db.flush()
    app.dependency_overrides[get_db] = lambda: x.db
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + create_access_token(
        u.username, user_id=u.id
    )
    return c


@pytest.mark.parametrize(
    "url",
    [
        "http://grafana.synthetic.invalid",
        "https://user:password@grafana.synthetic.invalid",
        "https://grafana.synthetic.invalid/?token=bad",
        "https://grafana.synthetic.invalid/#x",
        "https://grafana.synthetic.invalid/%2e%2e",
        "https://grafana.synthetic.invalid/../admin",
        "https://unapproved.invalid",
        "https://grafana.synthetic.invalid\\other",
        "https://grafana.synthetic.invalid\n",
    ],
)
def test_unsafe_grafana_url_rejected(x, url):
    with pytest.raises(ValueError):
        s.configure(
            x.db, x.u, b(ConfigInput, expected_revision=1, enabled=True, server_url=url)
        )


def test_configuration_revision_and_defaults(x):
    assert s.config(x.db)["revision"] == 1
    with pytest.raises(ValueError):
        s.configure(x.db, x.u, b(ConfigInput, expected_revision=0))
    with pytest.raises(ValidationError):
        b(ConfigInput, expected_revision=1, dashboards=["admin"])


def test_safe_links_do_not_forward_tokens_or_untrusted_paths(x):
    r = s.dashboard_link(
        x.db, x.u, b(LinkInput, dashboard_uid="noc-towers", query=query(x))
    )
    assert r["url"].startswith(s.SYNTHETIC_ORIGIN + "/d/noc-towers/noc?")
    assert "token" not in r["url"]
    assert "var-site=" + str(x.site.id) in r["url"]
    with pytest.raises(ValueError):
        s.dashboard_link(
            x.db, x.u, b(LinkInput, dashboard_uid="../../admin", query=query(x))
        )


def test_embedding_requires_explicit_proxy_and_global_approval(x):
    with pytest.raises(ValueError):
        s.dashboard_link(
            x.db,
            x.u,
            b(LinkInput, dashboard_uid="noc-network", query=query(x), embed=True),
        )
    with pytest.raises(ValidationError):
        b(ConfigInput, expected_revision=1, embed_enabled=True, auth_mode="session")


def test_provisioning_templates_have_no_admin_or_secret_surface(x):
    data = provision.bundle(list(DASHBOARDS))
    assert len(data["dashboards"]) == 10
    raw = json.dumps(data)
    assert "${site:raw}" not in raw
    assert "${site:sqlstring}" in raw
    assert "SYNTHETIC-NEVER-EXPORT" not in raw
    for uid, d in data["dashboards"].items():
        assert not d["editable"] and d["uid"] == uid
        assert d["refresh"] == "1m"
    assert data["datasource"]["datasources"][0]["user"] == "noc_grafana"


def test_provisioning_does_not_overwrite_existing_files(x):
    target = x.path / "provision"
    assert provision.write_files(target, list(DASHBOARDS))["dashboard_count"] == 10
    with pytest.raises(ValueError):
        provision.write_files(target, list(DASHBOARDS))
    assert "password" not in json.loads(
        (target / "noc-dashboards/noc-network.json").read_text()
    )


def test_numeric_views_omit_private_fields_and_validate_units(x):
    seed(x)
    r = s.series(x.db, query(x, metric="cpu_percent"))
    assert r["points"][0]["value"] == 42
    rf = s.series(x.db, query(x, metric="rssi_dbm"))
    assert rf["points"][0]["value"] == -65
    raw = json.dumps(r) + s.prometheus(x.db)
    for private in [
        "PRIVATE-NAME",
        "PRIVATE-CUSTOMER",
        "192.0.2.",
        "SYNTHETIC-NEVER-EXPORT",
        "PRIVATE-IDENTITY",
    ]:
        assert private not in raw


def test_missing_and_invalid_metrics_are_not_zero_or_healthy(x):
    seed(x, cpu=1000)
    r = s.series(x.db, query(x, metric="cpu_percent"))
    assert r["points"][0]["value"] is None
    assert r["points"][0]["state"] == "missing_or_invalid"
    absent = s.series(x.db, query(x, metric="battery_voltage"))
    assert absent["points"] == []
    assert "noc_cpu_percent{" not in s.prometheus(x.db)


def test_stale_metrics_not_exported_as_current(x):
    seed(x, at=s.now() - timedelta(hours=2))
    assert "noc_cpu_percent{" not in s.prometheus(x.db)
    assert s.health(x.db)["metrics_state"] == "stale"


def test_routing_and_interface_measurements_use_supported_raw_fields(x):
    seed(x)
    assert (
        s.series(x.db, query(x, metric="ospf_full_neighbors"))["points"][0]["value"]
        == 1
    )
    assert (
        s.series(x.db, query(x, metric="interface_rx_errors"))["points"][0]["value"]
        == 2
    )
    assert "PRIVATE-CUSTOMER" not in s.prometheus(x.db)


def test_tower_power_and_missing_collector_state(x):
    at = s.now() - timedelta(minutes=1)
    e = TowerEvent(event_id="analytics-power", body_hash="a" * 64, observed_at=at)
    x.db.add(e)
    x.db.flush()
    row = TowerSample(
        event_id=e.event_id,
        device_id=x.d.id,
        site_id=x.site.id,
        scope_key="synthetic",
        source="synthetic",
        observed_at=at,
        data={
            "metrics": {"collector_ok": True},
            "power": {"ac_present": False, "battery_voltage": 48.2},
            "unavailable": {},
        },
    )
    x.db.add(row)
    x.db.flush()
    assert s.series(x.db, query(x, metric="ac_present"))["points"][0]["value"] == 0
    assert (
        s.series(x.db, query(x, metric="battery_voltage"))["points"][0]["value"] == 48.2
    )
    row.data = {
        "metrics": {"collector_ok": False},
        "power": {"ac_present": False},
        "unavailable": {},
    }
    x.db.flush()
    assert s.series(x.db, query(x, metric="ac_present"))["points"][0]["value"] is None


def test_site_filters_and_conflicting_device_context(x):
    seed(x)
    site = Site(name="Other synthetic site")
    x.db.add(site)
    x.db.flush()
    assert s.series(x.db, RangeInput(site_id=site.id))["points"] == []
    with pytest.raises(ValueError):
        s.series(x.db, RangeInput(site_id=site.id, device_id=x.d.id))


def test_authoritative_outages_duration_and_customer_estimate(x):
    at = s.now() - timedelta(hours=1)
    for i in range(3):
        x.db.add(
            Incident(
                title="PRIVATE-CUSTOMER-NAME",
                site_id=x.site.id,
                severity="critical",
                status="resolved",
                started_at=at + timedelta(minutes=i * 10),
                resolved_at=at + timedelta(minutes=i * 10 + 5),
                customer_impact=4,
            )
        )
    x.db.flush()
    r = s.outages(x.db, query(x))
    assert r["count"] == 3
    assert r["items"][0]["duration_in_range_seconds"] == 300
    assert len(r["recurring_sites"]) == 1
    assert "PRIVATE-CUSTOMER" not in json.dumps(r)
    assert x.db.scalar(select(func.count()).select_from(Incident)) == 3


def test_advisory_rf_and_capacity_evidence(x):
    for m in [3, 2, 1]:
        seed(x, at=s.now() - timedelta(minutes=m))
    r = s.report(
        x.db, query(x, metric="rf_utilization_percent", preset="1h", max_points=60)
    )
    assert any("congestion" in v["action"] for v in r["recommendations"])
    assert not r["remediation_enabled"]


def test_custom_range_validation_and_retention(x):
    with pytest.raises(ValidationError):
        RangeInput(preset="custom", start=s.now() - timedelta(days=100), end=s.now())
    with pytest.raises(ValidationError):
        RangeInput(
            preset="custom", start=datetime(2020, 1, 1), end=datetime(2020, 1, 2)
        )
    with pytest.raises(ValueError):
        s.series(
            x.db,
            RangeInput(
                preset="custom",
                start=s.now() - timedelta(days=120),
                end=s.now() - timedelta(days=100),
            ),
        )


def test_hourly_rollups_are_idempotent_and_weighted(x):
    end = s.now().replace(minute=0, second=0, microsecond=0)
    at = end - timedelta(hours=1)
    seed(x, at=at + timedelta(minutes=5), cpu=20)
    seed(x, at=at + timedelta(minutes=10), cpu=60)
    args = b(RollupInput, start=at, end=end)
    s.rollup(x.db, x.u, args)
    n = x.db.scalar(select(func.count()).select_from(HourlyBin))
    s.rollup(x.db, x.u, args)
    assert x.db.scalar(select(func.count()).select_from(HourlyBin)) == n
    row = x.db.scalar(select(HourlyBin).where(HourlyBin.metric == "cpu_percent"))
    assert row.value_sum / row.valid_count == 40


def test_rollup_does_not_delete_retained_values_after_raw_pruning(x):
    end = s.now().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=1)
    t, p = seed(x, at=start + timedelta(minutes=5))
    args = b(RollupInput, start=start, end=end)
    s.rollup(x.db, x.u, args)
    x.db.execute(text("DELETE FROM telemetry_samples WHERE id=:id"), {"id": t.id})
    x.db.flush()
    s.rollup(x.db, x.u, args)
    assert (
        x.db.scalar(
            select(func.count())
            .select_from(HourlyBin)
            .where(HourlyBin.metric == "cpu_percent")
        )
        == 1
    )


def test_mock_health_and_missing_live_verification(x):
    r = grafana.check(x.db, x.u, b(HealthInput, mode="mock"))
    assert r["status"] == "ok"
    assert len(r["result"]["approved_dashboards"]) == 10
    assert not s.health(x.db)["grafana_live_verified"]
    with pytest.raises(ValueError):
        grafana.check(x.db, x.u, b(HealthInput, mode="live"))


@pytest.mark.parametrize("mode", ["timeout", "malformed", "redirect", "unhealthy"])
def test_grafana_failures_are_sanitized_and_bounded(x, mode):
    def handle(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("PRIVATE-TOKEN", request=request)
        if mode == "malformed":
            return httpx.Response(200, text="PRIVATE-TOKEN")
        if mode == "redirect":
            return httpx.Response(
                302, headers={"Location": "https://unapproved.invalid"}
            )
        return httpx.Response(
            200,
            json={"version": "12.3.3", "database": "error", "token": "PRIVATE-TOKEN"},
        )

    r = grafana.check(
        x.db, x.u, b(HealthInput, mode="mock"), httpx.MockTransport(handle)
    )
    assert r["status"] == "error"
    assert "PRIVATE-TOKEN" not in json.dumps(r)


def test_private_service_credentials_and_permissions(x):
    p = x.path / "key"
    p.write_text("b" * 64)
    p.chmod(0o600)
    assert s.private_secret(str(p)) == "b" * 64
    p.chmod(0o644)
    with pytest.raises(ValueError):
        s.private_secret(str(p))
    link = x.path / "symlink"
    link.symlink_to(p)
    with pytest.raises(ValueError):
        s.private_secret(str(link))


def test_reader_role_cannot_read_raw_customer_or_financial_tables(x):
    reader_role.install(x.db, "b" * 64)
    x.db.execute(text("SET LOCAL ROLE noc_grafana"))
    assert x.db.scalar(text("SELECT count(*) FROM noc_analytics.metric_points")) == 0
    for name in [
        "customer_accounts",
        "finance_entries",
        "telemetry_samples",
        "analytics_config",
    ]:
        with pytest.raises(DBAPIError):
            with x.db.begin_nested():
                x.db.execute(text("SELECT * FROM public." + name))
    x.db.execute(text("RESET ROLE"))


def test_metrics_auth_scope_and_rate_limit(x):
    seed(x)
    with pytest.raises(PermissionError):
        s.scrape_auth(x.db, "wrong")
    s.scrape_auth(x.db, "a" * 64)
    for _ in range(59):
        s.scrape_auth(x.db, "a" * 64)
    with pytest.raises(ValueError):
        s.scrape_auth(x.db, "a" * 64)


def test_worker_disabled_does_not_add_polling(x):
    assert worker.tick(x.db) == {"disabled": True, "device_polls": 0}


@pytest.mark.parametrize("role", ["viewer", "technician"])
def test_unauthorized_analytics_and_export_denied(x, role):
    c = api(x, role)
    try:
        assert c.get("/api/analytics/v1/configuration").status_code == 403
        assert (
            c.post(
                "/api/analytics/v1/export", json=RangeInput().model_dump(mode="json")
            ).status_code
            == 403
        )
    finally:
        app.dependency_overrides.clear()


def test_operator_read_only_and_admin_mutation_boundary(x):
    c = api(x, "operator")
    try:
        assert c.get("/api/analytics/v1/health").status_code == 200
        assert (
            c.post(
                "/api/analytics/v1/configuration",
                json=b(ConfigInput, expected_revision=1).model_dump(mode="json"),
            ).status_code
            == 403
        )
    finally:
        app.dependency_overrides.clear()


def test_report_export_and_private_metrics_api(x):
    seed(x)
    c = api(x)
    try:
        r = c.post(
            "/api/analytics/v1/report",
            json=query(x, metric="cpu_percent").model_dump(mode="json"),
        )
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        assert (
            c.post(
                "/api/analytics/v1/export", json=query(x).model_dump(mode="json")
            ).status_code
            == 200
        )
        assert (
            c.get(
                "/api/analytics-scrape/v1/metrics",
                headers={"Authorization": "Bearer " + "a" * 64},
            ).status_code
            == 200
        )
    finally:
        app.dependency_overrides.clear()


def test_late_recorded_samples_invalidate_prepared_hour(x):
    at = s.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
    seed(x, at=at + timedelta(minutes=1))
    s.rollup(x.db, x.u, b(RollupInput, start=at, end=at + timedelta(hours=1)))
    x.db.flush()
    assert x.db.get(HourCoverage, at).complete
    seed(x, at=at + timedelta(minutes=2))
    x.db.flush()
    x.db.expire_all()
    assert not x.db.get(HourCoverage, at).complete
    s.rollup(x.db, x.u, b(RollupInput, start=at, end=at + timedelta(hours=1)))
    x.db.flush()
    x.db.expire_all()
    assert x.db.get(HourCoverage, at).complete


def test_long_range_does_not_silently_use_partial_rollup(x):
    at = s.now().replace(minute=0, second=0, microsecond=0) - timedelta(days=2)
    seed(x, at=at + timedelta(minutes=1))
    s.rollup(x.db, x.u, b(RollupInput, start=at, end=at + timedelta(hours=1)))
    q = RangeInput(preset="7d", device_id=x.d.id, metric="cpu_percent")
    result = s.series(x.db, q)
    assert result["storage"] == "native_recorded_views"
    assert (
        result["closed_hour_coverage"]["covered"]
        < result["closed_hour_coverage"]["expected"]
    )


def test_provisioning_uses_sql_quoted_ids_and_weighted_rollups():
    sql = provision.metric_sql("rssi_dbm")
    assert ":raw" not in sql
    assert "${site:sqlstring}" in sql
    assert "sum(total)/nullif(sum(n),0)" in sql
    assert "noc_analytics.hourly" in sql
    assert "c.bucket=h.bucket AND c.complete" in sql


def test_context_without_filters_and_device_only_are_valid(x):
    app.dependency_overrides[get_db] = lambda: x.db
    try:
        with TestClient(app) as client:
            token = create_access_token(x.u.username, user_id=x.u.id)
            headers = {"Authorization": "Bearer " + token}
            for path in [
                "/api/analytics/v1/context",
                "/api/analytics/v1/context?device_id=" + str(x.d.id),
            ]:
                r = client.get(path, headers=headers)
                assert r.status_code == 200, r.text
                assert r.json()["financial_values_in_grafana"] is False
    finally:
        app.dependency_overrides.clear()
