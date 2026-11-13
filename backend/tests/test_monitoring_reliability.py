"""Synthetic NOC-92 checks; never uses network-device transports."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func
from pydantic import ValidationError
from test_rf_postgres_integration import postgres_db
from app.core.config import settings
from app.models.entities import (
    Device,
    MonitoringJob,
    MonitoringAlert,
    MonitoringReceipt,
    MonitoringAttempt,
    TelemetrySample,
    AuditLog,
)
from app.services.monitoring_policy import ReliabilityPolicy, JobSpec, backoff
from app.services.monitoring_scheduler import enroll, claim, execute
from app.services.monitoring_alerts import rules
from app.services.monitoring_health import correlate, health
from app.services import monitoring_spool as spool

NOW = datetime.now(timezone.utc)


@pytest.fixture
def isolated(monkeypatch):
    for k, v in dict(
        offline_mode=True,
        app_env="staging",
        synthetic_monitoring_enabled=True,
        rf_scheduler_enabled=True,
    ).items():
        monkeypatch.setattr(settings, k, v)


def setup(db, mode="normal", vendor="Cambium"):
    d = Device(
        source="synthetic",
        external_id="noc92-" + mode,
        name="Synthetic reliability",
        vendor=vendor,
        enabled=True,
    )
    db.add(d)
    db.flush()
    j = enroll(
        db,
        {
            "device_id": d.id,
            "interval_seconds": 15,
            "timeout_seconds": 1,
            "lease_seconds": 15,
            "fixture": {"mode": mode},
        },
        now=NOW,
    )
    return d, j


def poll(db, j, root, at):
    pair = claim(db, "test-worker", now=at, limit=1)[0]
    result = execute(db, *pair, now=at, spool_dir=root)
    db.flush()
    if result.get("spool_slot"):
        spool.acknowledge(root, result["spool_key"], result["spool_slot"])
    return result


@pytest.mark.parametrize(
    "bad",
    [
        {"signal_clear": -80},
        {"snr_clear": 10},
        {"capacity_clear": 50},
        {"temperature_clear": 90},
        {"utilization_clear": 99},
        {"backoff_cap": 1},
        {"snr_enter": float("nan")},
        {"unknown": 1},
    ],
)
def test_invalid_policy(bad):
    with pytest.raises(ValidationError):
        ReliabilityPolicy(**bad)


def test_backoff_and_job_bounds():
    assert [backoff(n, ReliabilityPolicy()) for n in (1, 2, 3, 99)] == [5, 10, 20, 300]
    for bad in (
        {"interval_seconds": 0},
        {"lease_seconds": 15, "timeout_seconds": 20},
        {"kind": "mikrotik", "timeout_seconds": 10},
        {"fixture": {"mode": "reboot"}},
    ):
        with pytest.raises(ValidationError):
            JobSpec(device_id=1, **bad)


def point(n, value=-80, **kw):
    return {
        "at": NOW + timedelta(seconds=15 * n),
        "metrics": {"signal": value, "uptime": n + 100},
        "context": ("same",),
        "radio_status": "up",
        "ethernet_status": None,
        **kw,
    }


def test_consecutive_hysteresis_missing_and_stale():
    p = ReliabilityPolicy()
    pts = [point(n) for n in range(3)]
    assert rules(pts, NOW + timedelta(seconds=30), p)["low_signal"]["state"] == "active"
    assert (
        rules(pts + [point(3, -74)], NOW + timedelta(seconds=45), p)["low_signal"][
            "state"
        ]
        == "active"
    )
    assert (
        rules(pts + [point(3, None)], NOW + timedelta(seconds=45), p)["low_signal"][
            "state"
        ]
        == "unknown"
    )
    assert rules(pts, NOW + timedelta(hours=1), p)["low_signal"]["state"] == "unknown"
    assert (
        rules(
            pts + [point(n, -65) for n in range(3, 6)], NOW + timedelta(seconds=75), p
        )["low_signal"]["state"]
        == "clear"
    )


def test_restart_and_context_reset():
    pts = [point(n) for n in range(2)] + [
        point(2, metrics={"signal": -80, "uptime": 1})
    ]
    assert (
        rules(pts, NOW + timedelta(seconds=30), ReliabilityPolicy())["low_signal"][
            "state"
        ]
        == "unknown"
    )
    pts[-1] = point(2, context=("new frequency",))
    assert (
        rules(pts, NOW + timedelta(seconds=30), ReliabilityPolicy())["low_signal"][
            "state"
        ]
        == "unknown"
    )


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("normal", "success"),
        ("timeout", "timeout"),
        ("disconnected", "unavailable"),
        ("invalid", "invalid_response"),
        ("stale", "stale_telemetry"),
        ("missing", "no_telemetry"),
    ],
)
def test_poll_results(postgres_db, isolated, tmp_path, mode, expected):
    d, j = setup(postgres_db, mode)
    assert poll(postgres_db, j, tmp_path, NOW)["result"] == expected
    assert not list(postgres_db.scalars(select(MonitoringAlert)))


@pytest.mark.parametrize(
    "vendor", ["Ubiquiti", "Cambium", "Racom", "Aviat", "MikroTik"]
)
def test_all_vendor_collectors(postgres_db, isolated, tmp_path, vendor):
    d, j = setup(postgres_db, vendor=vendor)
    assert poll(postgres_db, j, tmp_path, NOW)["result"] == "success"
    assert postgres_db.scalar(select(func.count(TelemetrySample.id))) == 1


def test_duplicate_jobs_and_fencing(postgres_db, isolated, tmp_path):
    db = postgres_db
    d, j = setup(db)
    assert enroll(db, {"device_id": d.id}, now=NOW).key == j.key
    assert db.scalar(select(func.count(MonitoringJob.key))) == 1
    pair = claim(db, "a", now=NOW)[0]
    assert claim(db, "b", now=NOW) == []
    assert (
        execute(db, j.key, "wrong-token", now=NOW, spool_dir=tmp_path)["result"]
        == "fenced"
    )
    assert execute(db, *pair, now=NOW, spool_dir=tmp_path)["result"] == "success"
    assert execute(db, *pair, now=NOW, spool_dir=tmp_path)["result"] == "fenced"


def test_crash_after_handoff_replays_once(postgres_db, isolated, tmp_path):
    db = postgres_db
    d, j = setup(db)
    pair = claim(db, "dead", now=NOW)[0]
    db.flush()
    with pytest.raises(SystemExit):
        execute(db, *pair, now=NOW, spool_dir=tmp_path, crash_after_spool=True)
    assert (
        spool.count(tmp_path) == 1
        and db.scalar(select(func.count(TelemetrySample.id))) == 0
    )
    recovered = claim(db, "replacement", now=NOW + timedelta(seconds=16))[0]

    def forbidden(*a):
        raise AssertionError("replay must not recollect")

    result = execute(
        db,
        *recovered,
        now=NOW + timedelta(seconds=16),
        collector=forbidden,
        spool_dir=tmp_path,
    )
    assert result["result"] == "success" and result["replayed"]
    assert db.scalar(select(func.count(MonitoringReceipt.id))) == 1
    assert db.get(MonitoringAttempt, pair[1]).result == "abandoned"
    assert spool.reconcile(tmp_path, db) == 1 and spool.count(tmp_path) == 0
    assert db.scalar(select(func.count(TelemetrySample.id))) == 1


def test_storm_dedup_no_false_recovery(postgres_db, isolated, tmp_path):
    db = postgres_db
    d, j = setup(db, "weak")
    for n in range(8):
        assert (
            poll(db, j, tmp_path, NOW + timedelta(seconds=15 * n))["result"]
            == "success"
        )
    alert = db.scalar(
        select(MonitoringAlert).where(MonitoringAlert.rule == "low_signal")
    )
    assert alert.state == "active" and alert.episode == 1
    assert (
        db.scalar(
            select(func.count(MonitoringAlert.id)).where(
                MonitoringAlert.rule == "low_signal"
            )
        )
        == 1
    )
    j.fixture = {"mode": "missing"}
    poll(db, j, tmp_path, NOW + timedelta(seconds=120))
    assert alert.state == "unknown" and alert.cleared_at is None
    j.fixture = {"mode": "normal"}
    for n in range(9, 11):
        poll(db, j, tmp_path, NOW + timedelta(seconds=15 * n))
        assert alert.state == "unknown"
    poll(db, j, tmp_path, NOW + timedelta(seconds=165))
    assert alert.state == "cleared"


def test_collector_stable_recovery(postgres_db, isolated, tmp_path):
    db = postgres_db
    d, j = setup(db, "timeout")
    for n in range(3):
        poll(db, j, tmp_path, NOW + timedelta(seconds=30 * n))
    alert = db.scalar(
        select(MonitoringAlert).where(MonitoringAlert.rule == "collection_failure")
    )
    assert alert.state == "active" and alert.category == "monitoring"
    j.fixture = {"mode": "normal"}
    for n in range(2):
        poll(db, j, tmp_path, NOW + timedelta(seconds=90 + 15 * n))
        assert alert.state == "unknown"
    poll(db, j, tmp_path, NOW + timedelta(seconds=120))
    assert alert.state == "cleared"
    assert (
        db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "monitoring.collector_recovered"
            )
        )
        == 1
    )
    assert health(db, now=NOW + timedelta(seconds=120))["failures_24h"] == 3


def test_private_spool(postgres_db, isolated, tmp_path):
    db = postgres_db
    d, j = setup(db)
    pair = claim(db, "w", now=NOW)[0]
    with pytest.raises(SystemExit):
        execute(db, *pair, now=NOW, spool_dir=tmp_path, crash_after_spool=True)
    next(tmp_path.glob("*.json")).chmod(0o644)
    with pytest.raises(ValueError):
        spool.pending(tmp_path, j.key, NOW)


def test_synthetic_denied_outside_isolation(postgres_db, monkeypatch):
    monkeypatch.setattr(settings, "offline_mode", False)
    with pytest.raises(ValueError):
        setup(postgres_db)


def test_redundancy_and_monitoring_not_rf_root():
    def alert(i, dev):
        return SimpleNamespace(
            id=i,
            device_id=dev,
            series="radio",
            rule="wireless_outage",
            state="active",
            category="rf",
            severity="critical",
            episode=1,
            first_seen_at=NOW,
            updated_at=NOW,
            evidence={"observed_at": NOW.isoformat()},
            incident_id=None,
        )

    devices = [
        SimpleNamespace(id=1, parent_device_id=None),
        SimpleNamespace(id=2, parent_device_id=1),
    ]
    alerts = [alert(1, 1), alert(2, 2)]
    assert correlate(alerts, devices, [], NOW)[1]["probable_parent_alert_id"] == 1
    assert (
        correlate(
            alerts, devices, [SimpleNamespace(id=9, a_device_id=3, z_device_id=2)], NOW
        )[1]["correlation"]
        == "multiple_paths_uncertain"
    )
    alerts[0].evidence = {"observed_at": (NOW - timedelta(hours=1)).isoformat()}
    assert correlate(alerts, devices, [], NOW)[1]["probable_parent_alert_id"] is None
    assert correlate(alerts, devices, [], NOW)[0]["state"] == "unknown"
    alerts[0].evidence = {"observed_at": NOW.isoformat()}
    alerts[1].evidence = {"observed_at": (NOW + timedelta(seconds=1)).isoformat()}
    assert (
        correlate(alerts, devices, [], NOW)[1]["correlation"]
        == "insufficient_fresh_evidence"
    )
    alerts[1].evidence = {"observed_at": NOW.isoformat()}
    alerts[0].category = "monitoring"
    assert correlate(alerts, devices, [], NOW)[1]["probable_parent_alert_id"] is None


@pytest.mark.parametrize(
    "role,expected", [("viewer", 403), ("operator", 200), ("admin", 200)]
)
def test_health_authorization(postgres_db, role, expected):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.database import get_db
    from app.api.deps import current_user

    app.dependency_overrides[get_db] = lambda: postgres_db
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=None, role=role)
    try:
        with TestClient(app) as c:
            r = c.get("/api/monitoring/reliability")
            assert r.status_code == expected
            if expected == 200:
                assert r.json()["execution_enabled"] is False
            assert c.post(
                "/api/monitoring/reliability/synthetic-jobs",
                json={"device_id": 1, "kind": "radio"},
            ).status_code == (409 if role == "admin" else 403)
    finally:
        app.dependency_overrides.clear()


def test_database_and_redis_recovery_heartbeat(
    postgres_db, isolated, monkeypatch, tmp_path
):
    from contextlib import contextmanager
    from sqlalchemy.exc import OperationalError
    from app.services.worker_runtime import Runtime

    monkeypatch.setattr(settings, "rf_scheduler_spool_dir", str(tmp_path))
    state = {"database": False, "redis": False}
    writes = []

    @contextmanager
    def factory():
        if not state["database"]:
            raise OperationalError(
                "SELECT 1", {}, Exception("synthetic db interruption")
            )
        yield postgres_db

    class Redis:
        def set(self, *a, **k):
            if not state["redis"]:
                raise ConnectionError("synthetic redis interruption")
            writes.append(a[0])

    runtime = Runtime(
        factory=factory, redis_factory=Redis, worker_id="test-recovery", write_id=False
    )
    runtime.heartbeat(force=True)
    assert runtime.db_failed and not writes
    state["database"] = True
    runtime.heartbeat(force=True)
    assert not runtime.db_failed and not writes
    state["redis"] = True
    runtime.heartbeat(force=True)
    assert len(writes) == 2
    for action in ("monitoring.database_recovered", "monitoring.redis_recovered"):
        assert (
            postgres_db.scalar(
                select(func.count(AuditLog.id)).where(AuditLog.action == action)
            )
            == 1
        )


def test_poll_database_interruption_retains_private_handoff(
    postgres_db, isolated, tmp_path, monkeypatch
):
    from sqlalchemy.exc import OperationalError
    import app.services.monitoring_scheduler as scheduler

    db = postgres_db
    d, j = setup(db)
    pair = claim(db, "w", now=NOW)[0]
    original = scheduler.stage

    def unavailable(*args):
        raise OperationalError(
            "INSERT telemetry", {}, Exception("synthetic database interruption")
        )

    monkeypatch.setattr(scheduler, "stage", unavailable)
    with pytest.raises(OperationalError):
        execute(db, *pair, now=NOW, spool_dir=tmp_path)
    assert (
        spool.count(tmp_path) == 1
        and db.scalar(select(func.count(TelemetrySample.id))) == 0
    )
    monkeypatch.setattr(scheduler, "stage", original)
    result = execute(db, *pair, now=NOW, spool_dir=tmp_path)
    assert result["replayed"] and result["result"] == "success"
    assert db.scalar(select(func.count(MonitoringReceipt.id))) == 1


@pytest.mark.parametrize("vendor", ["Racom", "Aviat"])
def test_explicit_multicarrier_synthetic_poll(postgres_db, isolated, tmp_path, vendor):
    from app.models.entities import Site, NetworkLink
    from app.services.monitoring_collectors import collect

    db = postgres_db
    d, j = setup(db, "carrier_failure", vendor)
    s = Site(name="Synthetic carrier site")
    db.add(s)
    db.flush()
    db.add(
        NetworkLink(
            name="Synthetic carrier link",
            a_site_id=s.id,
            z_site_id=s.id,
            a_device_id=d.id,
            z_device_id=d.id,
        )
    )
    db.flush()
    assert collect(db, j, NOW).format == "microwave"
    for n in range(3):
        assert (
            poll(db, j, tmp_path, NOW + timedelta(seconds=15 * n))["result"]
            == "success"
        )
    assert (
        db.scalar(
            select(MonitoringAlert).where(MonitoringAlert.rule == "carrier_failure")
        ).state
        == "active"
    )
    assert (
        db.scalar(
            select(func.count(MonitoringAlert.id)).where(
                MonitoringAlert.rule == "wireless_outage"
            )
        )
        == 0
    )


@pytest.mark.parametrize(
    "metric,bad,good,rule",
    [
        ("snr", 10, 30, "low_snr"),
        ("capacity", 20, 500, "low_capacity"),
        ("utilization", 95, 30, "high_utilization"),
        ("temperature", 95, 40, "overheating"),
    ],
)
def test_metric_alerts_and_hysteretic_recovery(metric, bad, good, rule):
    pts = [point(n, metrics={metric: bad}) for n in range(3)]
    p = ReliabilityPolicy()
    assert rules(pts, NOW + timedelta(seconds=30), p)[rule]["state"] == "active"
    pts.extend(point(n, metrics={metric: good}) for n in range(3, 6))
    assert rules(pts, NOW + timedelta(seconds=75), p)[rule]["state"] == "clear"


@pytest.mark.parametrize(
    "field,rule",
    [("radio_status", "wireless_outage"), ("ethernet_status", "ethernet_failure")],
)
def test_explicit_outage_only_and_stable_recovery(field, rule):
    pts = [point(n, **{field: "down"}) for n in range(3)]
    p = ReliabilityPolicy()
    assert rules(pts, NOW + timedelta(seconds=30), p)[rule]["state"] == "active"
    pts.append(point(3, **{field: None}))
    assert rules(pts, NOW + timedelta(seconds=45), p)[rule]["state"] == "unknown"
    pts.extend(point(n, **{field: "up"}) for n in range(4, 7))
    assert rules(pts, NOW + timedelta(seconds=90), p)[rule]["state"] == "clear"


@pytest.mark.parametrize(
    "metric,baseline,degraded,rule",
    [("noise", -95, -85, "rising_noise"), ("modulation", 8, 2, "modulation_fallback")],
)
def test_observed_disjoint_reference(metric, baseline, degraded, rule):
    pts = [point(n, metrics={metric: baseline}) for n in range(4)] + [
        point(n, metrics={metric: degraded}) for n in range(4, 7)
    ]
    assert (
        rules(pts, NOW + timedelta(seconds=90), ReliabilityPolicy())[rule]["state"]
        == "active"
    )
    assert (
        rules(pts[4:], NOW + timedelta(seconds=90), ReliabilityPolicy())[rule]["state"]
        == "unknown"
    )


def test_duplicate_snapshot_not_counted_as_new_collection(
    postgres_db, isolated, tmp_path
):
    from app.services.monitoring_collectors import collect

    db = postgres_db
    d, j = setup(db)
    cached = collect(db, j, NOW)
    assert poll(db, j, tmp_path, NOW)["result"] == "success"
    pair = claim(db, "w", now=NOW + timedelta(seconds=15))[0]
    r = execute(
        db,
        *pair,
        now=NOW + timedelta(seconds=15),
        collector=lambda *args: cached,
        spool_dir=tmp_path,
    )
    assert r["result"] == "duplicate_telemetry" and j.successes == 0
    assert db.scalar(select(func.count(TelemetrySample.id))) == 1
    assert db.scalar(select(func.count(MonitoringReceipt.id))) == 2


def test_concurrent_claims_do_not_overlap_and_collectors_are_isolated(
    isolated, tmp_path
):
    import os, uuid
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base

    url = os.environ.get("RF_INTEGRATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Disposable PostgreSQL URL required")
    engine = create_engine(url)
    schema = "noc92_test_" + uuid.uuid4().hex[:12]
    with engine.begin() as c:
        assert c.scalar(text("SELECT current_database()")) == "nwo_noc"
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    factory = sessionmaker(scoped)
    try:
        Base.metadata.create_all(scoped)
        with factory() as db:
            for mode in ("normal", "timeout", "disconnected", "weak"):
                setup(db, mode)
            db.commit()
        with factory() as a, factory() as b:
            first = claim(a, "first", now=NOW, limit=2)
            second = claim(b, "second", now=NOW, limit=4)
            assert len(first) == len(second) == 2 and not {key for key, _ in first} & {
                key for key, _ in second
            }
            a.commit()
            b.commit()

        def one(pair):
            with factory() as db:
                result = execute(db, *pair, now=NOW, spool_dir=tmp_path)
                db.commit()
                return result["result"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(one, first + second))
        assert results.count("success") == 2 and sorted(results) == [
            "success",
            "success",
            "timeout",
            "unavailable",
        ]
        with factory() as db:
            assert db.scalar(select(func.count(MonitoringReceipt.id))) == 2
            assert db.scalar(select(func.count(TelemetrySample.id))) == 2
    finally:
        with engine.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_slow_legacy_collector_cannot_block_other_tasks_or_duplicate_itself(caplog):
    from contextlib import contextmanager
    from threading import Event
    from app.services.worker_runtime import LegacyTasks

    @contextmanager
    def factory():
        yield None

    started = Event()
    release = Event()
    other = Event()

    def slow(db):
        started.set()
        assert release.wait(3)

    def failed(db):
        raise ConnectionError("credential-canary-never-log")

    tasks = LegacyTasks(factory=factory)
    try:
        assert tasks.submit("tcp", slow) and started.wait(1)
        assert not tasks.submit("tcp", slow)
        assert tasks.submit("uisp", lambda db: other.set()) and other.wait(1)
        assert tasks.submit("mikrotik", failed)
        assert tasks.pending["mikrotik"].result(timeout=1) == {
            "result": "collection_failed"
        }
    finally:
        release.set()
        tasks.close()
    assert "credential-canary-never-log" not in caplog.text


def test_operator_incident_correlation_deduplicates_and_requires_new_evidence(
    postgres_db,
):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.database import get_db
    from app.api.deps import current_user
    from app.models.entities import User, Incident

    db = postgres_db
    user = User(
        username="noc92-tech",
        email="noc92-tech@synthetic.invalid",
        password_hash="not-a-login-hash",
        role="operator",
        is_active=True,
    )
    parent = Device(
        source="synthetic",
        external_id="parent",
        name="Synthetic upstream",
        enabled=True,
    )
    child = Device(
        source="synthetic",
        external_id="child",
        name="Synthetic downstream",
        enabled=True,
    )
    db.add_all([user, parent, child])
    db.flush()
    child.parent_device_id = parent.id
    at = datetime.now(timezone.utc) - timedelta(seconds=30)
    alerts = [
        MonitoringAlert(
            device_id=d.id,
            series="radio",
            rule="wireless_outage",
            category="rf",
            severity="critical",
            state="active",
            first_seen_at=at,
            updated_at=at,
            evidence={"observed_at": at.isoformat()},
        )
        for d in (parent, child)
    ]
    db.add_all(alerts)
    db.flush()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: user
    try:
        with TestClient(app) as c:
            first = c.post(
                f"/api/monitoring/reliability/alerts/{alerts[1].id}/incident"
            )
            assert first.status_code == 200, first.text
            ident = first.json()["incident_id"]
            incident = db.get(Incident, ident)
            assert (
                incident.device_id == parent.id
                and first.json()["execution_enabled"] is False
            )
            repeated = c.post(
                f"/api/monitoring/reliability/alerts/{alerts[0].id}/incident"
            )
            assert (
                repeated.status_code == 200 and repeated.json()["incident_id"] == ident
            )
            assert db.scalar(select(func.count(Incident.id))) == 1
            incident.status = "closed"
            incident.closed_at = datetime.now(timezone.utc)
            db.commit()
            assert (
                c.post(
                    f"/api/monitoring/reliability/alerts/{alerts[0].id}/incident"
                ).status_code
                == 409
            )
            alerts[0].evidence = {"observed_at": datetime.now(timezone.utc).isoformat()}
            db.flush()
            new = c.post(f"/api/monitoring/reliability/alerts/{alerts[0].id}/incident")
            assert new.status_code == 200 and new.json()["incident_id"] != ident
            assert db.get(Incident, ident).status == "closed"
            assert (
                db.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.action == "incident.monitoring_context"
                    )
                )
                == 3
            )
    finally:
        app.dependency_overrides.clear()
