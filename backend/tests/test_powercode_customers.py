"""Synthetic customer source, private API, topology and no-delivery regressions."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import copy, json, os
import httpx, pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text
from app.main import app
from app.core.config import settings, Settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.entities import (
    User,
    Site,
    Device,
    Sector,
    NetworkLink,
    Incident,
    MaintenanceWindow,
    AuditLog,
)
from app.customers.models import (
    CustomerAccount,
    CustomerService,
    CustomerSyncState,
    CustomerSyncRun,
    IncidentCustomer,
)
from app.customers.contracts import e164, masked_phone, masked_email
from app.customers.providers import FixtureAPI, FixturePostgres, ProviderFailure, parse
from app.customers.sync import synchronize
from app.customers.impact import Target, analyze, recipients
from test_rf_postgres_integration import postgres_db

NOW = datetime.now(timezone.utc) - timedelta(minutes=10)


def batch(i=0, mode="full"):
    return {
        "schema_version": "noc-powercode-export-v1",
        "snapshot_id": f"snapshot-{i}",
        "mode": mode,
        "complete": True,
        "as_of": (NOW + timedelta(seconds=i + 1)).isoformat(),
        "previous_cursor": f"cursor-{i-1}" if i else None,
        "cursor": f"cursor-{i}",
        "accounts": [
            {
                "external_id": "SYN-C1",
                "name": "Synthetic Household One",
                "phone": "+15555550101",
                "email": "synthetic@example.invalid",
                "updated_at": (NOW + timedelta(seconds=i)).isoformat(),
            }
        ],
        "services": [
            {
                "external_id": "SYN-S1",
                "account_id": "SYN-C1",
                "status": "active",
                "service_type": "wireless",
                "address": "1 Synthetic Lane, Test OH",
                "plan": "Synthetic 100/20",
                "download_mbps": 100,
                "upload_mbps": 20,
                "updated_at": (NOW + timedelta(seconds=i)).isoformat(),
                "source_refs": {"tower": "fixture-tower-A"},
            }
        ],
    }


class Provider:
    def __init__(self, payload):
        self.payload = payload
        self.cursor = None

    def fetch(self, *, cursor=None):
        self.cursor = cursor
        return parse(self.payload)


def sync(db, payload=None):
    return synchronize(db, Provider(batch() if payload is None else payload))


@pytest.fixture
def inventory(postgres_db):
    db = postgres_db
    root = Site(name="NOC95 Synthetic core", site_type="core-pop")
    a = Site(name="NOC95 Synthetic tower A")
    b = Site(name="NOC95 Synthetic tower B")
    c = Site(name="NOC95 Synthetic tower C")
    db.add_all([root, a, b, c])
    db.flush()
    ap = Device(
        source="synthetic",
        external_id="noc95-ap",
        name="NOC95 Synthetic Cambium AP",
        vendor="Cambium",
        role="access-point",
        wisp_site_id=a.id,
    )
    db.add(ap)
    db.flush()
    sector = Sector(name="NOC95 Synthetic sector", site_id=a.id, device_id=ap.id)
    db.add(sector)
    up = NetworkLink(
        name="NOC95 upstream",
        link_type="licensed-microwave",
        a_site_id=root.id,
        z_site_id=a.id,
        status="active",
    )
    down = NetworkLink(
        name="NOC95 downstream", a_site_id=a.id, z_site_id=b.id, status="active"
    )
    fiber = NetworkLink(
        name="NOC95 fiber",
        link_type="fiber",
        a_site_id=a.id,
        z_site_id=c.id,
        status="active",
    )
    db.add_all([up, down, fiber])
    db.flush()
    return SimpleNamespace(
        db=db,
        root=root,
        a=a,
        b=b,
        c=c,
        ap=ap,
        sector=sector,
        up=up,
        down=down,
        fiber=fiber,
    )


@pytest.fixture
def imported(inventory):
    x = inventory
    assert sync(x.db)["status"] == "healthy"
    x.service = x.db.scalar(select(CustomerService))
    x.account = x.db.scalar(select(CustomerAccount))
    x.service.site_id = x.a.id
    x.service.device_id = x.ap.id
    x.service.sector_id = x.sector.id
    x.service.mapping_state = "verified"
    x.db.flush()
    return x


def test_full_sync_no_duplicate_accounts_or_services(postgres_db):
    assert sync(postgres_db)["received"] == 2
    assert sync(postgres_db)["status"] == "already_applied"
    payload = batch(1)
    payload["accounts"] *= 2
    payload["services"] *= 2
    assert sync(postgres_db, payload)["received"] == 2
    assert postgres_db.scalar(select(func.count(CustomerAccount.id))) == 1
    assert postgres_db.scalar(select(func.count(CustomerService.id))) == 1


def test_incremental_preserves_absent_and_updates_contacts(postgres_db):
    sync(postgres_db)
    a = postgres_db.scalar(select(CustomerAccount))
    a.sms_consent = "granted"
    a.consent_record = "SYN-RECORD"
    a.communication_preference = "sms"
    postgres_db.flush()
    p = batch(1, "incremental")
    p["services"] = []
    p["accounts"][0]["phone"] = "+15555550102"
    provider = Provider(p)
    assert synchronize(postgres_db, provider)["status"] == "healthy"
    assert provider.cursor == "cursor-0"
    assert a.sms_consent == "unknown" and a.consent_record is None
    assert postgres_db.scalar(select(CustomerService)).source_deleted_at is None


@pytest.mark.parametrize(
    "case,code",
    [
        ("partial", "incomplete_snapshot"),
        ("cursor", "cursor_conflict"),
        ("duplicate", "conflicting_duplicate"),
        ("schema", "schema_mismatch"),
        ("unknown_account", "invalid_reference"),
        ("stale", "stale_snapshot"),
    ],
)
def test_failed_sync_is_atomic_and_retains_cursor(postgres_db, case, code):
    sync(postgres_db)
    p = batch(1, "incremental")
    p["accounts"][0]["name"] = "Never commit this change"
    if case == "partial":
        p["complete"] = False
    if case == "cursor":
        p["previous_cursor"] = "wrong"
    if case == "duplicate":
        p["accounts"].append(p["accounts"][0] | {"name": "Conflicting duplicate"})
    if case == "schema":
        p["schema_version"] = "undocumented-native-schema"
    if case == "unknown_account":
        p["services"][0]["account_id"] = "NO-SUCH-ACCOUNT"
    if case == "stale":
        p["as_of"] = batch()["as_of"]
        p["accounts"] = []
        p["services"] = []
    result = sync(postgres_db, p)
    assert result["error_code"] == code
    state = postgres_db.get(CustomerSyncState, "powercode")
    assert state.cursor == "cursor-0" and state.last_success_at
    assert postgres_db.scalar(select(CustomerAccount)).name == "Synthetic Household One"
    assert postgres_db.scalar(select(CustomerService)).status == "active"
    assert (
        postgres_db.scalar(
            select(CustomerSyncRun).order_by(CustomerSyncRun.id.desc())
        ).status
        == "failed"
    )


def test_full_reconciliation_minimizes_deleted_data(postgres_db):
    sync(postgres_db)
    p = batch(1)
    p["accounts"] = []
    p["services"] = []
    assert sync(postgres_db, p)["reconciled"] == 1
    a = postgres_db.scalar(select(CustomerAccount))
    s = postgres_db.scalar(select(CustomerService))
    assert (
        a.source_deleted_at
        and a.phone is None
        and a.email is None
        and a.name is None
        and a.sms_opt_out
    )
    assert s.status == "disconnected" and s.address is None and s.equipment_id is None


def test_multiple_services_one_account_and_inactive_status(postgres_db):
    p = batch()
    p["services"] += [
        p["services"][0]
        | {"external_id": "SYN-S2", "status": "suspended", "service_type": "fiber"},
        p["services"][0]
        | {"external_id": "SYN-S3", "status": "disconnected", "service_type": "hybrid"},
    ]
    sync(postgres_db, p)
    assert postgres_db.scalar(select(func.count(CustomerAccount.id))) == 1
    assert postgres_db.scalar(select(func.count(CustomerService.id))) == 3
    result = analyze(
        postgres_db,
        Target(site_id=1),
        list(postgres_db.scalars(select(CustomerService))),
    )
    assert [r["classification"] for r in result] == [
        "unknown",
        "not_affected",
        "not_affected",
    ]


def test_changed_tower_hint_requires_operator_review(imported):
    p = batch(1)
    p["services"][0]["source_refs"]["tower"] = "fixture-tower-B"
    sync(imported.db, p)
    assert (
        imported.service.site_id == imported.a.id
        and imported.service.mapping_state == "review_required"
    )
    assert (
        analyze(imported.db, Target(site_id=imported.a.id), [imported.service])[0][
            "classification"
        ]
        == "unknown"
    )


@pytest.mark.parametrize("kind", ["site", "device", "sector", "fiber"])
def test_explicit_dependency_impact(imported, kind):
    x = imported
    if kind == "fiber":
        x.service.service_type = "fiber"
        x.service.device_id = None
        x.service.sector_id = None
        x.service.fiber_link_id = x.fiber.id
        x.db.flush()
    target = {
        "site": Target(site_id=x.a.id),
        "device": Target(device_id=x.ap.id),
        "sector": Target(sector_id=x.sector.id),
        "fiber": Target(link_id=x.fiber.id),
    }[kind]
    result = analyze(x.db, target, [x.service])[0]
    assert (
        result["classification"] == "confirmed"
        and result["evidence"]["claim"] == "potential dependency impact only"
    )


def test_backhaul_multiple_towers_and_redundancy(imported):
    x = imported
    s2 = CustomerService(
        external_id="SYN-S2",
        customer_id=x.account.id,
        status="active",
        service_type="wireless",
        site_id=x.b.id,
        device_id=None,
        sector_id=None,
        mapping_state="verified",
        source_refs={},
        source_updated_at=NOW,
        last_synced_at=datetime.now(timezone.utc),
    )
    # A second actual access point is mapped; co-location is not inferred.
    ap2 = Device(
        source="synthetic",
        external_id="noc95-ap2",
        name="Synthetic Ubiquiti AP",
        vendor="Ubiquiti",
        wisp_site_id=x.b.id,
    )
    x.db.add(ap2)
    x.db.flush()
    s2.device_id = ap2.id
    x.db.add(s2)
    x.db.flush()
    rows = analyze(x.db, Target(link_id=x.up.id), [x.service, s2])
    assert {r["classification"] for r in rows} == {"probable"}
    backup = NetworkLink(
        name="NOC95 redundant", a_site_id=x.root.id, z_site_id=x.b.id, status="active"
    )
    x.db.add(backup)
    x.db.flush()
    assert {
        r["reason"] for r in analyze(x.db, Target(link_id=x.up.id), [x.service, s2])
    } == {"remaining_recorded_path"}
    backup.status = "unknown"
    x.db.flush()
    assert {
        r["classification"]
        for r in analyze(x.db, Target(link_id=x.up.id), [x.service, s2])
    } == {"unknown"}


def test_hybrid_failure_does_not_invent_failover(imported):
    x = imported
    x.service.service_type = "hybrid"
    x.service.fiber_link_id = x.fiber.id
    x.db.flush()
    assert (
        analyze(x.db, Target(link_id=x.fiber.id), [x.service])[0]["reason"]
        == "hybrid_failover_unverified"
    )


def test_stale_service_does_not_claim_confirmed_impact(imported):
    x = imported
    x.service.last_synced_at = datetime.now(timezone.utc) - timedelta(days=2)
    assert (
        analyze(x.db, Target(site_id=x.a.id), [x.service])[0]["reason"]
        == "stale_customer_data"
    )


def test_missing_contacts_consent_optout_and_global_dedup(imported):
    x = imported
    result = analyze(x.db, Target(site_id=x.a.id), [x.service])
    assert recipients(x.db, result)["recipients"][0]["reason"] == "consent_unverified"
    x.account.sms_consent = "granted"
    x.account.communication_preference = "sms"
    x.account.consent_record = "SYN-CONSENT"
    x.db.flush()
    assert recipients(x.db, result, reveal=True)["eligible_count"] == 1
    x.account.sms_opt_out = True
    x.db.flush()
    assert recipients(x.db, result)["recipients"][0]["reason"] == "opted_out"
    x.account.sms_opt_out = False
    other = CustomerAccount(
        external_id="SYN-C2",
        phone=x.account.phone,
        source_updated_at=NOW,
        last_synced_at=datetime.now(timezone.utc),
    )
    x.db.add(other)
    x.db.flush()
    assert (
        recipients(x.db, result, reveal=True)["recipients"][0]["reason"]
        == "shared_number_review"
    )
    assert recipients(x.db, result, reveal=True)["recipients"][0]["phone"] == "***0101"
    x.account.phone = None
    x.db.flush()
    assert recipients(x.db, result)["eligible_count"] == 0


@pytest.mark.parametrize(
    "number,valid",
    [
        ("+15555550101", True),
        ("+447700900123", True),
        ("555-555-0101", False),
        ("15555550101", False),
        ("+05555550101", False),
        ("+15555550101 ext2", False),
        (None, False),
    ],
)
def test_phone_validation_no_guessed_country_code(number, valid):
    assert bool(e164(number)) is valid


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (429, "rate_limited"),
        (500, "unavailable"),
        (302, "schema_mismatch"),
    ],
)
def test_mock_api_errors_are_static_and_bounded(status, code, monkeypatch):
    monkeypatch.setattr("app.customers.providers.disabled", lambda: False)
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(
            status, text="secret +15555550101 synthetic@example.invalid"
        )

    with pytest.raises(ProviderFailure, match=code):
        FixtureAPI(httpx.MockTransport(handler), sleep=lambda _: None).fetch()
    assert len(calls) == (3 if status in (429, 500) else 1)
    assert all(r.method == "GET" for r in calls)


def test_timeout_retry_and_recovery(monkeypatch):
    monkeypatch.setattr("app.customers.providers.disabled", lambda: False)
    calls = []

    def handler(r):
        calls.append(r)
        if len(calls) < 3:
            raise httpx.ReadTimeout("Never log source details", request=r)
        return httpx.Response(200, json=batch())

    sleeps = []
    provider = FixtureAPI(httpx.MockTransport(handler), sleep=sleeps.append)
    assert provider.fetch(cursor="opaque-cursor").cursor == "cursor-0" and sleeps == [
        0.25,
        0.5,
    ]
    assert all("opaque-cursor" not in str(r.url) for r in calls)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"schema_version": "bad"},
        batch() | {"password": "do-not-import"},
        batch() | {"payment_card": "do-not-import"},
    ],
)
def test_malformed_and_forbidden_fields_are_rejected(payload):
    with pytest.raises(ProviderFailure, match="schema_mismatch"):
        parse(payload)


def test_live_network_construction_and_enable_are_rejected():
    with pytest.raises(ProviderFailure, match="disabled"):
        FixtureAPI(None)
    with pytest.raises(ValueError):
        Settings(
            secret_key="x" * 40,
            admin_password="synthetic-only",
            database_url="sqlite://",
            powercode_live_enabled=True,
        )


def test_actual_fixture_postgres_read_only_schema_validation(postgres_db):
    import psycopg
    from psycopg.types.json import Jsonb

    url = os.environ["RF_INTEGRATION_TEST_DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://"
    )
    with psycopg.connect(url, autocommit=True) as conn:
        # Only the explicitly disposable integration DB, checked by postgres_db.
        conn.execute("CREATE SCHEMA noc95_export")
        try:
            conn.execute("CREATE TABLE noc95_export.fixture (payload jsonb)")
            conn.execute(
                "INSERT INTO noc95_export.fixture VALUES (%s)", (Jsonb(batch()),)
            )
            conn.execute(
                "CREATE VIEW noc95_export.customer_batch AS SELECT payload FROM noc95_export.fixture"
            )
            assert (
                FixturePostgres(conn).fetch().schema_version
                == "noc-powercode-export-v1"
            )
            conn.execute("DROP VIEW noc95_export.customer_batch")
            conn.execute(
                "CREATE VIEW noc95_export.customer_batch AS SELECT payload::text AS payload FROM noc95_export.fixture"
            )
            with pytest.raises(ProviderFailure, match="schema_mismatch"):
                FixturePostgres(conn).fetch()
            assert conn.execute(
                "SELECT count(*) FROM noc95_export.fixture"
            ).fetchone() == (1,)
        finally:
            conn.execute("DROP SCHEMA noc95_export CASCADE")


@pytest.fixture
def api(imported):
    x = imported
    u = User(
        username="noc95-operator",
        email="noc95-operator@example.invalid",
        password_hash=hash_password("synthetic-only-pass"),
        role="operator",
        is_active=True,
    )
    x.db.add(u)
    x.db.commit()
    app.dependency_overrides[get_db] = lambda: x.db
    with TestClient(app) as client:
        yield x, u, client, {
            "Authorization": "Bearer " + create_access_token(u.username, user_id=u.id)
        }
    app.dependency_overrides.clear()


@pytest.mark.parametrize("role", ["viewer", "unknown", "operator", "admin"])
@pytest.mark.parametrize(
    "path,method,body",
    [
        ("/api/customers", "GET", None),
        ("/api/customers/search", "POST", {"q": "Synthetic"}),
        ("/api/customers/impact-preview", "POST", {"site_id": 999999}),
        ("/api/customers/incidents/999999/assess", "POST", {}),
    ],
)
def test_customer_apis_require_operator(api, role, path, method, body):
    x, u, c, headers = api
    u.role = role
    x.db.flush()
    r = c.request(method, path, headers=headers, json=body)
    if role in {"viewer", "unknown"}:
        assert r.status_code == 403 and "Synthetic Household" not in r.text
    else:
        assert r.status_code in (200, 404)


def test_anonymous_access_has_no_customer_data(api):
    _, _, c, _ = api
    r = c.get("/api/customers")
    assert r.status_code == 401 and "Synthetic" not in r.text


def test_private_search_masked_contacts_and_audit(api, caplog):
    x, u, c, h = api
    r = c.post("/api/customers/search", headers=h, json={"q": "Synthetic Household"})
    assert r.status_code == 200
    data = r.json()
    assert data["total_customers"] == 1 and data["active_customers"] == 1
    assert (
        data["items"][0]["phone"] == "***0101"
        and data["items"][0]["email"] == "***@example.invalid"
    )
    a = x.db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "customer.accessed")
        .order_by(AuditLog.id.desc())
    )
    assert a.user_id == u.id
    assert (
        "Household" not in json.dumps(a.details)
        and "+15555550101" not in caplog.text
        and "synthetic@example.invalid" not in caplog.text
    )
    assert (
        c.post("/api/customers/search", headers=h, json={"q": "%"}).json()["total"] == 0
    )


def test_mapping_workflow_and_cross_site_rejection(api):
    x, u, c, h = api
    path = f"/api/customers/services/{x.service.id}/mapping"
    assert (
        c.patch(
            path,
            headers=h,
            json={
                "site_id": x.b.id,
                "device_id": x.ap.id,
                "note": "Synthetic evidence",
            },
        ).status_code
        == 409
    )
    r = c.patch(
        path,
        headers=h,
        json={
            "site_id": x.a.id,
            "device_id": x.ap.id,
            "sector_id": x.sector.id,
            "note": "Synthetic evidence",
        },
    )
    assert r.status_code == 200 and r.json()["network_configuration_changed"] is False


def test_preferences_consent_evidence_and_maintenance_preview(api):
    x, u, c, h = api
    path = f"/api/customers/accounts/{x.account.id}/preferences"
    body = {
        "consent": "granted",
        "opt_out": False,
        "preference": "sms",
        "consent_record": None,
    }
    assert c.patch(path, headers=h, json=body).status_code == 409
    assert (
        c.patch(
            path, headers=h, json=body | {"consent_record": "SYN-CONSENT-95"}
        ).status_code
        == 200
    )
    m = MaintenanceWindow(
        title="Synthetic maintenance",
        site_id=x.a.id,
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=1),
    )
    x.db.add(m)
    x.db.flush()
    data = c.post(
        "/api/customers/impact-preview", headers=h, json={"maintenance_id": m.id}
    ).json()
    assert (
        data["recipient_preview"]["eligible_count"] == 1
        and data["delivery_enabled"] is False
    )
    assert data["items"][0]["classification"] == "confirmed"
    assert "+15555550101" not in json.dumps(data)


def test_incident_customer_assessment_idempotence(api):
    x, u, c, h = api
    i = Incident(title="Synthetic AP incident", device_id=x.ap.id)
    x.db.add(i)
    x.db.flush()
    for _ in range(2):
        assert (
            c.post(f"/api/customers/incidents/{i.id}/assess", headers=h).status_code
            == 200
        )
    assert x.db.scalar(select(func.count(IncidentCustomer.id))) == 1
    assert (
        c.post("/api/customers/search", headers=h, json={"incident_id": i.id}).json()[
            "total"
        ]
        == 1
    )


def test_fixture_sync_disabled_and_admin_only(api, monkeypatch):
    x, u, c, h = api
    assert (
        c.post("/api/customers/fixture-sync", headers=h, json=batch(1)).status_code
        == 403
    )
    u.role = "admin"
    x.db.flush()
    monkeypatch.setattr(settings, "powercode_fixture_enabled", False)
    assert (
        c.post("/api/customers/fixture-sync", headers=h, json=batch(1)).status_code
        == 409
    )
    monkeypatch.setattr(settings, "powercode_fixture_enabled", True)
    monkeypatch.setattr(settings, "offline_mode", True)
    monkeypatch.setattr(settings, "app_env", "staging")
    assert (
        c.post("/api/customers/fixture-sync", headers=h, json=batch(1)).json()["status"]
        == "healthy"
    )


def test_validation_does_not_echo_sensitive_input(api):
    _, _, c, h = api
    r = c.post(
        "/api/customers/search",
        headers=h,
        json={"q": "a" * 101, "password": "secret", "email": "secret@example.invalid"},
    )
    assert r.status_code == 422 and "secret" not in r.text


def test_source_failure_then_recovery_preserves_data(postgres_db):
    sync(postgres_db)

    class Offline:
        def fetch(self, **kwargs):
            raise ProviderFailure("timeout")

    assert synchronize(postgres_db, Offline())["error_code"] == "timeout"
    assert postgres_db.get(CustomerSyncState, "powercode").cursor == "cursor-0"
    assert sync(postgres_db, batch(1, "incremental"))["status"] == "healthy"
    assert postgres_db.get(CustomerSyncState, "powercode").last_error is None


def test_service_identifier_cannot_change_customer(postgres_db):
    sync(postgres_db)
    p = batch(1)
    p["accounts"].append(p["accounts"][0] | {"external_id": "SYN-C2"})
    p["services"][0]["account_id"] = "SYN-C2"
    assert sync(postgres_db, p)["error_code"] == "invalid_reference"
    assert postgres_db.scalar(select(func.count(CustomerAccount.id))) == 1


def test_unmapped_customers_never_infer_name_matches(imported):
    x = imported
    x.service.mapping_state = "unknown"
    x.service.site_id = None
    x.db.flush()
    assert (
        analyze(x.db, Target(site_id=x.a.id), [x.service])[0]["classification"]
        == "unknown"
    )


def test_backhaul_endpoint_device_failure_correlates(imported):
    x = imported
    router = Device(
        source="synthetic",
        external_id="noc95-router",
        name="Synthetic MikroTik backhaul",
        vendor="MikroTik",
        role="backhaul",
        wisp_site_id=x.root.id,
    )
    x.db.add(router)
    x.db.flush()
    x.up.a_device_id = router.id
    x.db.flush()
    assert (
        analyze(x.db, Target(device_id=router.id), [x.service])[0]["classification"]
        == "probable"
    )


def test_unmodeled_router_dependency_stays_unknown(imported):
    x = imported
    router = Device(
        source="synthetic",
        external_id="noc95-unknown-router",
        name="Unmodeled MikroTik router",
        vendor="MikroTik",
        role="tower-router",
        wisp_site_id=x.a.id,
    )
    x.db.add(router)
    x.db.flush()
    assert (
        analyze(x.db, Target(device_id=router.id), [x.service])[0]["classification"]
        == "unknown"
    )


def test_unknown_impacts_never_select_recipients(imported):
    x = imported
    x.service.mapping_state = "unknown"
    x.account.sms_consent = "granted"
    x.account.consent_record = "SYN"
    x.account.communication_preference = "sms"
    x.db.flush()
    assert (
        recipients(x.db, analyze(x.db, Target(site_id=x.a.id), [x.service]))[
            "recipients"
        ]
        == []
    )


def test_export_adapter_cannot_bypass_emergency_stop(monkeypatch):
    monkeypatch.setattr("app.customers.providers.disabled", lambda: True)
    transport = httpx.MockTransport(
        lambda r: pytest.fail("Source contacted after stop")
    )
    with pytest.raises(ProviderFailure, match="disabled"):
        FixtureAPI(transport).fetch()


def test_source_invalid_json_and_timeout_are_sanitized(monkeypatch):
    monkeypatch.setattr("app.customers.providers.disabled", lambda: False)
    with pytest.raises(ProviderFailure, match="schema_mismatch"):
        FixtureAPI(
            httpx.MockTransport(
                lambda r: httpx.Response(200, text="secret@example.invalid")
            ),
            sleep=lambda _: None,
        ).fetch()

    def timeout(r):
        raise httpx.ReadTimeout("sensitive-source-error", request=r)

    with pytest.raises(ProviderFailure, match="^timeout$"):
        FixtureAPI(httpx.MockTransport(timeout), sleep=lambda _: None).fetch()


def test_fixture_database_connection_restrictions():
    for host, dbname, autocommit in [
        ("10.0.0.1", "nwo_noc", True),
        ("127.0.0.1", "production", True),
        ("127.0.0.1", "nwo_noc", False),
    ]:
        conn = SimpleNamespace(
            info=SimpleNamespace(host=host, dbname=dbname), autocommit=autocommit
        )
        with pytest.raises(ProviderFailure, match="disabled"):
            FixturePostgres(conn)


def test_overlapping_sync_cannot_advance_cursor(postgres_db):
    import psycopg

    url = os.environ["RF_INTEGRATION_TEST_DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://"
    )
    with psycopg.connect(url, autocommit=True) as connection:
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(950001)")

            class NeverFetch:
                def fetch(self, **kwargs):
                    pytest.fail("Overlapping sync reached source")

            with pytest.raises(ProviderFailure, match="overlap_skipped"):
                synchronize(postgres_db, NeverFetch())
    assert postgres_db.get(CustomerSyncState, "powercode") is None


def test_invalid_mapping_after_inventory_deletion_stays_unknown(imported):
    x = imported
    x.service.device_id = 999999  # In-memory corruption is rejected before FK flush.
    with x.db.no_autoflush:
        assert (
            analyze(x.db, Target(link_id=x.up.id), [x.service])[0]["classification"]
            == "unknown"
        )
    x.service.device_id = x.ap.id


def test_unchanged_timestamp_cannot_overwrite_record(postgres_db):
    sync(postgres_db)
    p = batch(1, "incremental")
    p["accounts"][0] = batch()["accounts"][0] | {
        "name": "Contradictory same-time update"
    }
    assert sync(postgres_db, p)["error_code"] == "conflicting_duplicate"
    assert postgres_db.scalar(select(CustomerAccount)).name == "Synthetic Household One"


def test_deleted_account_not_resurrected_by_old_incremental(postgres_db):
    sync(postgres_db)
    p = batch(3)
    p["accounts"] = []
    p["services"] = []
    sync(postgres_db, p)
    p = batch(4, "incremental")
    p["previous_cursor"] = "cursor-3"
    p["accounts"] = batch()["accounts"]
    p["services"] = batch()["services"]
    assert sync(postgres_db, p)["status"] == "failed"
    assert postgres_db.scalar(select(CustomerAccount)).source_deleted_at
    assert postgres_db.scalar(select(CustomerService)).status == "disconnected"


@pytest.mark.parametrize(
    "bad",
    [
        {"retries": 3},
        {"timeout_seconds": 0},
        {"connect_seconds": 10},
        {"max_bytes": 999999999},
    ],
)
def test_provider_limits_are_bounded(bad):
    with pytest.raises(ValueError):
        FixtureAPI(
            httpx.MockTransport(lambda r: httpx.Response(200, json=batch())), limits=bad
        )


def test_incident_location_context_does_not_expand_ap_outage(imported):
    from app.customers.impact import target_for

    x = imported
    i = Incident(
        title="Synthetic sector-specific incident", site_id=x.a.id, device_id=x.ap.id
    )
    x.db.add(i)
    x.db.flush()
    assert target_for(x.db, incident_id=i.id) == Target(device_id=x.ap.id)
    i.link_id = x.up.id
    x.db.flush()
    assert target_for(x.db, incident_id=i.id) == Target(link_id=x.up.id)


def test_hybrid_wireless_failure_preserves_failover_uncertainty(imported):
    x = imported
    x.service.service_type = "hybrid"
    x.service.fiber_link_id = x.fiber.id
    x.db.flush()
    assert (
        analyze(x.db, Target(sector_id=x.sector.id), [x.service])[0]["reason"]
        == "hybrid_failover_unverified"
    )


def test_customer_without_contacts_or_source_assignments_is_not_invented(postgres_db):
    p = batch()
    p["accounts"][0]["phone"] = None
    p["accounts"][0]["email"] = None
    p["services"][0]["source_refs"] = {}
    p["services"][0]["download_mbps"] = None
    p["services"][0]["upload_mbps"] = None
    sync(postgres_db, p)
    a = postgres_db.scalar(select(CustomerAccount))
    s = postgres_db.scalar(select(CustomerService))
    assert (
        a.phone is None
        and a.email is None
        and s.site_id is None
        and s.download_mbps is None
    )


def test_complete_incremental_verifies_unchanged_records(imported):
    x = imported
    x.service.last_synced_at = NOW - timedelta(days=2)
    x.account.last_synced_at = NOW - timedelta(days=2)
    x.db.flush()
    p = batch(1, "incremental")
    p["accounts"] = []
    p["services"] = []
    assert sync(x.db, p)["status"] == "healthy"
    assert (
        analyze(x.db, Target(site_id=x.a.id), [x.service])[0]["classification"]
        == "confirmed"
    )
    assert x.account.last_synced_at > NOW


def test_older_record_does_not_advance_successful_cursor(postgres_db):
    sync(postgres_db)
    p = batch(1, "incremental")
    p["accounts"][0]["updated_at"] = (NOW - timedelta(seconds=1)).isoformat()
    assert sync(postgres_db, p)["error_code"] == "stale_snapshot"
    assert postgres_db.get(CustomerSyncState, "powercode").cursor == "cursor-0"
