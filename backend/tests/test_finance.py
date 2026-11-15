"""Synthetic operational finances against disposable PostgreSQL; no billing/network clients."""

from datetime import date, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import uuid4
import pytest
from pydantic import ValidationError
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from fastapi.testclient import TestClient
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.entities import User, Site, Sector, AuditLog
from app.customers.models import CustomerAccount, CustomerService
from app.field.models import now
from app.finance import service as s
from app.finance.models import *
from app.finance.contracts import *
from test_rf_postgres_integration import postgres_db

R = "Synthetic operational financial test; no billing or production changes"


def b(cls, **kw):
    return cls(operation_key=uuid4(), reason=R, **kw)


@pytest.fixture
def x(postgres_db):
    db = postgres_db
    u = User(
        username="finance-admin",
        email="finance@example.invalid",
        password_hash="unusable",
        role="admin",
        is_active=True,
    )
    db.add(u)
    db.flush()
    sites = []
    sectors = []
    services = []
    for i in range(2):
        site = Site(name=f"Synthetic finance POP {i}")
        db.add(site)
        db.flush()
        sites.append(site)
        sector = Sector(
            name=f"Synthetic finance sector {i}", site_id=site.id, capacity_mbps=200
        )
        db.add(sector)
        db.flush()
        sectors.append(sector)
        customer = CustomerAccount(
            external_id=f"financial-customer-{i}",
            source_updated_at=now(),
            last_synced_at=now(),
        )
        db.add(customer)
        db.flush()
        service = CustomerService(
            external_id=f"financial-service-{i}",
            customer_id=customer.id,
            status="active",
            service_type="wireless",
            plan="PLAN-A",
            site_id=site.id,
            sector_id=sector.id,
            mapping_state="verified",
            source_updated_at=now(),
            last_synced_at=now(),
        )
        db.add(service)
        db.flush()
        services.append(service)
    p = s.create_period(
        db,
        u,
        b(
            PeriodInput,
            code="2026-01",
            starts_on=date(2026, 1, 1),
            ends_on=date(2026, 2, 1),
            currency="USD",
        ),
    )
    scopes = {}
    for code, kind, id in [
        ("network", "network", None),
        ("tower0", "tower", sites[0].id),
        ("tower1", "tower", sites[1].id),
        ("sector0", "sector", sectors[0].id),
        ("sector1", "sector", sectors[1].id),
        ("service0", "service", services[0].id),
        ("service1", "service", services[1].id),
    ]:
        scopes[code] = s.create_scope(
            db, u, b(ScopeInput, code=code, name=code, kind=kind, reference_id=id)
        )["id"]
    return SimpleNamespace(
        db=db,
        u=u,
        p=p["id"],
        scopes=scopes,
        sites=sites,
        sectors=sectors,
        services=services,
    )


def entry(
    x,
    scope="service0",
    amount="100.00",
    kind="revenue",
    category=None,
    basis=None,
    **kw,
):
    return s.add_entry(
        x.db,
        x.u,
        x.p,
        b(
            EntryInput,
            scope_id=x.scopes[scope],
            kind=kind,
            category=category
            or (
                "subscription"
                if kind == "revenue"
                else "equipment" if kind == "capex" else "power"
            ),
            basis=basis or ("contracted" if kind == "revenue" else "actual"),
            amount=amount,
            external_key=uuid4().hex,
            evidence=R,
            **kw,
        ),
    )


def report(x, scope="tower0", **kw):
    return s.calculate(x.db, x.p, x.scopes[scope], **kw)


def attest(x, scope="tower0", categories=None):
    return s.attest(
        x.db,
        x.u,
        x.p,
        b(
            CoverageInput,
            scope_id=x.scopes[scope],
            revenue_basis="contracted",
            expense_basis="actual",
            categories=list(categories or (OPEX | {"revenue", "capital"})),
            evidence=R,
        ),
    )


def client(x, role="admin"):
    user = x.u
    if role != "admin":
        user = User(
            username="finance-" + role,
            email=role + "@example.invalid",
            password_hash="unusable",
            role=role,
            is_active=True,
        )
        x.db.add(user)
        x.db.flush()
    app.dependency_overrides[get_db] = lambda: x.db
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + create_access_token(
        user.username, user_id=user.id
    )
    return c


def test_missing_inputs_do_not_imply_profitable(x):
    entry(x)
    entry(x, scope="tower0", kind="opex", category="lease", amount="10")
    r = report(x)
    assert r["contribution"] is None and not r["complete"]
    assert r["known_contribution_subtotal"] == "90.00"
    assert r["payback_months"] is None


def test_tower_sector_and_network_totals_no_double_count(x):
    entry(x)
    entry(x, scope="service1", amount="200")
    entry(x, scope="sector0", kind="opex", amount="30")
    attest(x)
    assert report(x)["contribution"] == "70.00"
    assert report(x, "sector0")["totals"]["revenue"] == "100.00"
    assert report(x, "network")["totals"]["revenue"] == "300.00"


def test_roi_payback_and_arpu_explicit_period(x):
    entry(x, amount="1000")
    entry(x, kind="opex", scope="tower0", amount="100")
    entry(x, kind="capex", scope="tower0", amount="9000")
    attest(x)
    r = report(x)
    assert r["operational_roi_percent"] == "10.00"
    assert D(r["payback_months"]) > 10
    assert r["revenue_per_customer"] == "1000.00"
    assert r["cost_per_customer"] == "100.00"


@pytest.mark.parametrize("basis", ["contracted", "invoiced", "estimated", "collected"])
def test_revenue_bases_are_not_combined(x, basis):
    for kind in ["contracted", "invoiced", "estimated", "collected"]:
        entry(x, basis=kind, verified_collection=kind == "collected")
    r = report(x, revenue_basis=basis)
    assert r["totals"]["revenue"] == "100.00"
    assert ("not collected cash" in r["basis_label"]) == (basis != "collected")


def test_unverified_collected_rejected():
    with pytest.raises(ValidationError):
        b(
            EntryInput,
            scope_id=1,
            kind="revenue",
            category="subscription",
            basis="collected",
            amount="100",
            external_key="cash",
            evidence=R,
        )


@pytest.mark.parametrize(
    "method",
    ["equal", "customer_count", "revenue_weighted", "capacity_weighted", "manual"],
)
def test_shared_cost_allocation_conserves_cents(x, method):
    entry(x)
    entry(x, scope="service1", amount="300")
    e = entry(x, scope="network", kind="opex", category="backhaul", amount="100.01")
    targets = [{"scope_id": x.scopes["sector0"]}, {"scope_id": x.scopes["sector1"]}]
    if method in {"manual", "capacity_weighted"}:
        targets[0]["weight"] = "1"
        targets[1]["weight"] = "3"
    a = s.allocate(
        x.db, x.u, e["id"], b(AllocationInput, method=method, targets=targets)
    )
    assert sum(D(v["amount"]) for v in a["shares"]) == D("100.01")
    assert report(x, "network")["totals"]["opex"] == "100.01"
    assert D(report(x, "tower0")["totals"]["opex"]) + D(
        report(x, "tower1")["totals"]["opex"]
    ) == D("100.01")
    assert a["method"] == method


def test_duplicate_allocation_and_target_rejected(x):
    e = entry(x, scope="network", kind="opex")
    args = b(
        AllocationInput, method="equal", targets=[{"scope_id": x.scopes["tower0"]}]
    )
    s.allocate(x.db, x.u, e["id"], args)
    with pytest.raises(ValueError):
        s.allocate(x.db, x.u, e["id"], args)
    with pytest.raises(ValidationError):
        b(AllocationInput, method="equal", targets=[{"scope_id": 1}, {"scope_id": 1}])


def test_zero_customer_weight_not_invented(x):
    e = entry(x, scope="network", kind="opex")
    x.services[1].status = "inactive"
    x.db.flush()
    with pytest.raises(ValueError):
        s.allocate(
            x.db,
            x.u,
            e["id"],
            b(
                AllocationInput,
                method="customer_count",
                targets=[
                    {"scope_id": x.scopes["tower0"]},
                    {"scope_id": x.scopes["tower1"]},
                ],
            ),
        )
    # Explicit capacity inputs are mandatory, never inferred from interface throughput.
    with pytest.raises(ValidationError):
        b(
            AllocationInput,
            method="capacity_weighted",
            targets=[{"scope_id": x.scopes["tower0"]}],
        )


def test_duplicate_expense_source_constraint(x):
    args = b(
        EntryInput,
        scope_id=x.scopes["tower0"],
        kind="opex",
        category="power",
        basis="actual",
        amount="20",
        external_key="invoice-1",
        evidence=R,
    )
    s.add_entry(x.db, x.u, x.p, args)
    with pytest.raises(IntegrityError):
        with x.db.begin_nested():
            s.add_entry(x.db, x.u, x.p, args)


def test_reversal_preserves_original_and_allocated_cost(x):
    e = entry(x, scope="network", kind="opex", amount="100")
    s.allocate(
        x.db,
        x.u,
        e["id"],
        b(
            AllocationInput,
            method="equal",
            targets=[
                {"scope_id": x.scopes["tower0"]},
                {"scope_id": x.scopes["tower1"]},
            ],
        ),
    )
    s.reverse(x.db, x.u, e["id"], b(ReversalInput))
    assert report(x, "tower0")["totals"]["opex"] == "0.00"
    assert x.db.get(Entry, e["id"]).amount == D("100")
    with pytest.raises(ValueError):
        s.reverse(x.db, x.u, e["id"], b(ReversalInput))


def test_immutable_ledger_database_guard(x):
    e = entry(x)
    with pytest.raises(DBAPIError):
        with x.db.begin_nested():
            x.db.execute(
                text("UPDATE finance_entries SET amount=1 WHERE id=:id"),
                {"id": e["id"]},
            )


def test_historical_dimensions_survive_customer_move(x):
    entry(x)
    x.services[0].site_id = x.sites[1].id
    x.services[0].sector_id = x.sectors[1].id
    x.db.flush()
    assert report(x)["totals"]["revenue"] == "100.00"
    assert report(x, "tower1")["totals"]["revenue"] == "0.00"


def test_complete_zero_revenue_does_not_divide_by_zero(x):
    attest(x)
    r = report(x)
    assert r["contribution"] == "0"
    assert r["operating_margin_percent"] is None
    assert r["payback_months"] is None


def test_filtered_slice_does_not_inherit_complete_attestation(x):
    entry(x)
    attest(x)
    r = report(x, service_type="wireless")
    assert not r["complete"]
    assert r["totals"]["revenue"] == "100.00"


def test_scenario_is_advisory_and_does_not_mutate_actuals(x):
    entry(x)
    attest(x)
    before = report(x)["totals"]
    r = s.scenario(
        x.db,
        x.u,
        x.p,
        b(
            ScenarioInput,
            scope_id=x.scopes["tower0"],
            name="Sector upgrade",
            monthly_revenue_delta="100",
            monthly_opex_delta="20",
            capex="800",
            additional_customers=2,
            evidence=R,
        ),
    )
    assert r["result"]["incremental_payback_months"] == "10.00"
    assert len(r["result"]["sensitivity"]) == 3
    assert report(x)["totals"] == before


def test_scenario_incomplete_baseline_has_no_proposed_total(x):
    r = s.scenario(
        x.db,
        x.u,
        x.p,
        b(
            ScenarioInput,
            scope_id=x.scopes["tower0"],
            name="Assumption only",
            monthly_revenue_delta="20",
            monthly_opex_delta="30",
            capex="100",
            evidence=R,
        ),
    )
    assert r["result"]["monthly_proposed_contribution"] is None
    assert r["result"]["incremental_payback_months"] is None


def test_retirement_requires_review_and_preserves_customers(x):
    r = s.retirement(x.db, x.u, x.p, b(RetirementInput, scope_id=x.scopes["tower0"]))
    assert not r["automatic_retirement"]
    assert r["active_customer_count"] == 1
    assert r["removal_cost"] is None
    assert x.services[0].status == "active"


def charge(x, **kw):
    return {
        "external_key": "charge-1",
        "service_external_id": x.services[0].external_id,
        "amount": "75.00",
        "basis": "contracted",
        "currency": "USD",
        "evidence": R,
        **kw,
    }


def import_charge(x, **kw):
    return s.preview_import(
        x.db,
        x.u,
        x.p,
        b(ImportInput, provider="powercode_mock", charges=[charge(x, **kw)]),
    )


def test_powercode_preview_approval_and_deduplication(x):
    r = import_charge(x)
    assert report(x)["totals"]["revenue"] == "0.00"
    s.approve_import(x.db, x.u, r["id"], b(ApprovalInput, digest=r["digest"]))
    assert report(x)["totals"]["revenue"] == "75.00"
    again = import_charge(x)
    s.approve_import(x.db, x.u, again["id"], b(ApprovalInput, digest=again["digest"]))
    assert report(x)["totals"]["revenue"] == "75.00"
    assert x.services[0].status == "active"


def test_powercode_bill_is_not_collection(x):
    r = import_charge(x, basis="invoiced")
    s.approve_import(x.db, x.u, r["id"], b(ApprovalInput, digest=r["digest"]))
    assert report(x, revenue_basis="collected")["totals"]["revenue"] == "0.00"
    with pytest.raises(ValueError):
        import_charge(x, basis="collected")


def test_mock_import_invalid_mapping_currency_and_duplicates(x):
    for kw in [{"service_external_id": "not-existing"}, {"currency": "EUR"}]:
        with pytest.raises(ValueError):
            import_charge(x, **kw)
    with pytest.raises(ValueError):
        s.preview_import(
            x.db,
            x.u,
            x.p,
            b(ImportInput, provider="powercode_mock", charges=[charge(x), charge(x)]),
        )


def test_import_approval_detects_stale_native_mapping(x):
    r = import_charge(x)
    x.services[0].status = "cancelled"
    x.db.flush()
    with pytest.raises(ValueError):
        s.approve_import(x.db, x.u, r["id"], b(ApprovalInput, digest=r["digest"]))
    assert x.db.scalar(select(func.count()).select_from(Entry)) == 0


def test_recurring_expense_generation_idempotent(x):
    s.recurring_create(
        x.db,
        x.u,
        b(
            RecurringInput,
            scope_id=x.scopes["tower0"],
            code="power-contract",
            category="power",
            amount="45",
            currency="USD",
            basis="estimated",
            starts_on=date(2026, 1, 1),
            evidence=R,
        ),
    )
    s.materialize(x.db, x.u, x.p, b(Mutation))
    s.materialize(x.db, x.u, x.p, b(Mutation))
    assert report(x, expense_basis="estimated")["totals"]["opex"] == "45.00"
    assert report(x)["totals"]["opex"] == "0.00"


def test_period_overlap_rejected_and_capital_carried_forward(x):
    with pytest.raises(ValueError):
        s.create_period(
            x.db,
            x.u,
            b(
                PeriodInput,
                code="overlap",
                starts_on=date(2026, 1, 15),
                ends_on=date(2026, 2, 15),
                currency="USD",
            ),
        )
    entry(x, scope="tower0", kind="capex", amount="1000")
    p = s.create_period(
        x.db,
        x.u,
        b(
            PeriodInput,
            code="2026-02",
            starts_on=date(2026, 2, 1),
            ends_on=date(2026, 3, 1),
            currency="USD",
        ),
    )
    assert (
        s.calculate(x.db, p["id"], x.scopes["tower0"])["totals"]["capex"] == "1000.00"
    )


def test_report_snapshot_historical_cache_is_stable(x):
    entry(x)
    r = s.snapshot(x.db, x.u, x.p, x.scopes["tower0"], "contracted", "actual")
    again = s.snapshot(x.db, x.u, x.p, x.scopes["tower0"], "contracted", "actual")
    assert r["id"] == again["id"]
    entry(x, amount="5")
    new = s.snapshot(x.db, x.u, x.p, x.scopes["tower0"], "contracted", "actual")
    assert new["id"] != r["id"]
    assert x.db.get(Report, r["id"]).result["totals"]["revenue"] == "100.00"


@pytest.mark.parametrize("role", ["operator", "technician", "viewer"])
def test_financial_access_and_export_denied(x, role):
    c = client(x, role)
    try:
        for path in [
            "/me",
            "/periods",
            f'/periods/{x.p}/export?scope_id={x.scopes["tower0"]}',
        ]:
            assert c.get("/api/finance/v1" + path).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_unauthenticated_finance_no_data(x):
    app.dependency_overrides[get_db] = lambda: x.db
    try:
        assert TestClient(app).get("/api/finance/v1/periods").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_admin_api_filtering_export_and_idempotent_entry(x):
    c = client(x)
    try:
        body = b(
            EntryInput,
            scope_id=x.scopes["service0"],
            kind="revenue",
            category="subscription",
            basis="contracted",
            amount="50",
            external_key="idempotent",
            evidence="=potential spreadsheet formula",
        ).model_dump(mode="json")
        a = c.post(f"/api/finance/v1/periods/{x.p}/entries", json=body)
        assert a.status_code == 200, a.text
        assert (
            c.post(f"/api/finance/v1/periods/{x.p}/entries", json=body).json()["id"]
            == a.json()["id"]
        )
        r = c.get(f'/api/finance/v1/periods/{x.p}/report?scope_id={x.scopes["tower0"]}')
        assert r.json()["totals"]["revenue"] == "50.00"
        assert r.headers["cache-control"] == "no-store"
        export = c.get(
            f'/api/finance/v1/periods/{x.p}/export?scope_id={x.scopes["tower0"]}'
        )
        assert "'=potential" in export.text
        assert (
            x.db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "field.finance.export")
            )
            == 1
        )
    finally:
        app.dependency_overrides.clear()


def test_transport_circuit_profitability_keeps_internet_separate(x):
    from app.unified.models import Circuit

    c = Circuit(
        code="SYN-TRANSPORT",
        service_id=x.services[0].id,
        purpose="transport",
        bandwidth_mbps=500,
        state="active",
        topology_complete=False,
        completeness_evidence=R,
    )
    x.db.add(c)
    x.db.flush()
    sc = s.create_scope(
        x.db,
        x.u,
        b(
            ScopeInput,
            code="transport",
            name="Synthetic business transport",
            kind="circuit",
            reference_id=c.id,
        ),
    )
    x.scopes["transport"] = sc["id"]
    entry(x, scope="transport", category="transport", amount="600")
    entry(x, scope="transport", category="installation", amount="200")
    entry(x, scope="transport", kind="opex", category="backhaul", amount="150")
    entry(x, scope="service0", amount="100")
    attest(x, scope="transport")
    r = report(x, "transport")
    assert (
        r["contribution"] == "650.00" and r["totals"]["recurring_revenue"] == "600.00"
    )
    assert r["totals"]["installation_revenue"] == "200.00"
    assert r["lines"][0]["dimensions"]["contracted_bandwidth_mbps"] == 500


def test_inventory_purchase_replacement_and_maintenance_import(x):
    from test_fiber_sync import equipment
    from app.equipment.models import Financial, Maintenance, CostEntry

    x.user = x.u
    x.z = x.sites[0]
    asset = equipment(x, "SYN-FINANCIAL")
    asset.purchase_date = date(2026, 1, 4)
    x.db.add(
        Financial(
            asset_id=asset.id,
            currency="USD",
            purchase_cost=D("1200"),
            replacement_estimate=D("1500"),
            salvage_value=D("0"),
            depreciation_method="straight_line_monthly",
            useful_life_months=12,
            updated_by=x.u.id,
        )
    )
    m = Maintenance(
        asset_id=asset.id,
        inspected_at=now(),
        technician_id=x.u.id,
        actor_id=x.u.id,
        status="completed",
        condition="good",
        work_performed=R,
        parts=[],
    )
    x.db.add(m)
    x.db.flush()
    x.db.add(
        CostEntry(
            asset_id=asset.id,
            maintenance_id=m.id,
            kind="maintenance",
            currency="USD",
            amount=D("25"),
            actor_id=x.u.id,
            at=now().replace(year=2026, month=1, day=15),
        )
    )
    x.db.flush()
    sc = s.create_scope(
        x.db,
        x.u,
        b(
            ScopeInput,
            code="asset",
            name="Synthetic installed router",
            kind="asset",
            reference_id=asset.id,
        ),
    )
    x.scopes["asset"] = sc["id"]
    r = s.preview_import(
        x.db, x.u, x.p, b(ImportInput, provider="inventory", asset_ids=[asset.id])
    )
    assert len(r["report"]["items"]) == 4
    s.approve_import(x.db, x.u, r["id"], b(ApprovalInput, digest=r["digest"]))
    actual = report(x, "asset")
    assert (
        actual["totals"]["capex"] == "1200.00" and actual["totals"]["opex"] == "25.00"
    )
    assert actual["totals"]["replacement_estimate"] == "1500.00"
    later = s.create_period(
        x.db,
        x.u,
        b(
            PeriodInput,
            code="dep-feb",
            starts_on=date(2026, 2, 1),
            ends_on=date(2026, 3, 1),
            currency="USD",
        ),
    )
    dep = s.preview_import(
        x.db,
        x.u,
        later["id"],
        b(ImportInput, provider="inventory", asset_ids=[asset.id]),
    )
    s.approve_import(x.db, x.u, dep["id"], b(ApprovalInput, digest=dep["digest"]))
    rr = s.calculate(x.db, later["id"], sc["id"])
    assert rr["totals"]["depreciation"] == "100.00"
    assert rr["totals"]["opex"] == "0.00"


def test_closed_period_adjustment_and_no_partial_import(x):
    e = entry(x, kind="capex", scope="tower0", amount="1000")
    p = x.db.get(Period, x.p)
    p.closed = True
    x.db.flush()
    with pytest.raises(ValueError):
        entry(x)
    later = s.create_period(
        x.db,
        x.u,
        b(
            PeriodInput,
            code="adjust",
            starts_on=date(2026, 2, 1),
            ends_on=date(2026, 3, 1),
            currency="USD",
        ),
    )
    s.reverse(x.db, x.u, e["id"], b(ReversalInput, adjustment_period_id=later["id"]))
    assert (
        s.calculate(x.db, later["id"], x.scopes["tower0"])["totals"]["capex"] == "0.00"
    )


def test_changed_reviewed_mapping_invalidates_import_preview(x):
    r = import_charge(x)
    x.services[0].site_id = x.sites[1].id
    x.services[0].sector_id = x.sectors[1].id
    x.db.flush()
    with pytest.raises(ValueError):
        s.approve_import(x.db, x.u, r["id"], b(ApprovalInput, digest=r["digest"]))
    assert x.db.scalar(select(func.count()).select_from(Entry)) == 0


def test_bad_second_import_record_has_no_partial_financial_entries(x):
    with pytest.raises(ValueError):
        s.preview_import(
            x.db,
            x.u,
            x.p,
            b(
                ImportInput,
                provider="powercode_mock",
                charges=[
                    charge(x),
                    charge(
                        x, external_key="bad-2", service_external_id="not-a-customer"
                    ),
                ],
            ),
        )
    assert x.db.scalar(select(func.count()).select_from(Entry)) == 0


def test_database_rejects_unbalanced_shared_allocation(x):
    e = entry(x, kind="opex", scope="network", amount="100")
    with pytest.raises(DBAPIError):
        with x.db.begin_nested():
            a = Allocation(
                entry_id=e["id"],
                method="manual",
                evidence={"reason": R},
                created_by=x.u.id,
            )
            x.db.add(a)
            x.db.flush()
            x.db.add(
                Share(
                    allocation_id=a.id,
                    scope_id=x.scopes["tower0"],
                    weight=D(1),
                    amount=D(99),
                    dimensions={"site_id": x.sites[0].id},
                )
            )
            x.db.flush()
            x.db.execute(text("SET CONSTRAINTS finance_allocation_complete IMMEDIATE"))
    assert x.db.scalar(select(func.count()).select_from(Allocation)) == 0


def test_dashboard_api_filters_existing_sector_scopes(x):
    entry(x)
    c = client(x)
    try:
        r = c.get(f"/api/finance/v1/periods/{x.p}/dashboard?kind=sector")
        assert r.status_code == 200, r.text
        assert len(r.json()["items"]) == 2
        assert all(row["scope"]["kind"] == "sector" for row in r.json()["items"])
    finally:
        app.dependency_overrides.clear()


def test_recorded_capacity_recommendations_require_fresh_consecutive_evidence(x):
    from datetime import datetime, timezone
    from app.models.entities import Device
    from app.tower_health.models import TowerEvent, TowerSample

    d = Device(
        name="Synthetic utilization source",
        source="synthetic",
        external_id="financial-utilization-device",
        ip_address="192.0.2.71",
        vendor="Synthetic",
        device_type="radio",
        wisp_site_id=x.sites[0].id,
    )
    x.db.add(d)
    x.db.flush()
    for i in range(3):
        at = datetime(2026, 1, 31, 23, 55 + i, tzinfo=timezone.utc)
        eid = "finance-capacity-" + str(i)
        x.db.add(TowerEvent(event_id=eid, body_hash="a" * 64, observed_at=at))
        x.db.flush()
        x.db.add(
            TowerSample(
                event_id=eid,
                device_id=d.id,
                site_id=x.sites[0].id,
                sector_id=x.sectors[0].id,
                scope_key="sector:" + str(x.sectors[0].id),
                source="synthetic",
                observed_at=at,
                data={
                    "metrics": {"collector_ok": True, "utilization_percent": 95},
                    "unavailable": {},
                },
            )
        )
    x.db.flush()
    r = report(x, "sector0")
    assert any("capacity-constrained" in rec["action"] for rec in r["recommendations"])
    assert len(r["capacity_context"]["utilization_samples"]) == 3


def test_missing_capacity_cannot_generate_upgrade_recommendation(x):
    r = report(x, "sector0")
    assert not r["capacity_context"]["utilization_samples"]
    assert not any(
        "capacity-constrained" in rec["action"] for rec in r["recommendations"]
    )
