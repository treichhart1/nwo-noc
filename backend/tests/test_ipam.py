"""Synthetic IPAM allocation, PostgreSQL invariants and recorded RouterOS acceptance."""

import pytest, json
from datetime import timedelta
from types import SimpleNamespace
from ipaddress import ip_network
from sqlalchemy import select, func, text
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
from app.ipam import service as s, discovery
from app.ipam.models import (
    VRF,
    VLAN,
    Interface,
    Prefix,
    Pool,
    Assignment,
    Observation,
    Change,
    ImportBatch,
    ReadBudget,
)
from app.ipam.contracts import (
    VRFInput,
    VLANInput,
    InterfaceInput,
    PrefixInput,
    PoolInput,
    AssignmentInput,
    Suggestion,
    DiscoveryInput,
    ImportInput,
    Approval,
)
from app.models.entities import Device, Site
from app.core.config import settings
from test_notifications import network, api
from test_powercode_customers import imported, inventory
from test_rf_postgres_integration import postgres_db

REASON = "Synthetic operator approved IPAM documentation"


@pytest.fixture
def plan(network):
    x = network
    x.now = s.clock()
    x.vrf = s.create_vrf(
        x.db, VRFInput(name="Synthetic IPAM", reason=REASON), x.admin.id
    )
    x.prefix = s.create_prefix(
        x.db,
        PrefixInput(
            vrf_id=x.vrf.id,
            network="10.99.0.0/24",
            site_id=x.a.id,
            gateway="10.99.0.1",
            reason=REASON,
        ),
        x.admin.id,
    )
    return x


def alloc(x, **kwargs):
    return s.allocate(
        x.db,
        AssignmentInput(
            **(
                {"prefix_id": x.prefix.id, "owner": "Synthetic owner", "reason": REASON}
                | kwargs
            )
        ),
        x.admin.id,
    )


def batch(x, **kwargs):
    return DiscoveryInput(
        **(
            {
                "batch_key": "SYN99-1",
                "vrf_id": x.vrf.id,
                "device_id": x.ap.id,
                "observed_at": x.now,
                "routeros_version": "7.16.2",
                "protocol": "rest7",
                "responses": {
                    "/rest/ip/address": [
                        {
                            "address": "10.99.0.50/24",
                            "interface": "bridge1",
                            "routing-table": "main",
                        }
                    ],
                    "/rest/ip/arp": [
                        {"address": "10.99.0.51", "mac-address": "02:00:00:00:00:01"}
                    ],
                    "/rest/ip/dhcp-server/lease": [
                        {"address": "10.99.0.52", "status": "bound"}
                    ],
                },
                "reason": REASON,
            }
            | kwargs
        )
    )


@pytest.mark.parametrize(
    "cidr,gateway,expected",
    [
        ("10.99.0.0/24", "10.99.0.1", "10.99.0.2"),
        ("192.0.2.0/31", None, "192.0.2.0"),
        ("192.0.2.8/32", None, "192.0.2.8"),
        ("2001:db8::/64", None, "2001:db8::1"),
        ("2001:db8::/127", None, "2001:db8::"),
        ("2001:db8::8/128", None, "2001:db8::8"),
    ],
)
def test_ipv4_ipv6_allocation_rules(cidr, gateway, expected, plan):
    x = plan
    p = (
        x.prefix
        if cidr == "10.99.0.0/24"
        else s.create_prefix(
            x.db,
            PrefixInput(vrf_id=x.vrf.id, network=cidr, gateway=gateway, reason=REASON),
            x.admin.id,
        )
    )
    a = alloc(x, prefix_id=p.id)
    assert str(a.address) == expected and a.state == "reserved"
    assert s.utilization(x.db, p)["used"] == "1"


@pytest.mark.parametrize("network", ["10.99.0.1/24", "2001:db8::1/64", "not-an-ip"])
def test_invalid_prefix_contract(network):
    with pytest.raises(ValidationError):
        PrefixInput(vrf_id=1, network=network, reason=REASON)


@pytest.mark.parametrize(
    "address", ["10.99.0.0", "10.99.0.255", "10.98.0.2", "2001:db8::1"]
)
def test_unusable_addresses_rejected(plan, address):
    with pytest.raises(ValueError):
        alloc(plan, address=address)


def test_duplicate_active_assignment_and_overlapping_vrfs(plan):
    x = plan
    a = alloc(x, address="10.99.0.10", state="assigned")
    with pytest.raises(ValueError):
        alloc(x, address="10.99.0.10")
    other = s.create_vrf(
        x.db, VRFInput(name="Customer isolated VRF", reason=REASON), x.admin.id
    )
    p = s.create_prefix(
        x.db,
        PrefixInput(vrf_id=other.id, network="10.99.0.0/24", reason=REASON),
        x.admin.id,
    )
    assert str(alloc(x, prefix_id=p.id, address="10.99.0.10").address) == str(a.address)


@pytest.mark.parametrize("net", ["10.99.0.0/25", "10.99.0.0/23", "10.99.0.0/24"])
def test_unreviewed_overlap_rejected(plan, net):
    with pytest.raises(ValueError):
        s.create_prefix(
            plan.db,
            PrefixInput(vrf_id=plan.vrf.id, network=net, reason=REASON),
            plan.admin.id,
        )


def test_ipv6_hierarchy_and_gap_planning(plan):
    x = plan
    p = s.create_prefix(
        x.db,
        PrefixInput(
            vrf_id=x.vrf.id, network="2001:db8:99::/48", kind="block", reason=REASON
        ),
        x.admin.id,
    )
    child = s.create_prefix(
        x.db,
        PrefixInput(
            vrf_id=x.vrf.id,
            network="2001:db8:99::/56",
            parent_id=p.id,
            kind="block",
            reason=REASON,
        ),
        x.admin.id,
    )
    leaf = s.create_prefix(
        x.db,
        PrefixInput(
            vrf_id=x.vrf.id,
            network="2001:db8:99::/64",
            parent_id=child.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    suggestions = s.suggest_prefixes(
        x.db, Suggestion(prefix_id=p.id, length=56, count=2)
    )
    assert suggestions["candidates"] == ["2001:db8:99:100::/56", "2001:db8:99:200::/56"]
    assert (
        suggestions["requires_operator_approval"]
        and not suggestions["equipment_changes"]
    )
    with pytest.raises(ValueError):
        s.create_prefix(
            x.db,
            PrefixInput(
                vrf_id=x.vrf.id,
                network="2001:db8:100::/64",
                parent_id=p.id,
                reason=REASON,
            ),
            x.admin.id,
        )


def test_vlan_reuse_sites_and_interface_context(plan):
    x = plan
    v = s.create_vlan(
        x.db,
        VLANInput(vrf_id=x.vrf.id, site_id=x.a.id, vid=99, name="Mgmt", reason=REASON),
        x.admin.id,
    )
    v2 = s.create_vlan(
        x.db,
        VLANInput(
            vrf_id=x.vrf.id,
            site_id=x.b.id,
            vid=99,
            name="Mgmt other tower",
            reason=REASON,
        ),
        x.admin.id,
    )
    assert v.id != v2.id
    with pytest.raises(ValueError):
        s.create_vlan(
            x.db,
            VLANInput(
                vrf_id=x.vrf.id, site_id=x.a.id, vid=99, name="Duplicate", reason=REASON
            ),
            x.admin.id,
        )
    i = s.create_interface(
        x.db,
        InterfaceInput(
            device_id=x.ap.id,
            vrf_id=x.vrf.id,
            external_id="*99",
            name="vlan99",
            kind="vlan",
            vlan_id=v.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    with pytest.raises(ValueError):
        alloc(x, interface_id=i.id, device_id=x.ap.id)


@pytest.mark.parametrize("vid", [0, 4095, True])
def test_vlan_ids(vid):
    with pytest.raises(ValidationError):
        VLANInput(vrf_id=1, site_id=1, vid=vid, name="Bad", reason=REASON)


def test_pool_dhcp_reservation_nat_and_release_history(plan):
    x = plan
    p = s.create_pool(
        x.db,
        PoolInput(
            prefix_id=x.prefix.id,
            name="Synthetic DHCP",
            first="10.99.0.100",
            last="10.99.0.110",
            kind="dhcp",
            reason=REASON,
        ),
        x.admin.id,
    )
    a = alloc(
        x,
        pool_id=p.id,
        kind="dhcp_reservation",
        device_id=x.ap.id,
        dns_name="cpe.example.invalid",
    )
    assert str(a.address) == "10.99.0.100"
    before = a.id
    s.release(x.db, a, REASON, x.admin.id)
    again = alloc(x, pool_id=p.id, address="10.99.0.100")
    assert again.id != before and a.state == "released"
    assert (
        x.db.scalar(
            select(func.count(Change.id)).where(
                Change.entity_id == a.id, Change.entity == "ipam_assignments"
            )
        )
        == 2
    )
    with pytest.raises(ValueError):
        s.create_pool(
            x.db,
            PoolInput(
                prefix_id=x.prefix.id,
                name="Overlap",
                first="10.99.0.105",
                last="10.99.0.120",
                reason=REASON,
            ),
            x.admin.id,
        )


def test_gateway_exhaustion_and_aged_reservation_evidence(plan):
    x = plan
    p = s.create_prefix(
        x.db,
        PrefixInput(
            vrf_id=x.vrf.id,
            network="192.0.2.0/30",
            scope="public",
            gateway="192.0.2.1",
            reason=REASON,
        ),
        x.admin.id,
    )
    a = alloc(x, prefix_id=p.id, address="192.0.2.2")
    a.created_at = x.now - timedelta(days=31)
    x.db.flush()
    with pytest.raises(ValueError):
        alloc(x, prefix_id=p.id)
    assert s.utilization(x.db, p)["available"] == "0"
    assert "aged_reservations" in {r["rule"] for r in s.recommendations(x.db, p)}
    with pytest.raises(ValueError):
        alloc(x, prefix_id=p.id, address="192.0.2.1")


def test_powercode_customer_mapping_is_documented_not_fixed_by_observation(plan):
    x = plan
    from app.customers.models import CustomerService

    service = x.db.scalar(
        select(CustomerService).where(CustomerService.device_id == x.ap.id)
    )
    service.last_synced_at = x.now
    a = alloc(
        x, service_id=service.id, device_id=x.ap.id, kind="customer", state="assigned"
    )
    assert a.service_id == service.id
    service.mapping_state = "unknown"
    x.db.flush()
    with pytest.raises(ValueError):
        alloc(x, service_id=service.id)


@pytest.mark.parametrize(
    "protocol,version,path",
    [
        ("rest7", "7.16.2", "/rest/ip/address"),
        ("api6_recorded", "6.49.17", "/ip/address/print"),
        ("api7_recorded", "7.16.2", "/ip/address/print"),
    ],
)
def test_routeros_read_only_versions_and_source_sanitization(
    plan, protocol, version, path
):
    x = plan
    b = batch(
        x,
        protocol=protocol,
        routeros_version=version,
        responses={
            path: [
                {
                    "address": "10.99.0.50/24",
                    "interface": "bridge",
                    "password": "DO-NOT-RETAIN",
                    "comment": "secret=canary",
                }
            ]
        },
    )
    result = discovery.ingest(x.db, b, x.admin.id)
    assert (
        result["observations"] == 1
        and discovery.ingest(x.db, b, x.admin.id)["duplicate"]
    )
    o = x.db.scalar(select(Observation))
    assert "canary" not in str(o.evidence) and "DO-NOT-RETAIN" not in str(o.evidence)
    assert not x.db.scalar(select(Assignment.id))


@pytest.mark.parametrize(
    "path",
    ["/rest/system/reboot", "/ip/address/add", "/ip/address/set", "/rest/ip/scan"],
)
def test_operation_allowlist(plan, path):
    x = plan
    with pytest.raises(ValueError):
        discovery.ingest(x.db, batch(x, responses={path: []}), x.admin.id)


@pytest.mark.parametrize(
    "payload", [{}, "garbled", [{"address": "invalid"}], [{"address": "10.99.0.1/99"}]]
)
def test_malformed_discovery_is_atomic(plan, payload):
    x = plan
    with pytest.raises(ValueError):
        discovery.ingest(
            x.db, batch(x, responses={"/rest/ip/address": payload}), x.admin.id
        )
    assert not x.db.scalar(select(Observation.id))


def test_dhcp_arp_staleness_ownership_and_conflict_uncertainty(plan):
    x = plan
    discovery.ingest(x.db, batch(x), x.admin.id)
    r = s.reconcile(x.db, x.vrf.id)
    assert {v["classification"] for v in r["items"]} == {
        "observed_unverified"
    } and not r["absent_observations_mean_free"]
    other = Device(
        source="synthetic",
        external_id="noc99-conflict",
        name="Synthetic alternate",
        wisp_site_id=x.a.id,
    )
    x.db.add(other)
    x.db.flush()
    discovery.ingest(
        x.db,
        batch(
            x,
            batch_key="SYN99-other",
            device_id=other.id,
            responses={"/rest/ip/address": [{"address": "10.99.0.50/24"}]},
        ),
        x.admin.id,
    )
    r = s.reconcile(x.db, x.vrf.id)
    assert (
        next(v for v in r["items"] if v["address"] == "10.99.0.50")["classification"]
        == "potential_conflict"
    )
    assert r["confirmed_duplicate_assignments"] == 0
    for o in x.db.scalars(select(Observation)):
        o.observed_at = x.now - timedelta(hours=1)
    x.db.flush()
    assert all(
        v["stale"] and v["classification"] == "observed_unverified"
        for v in s.reconcile(x.db, x.vrf.id)["items"]
    )
    assert int(__import__("ipaddress").ip_address(str(alloc(x).address))) not in {
        int(__import__("ipaddress").ip_address(f"10.99.0.{n}")) for n in [50, 51, 52]
    }


def test_routes_ospf_bridges_and_ipv6_neighbors(plan):
    x = plan
    r = discovery.ingest(
        x.db,
        batch(
            x,
            responses={
                "/rest/ip/route": [
                    {
                        "dst-address": "10.2.0.0/16",
                        "gateway": "10.99.0.254",
                        "routing-table": "main",
                    }
                ],
                "/rest/routing/ospf/neighbor": [
                    {"router-id": "192.0.2.1", "state": "Full", "interface": "ether1"}
                ],
                "/rest/interface/bridge": [{"name": "bridge-main"}],
                "/rest/ipv6/neighbor": [
                    {"address": "2001:db8::99", "interface": "ether1"}
                ],
                "/rest/ip/pool": [
                    {"name": "customers", "ranges": "10.99.0.100-10.99.0.200"}
                ],
                "/rest/ipv6/address": None,
            },
        ),
        x.admin.id,
    )
    assert r["status"] == "partial" and r["unsupported"] == ["ipv6_addresses"]
    assert {o.kind for o in x.db.scalars(select(Observation))} == {
        "route",
        "ospf",
        "bridges",
        "neighbor",
        "pools",
    }


def test_vrf_ambiguous_observations_not_combined(plan):
    x = plan
    with pytest.raises(ValueError):
        discovery.ingest(
            x.db,
            batch(
                x,
                responses={
                    "/rest/ip/address": [{"address": "10.99.0.3/24"}],
                    "/rest/ip/route": [
                        {"dst-address": "10.0.0.0/8", "routing-table": "customer-red"}
                    ],
                },
            ),
            x.admin.id,
        )


def test_csv_preview_approval_revalidation_and_atomic_failure(plan):
    x = plan
    b = s.preview_import(
        x.db,
        ImportInput(
            csv_text=f"type,prefix_id,address,owner\nassignment,{x.prefix.id},10.99.0.10,CSV owner\nassignment,{x.prefix.id},10.99.0.11,CSV owner",
            reason=REASON,
        ),
        x.admin.id,
    )
    assert not b["errors"] and not x.db.scalar(select(Assignment.id))
    alloc(x, address="10.99.0.11")
    with pytest.raises(ValueError):
        with x.db.begin_nested():
            s.apply_import(
                x.db,
                b["id"],
                Approval(approved=True, digest=b["digest"], reason=REASON),
                x.admin.id,
            )
    assert not x.db.scalar(
        select(Assignment.id).where(Assignment.address == "10.99.0.10")
    )
    assert x.db.get(ImportBatch, b["id"]).state == "preview"


@pytest.mark.parametrize(
    "csv_text",
    [
        "type,wrong\nassignment,x",
        "type,prefix_id,address,owner\nassignment,1,invalid,bad",
        "type,type\nprefix,prefix",
        "type,vrf_id,network\nreboot,1,10.0.0.0/8",
    ],
)
def test_csv_validation_failure(plan, csv_text):
    with pytest.raises(ValueError):
        s.preview_import(
            plan.db, ImportInput(csv_text=csv_text, reason=REASON), plan.admin.id
        )


def test_csv_duplicates_errors_and_successful_import(plan):
    x = plan
    prefix = (
        f"type,prefix_id,address,owner\nassignment,{x.prefix.id},10.99.0.12,CSV owner"
    )
    b = s.preview_import(
        x.db,
        ImportInput(csv_text=prefix + "\n" + prefix.splitlines()[1], reason=REASON),
        x.admin.id,
    )
    assert b["errors"] and not x.db.scalar(select(Assignment.id))
    good = s.preview_import(
        x.db, ImportInput(csv_text=prefix, reason=REASON), x.admin.id
    )
    result = s.apply_import(
        x.db,
        good["id"],
        Approval(approved=True, digest=good["digest"], reason=REASON),
        x.admin.id,
    )
    assert result["applied"] == 1 and result["equipment_changes"] == 0
    with pytest.raises(ValueError):
        s.apply_import(
            x.db,
            good["id"],
            Approval(approved=True, digest=good["digest"], reason=REASON),
            x.admin.id,
        )


def test_database_direct_sql_uniqueness_and_context_guards(plan):
    x = plan
    alloc(x, address="10.99.0.10")
    for address, vrf in [
        ("10.99.0.10", x.vrf.id),
        ("10.98.0.1", x.vrf.id),
        ("10.99.0.12/24", x.vrf.id),
    ]:
        with pytest.raises(IntegrityError):
            with x.db.begin_nested():
                x.db.add(
                    Assignment(
                        prefix_id=x.prefix.id,
                        vrf_id=vrf,
                        address=address,
                        owner="Invalid direct SQL",
                        actor_id=x.admin.id,
                    )
                )
                x.db.flush()
    with pytest.raises(IntegrityError):
        with x.db.begin_nested():
            x.db.add(Prefix(vrf_id=x.vrf.id, network="10.99.0.0/25"))
            x.db.flush()


def test_discovery_disabled_by_default(plan, monkeypatch):
    monkeypatch.setattr(settings, "ipam_discovery_enabled", False)
    with pytest.raises(discovery.IntegrationFailure):
        discovery.collect(plan.db, plan.ap.id, plan.vrf.id, plan.admin.id)


def test_api_auth_filters_exports_and_changes(api):
    x, c, h = api
    assert c.get("/api/ipam").status_code == 401
    r = c.post("/api/ipam/vrfs", headers=h, json={"name": "API99", "reason": REASON})
    assert r.status_code == 200, r.text
    v = r.json()["id"]
    r = c.post(
        "/api/ipam/prefixes",
        headers=h,
        json={
            "vrf_id": v,
            "network": "192.0.2.0/24",
            "scope": "public",
            "reason": REASON,
        },
    )
    assert r.status_code == 200, r.text
    p = r.json()["id"]
    r = c.post(
        "/api/ipam/assignments",
        headers=h,
        json={
            "prefix_id": p,
            "owner": "Synthetic UI",
            "address": "192.0.2.10",
            "dns_name": "node.example.invalid",
            "reason": REASON,
        },
    )
    assert r.status_code == 200, r.text
    a = r.json()["id"]
    page = c.get(f"/api/ipam/prefixes?vrf_id={v}", headers=h)
    assert (
        page.status_code == 200
        and page.json()["items"][0]["utilization"]["used"] == "1"
    )
    for path in [
        "",
        f"/prefixes/{p}",
        f"/assignments/{a}/history",
        f"/assignments?vrf_id={v}&q=192.0.2.0%2F24",
        f"/lookup?vrf_id={v}&address=192.0.2.99",
        f"/topology?vrf_id={v}",
        f"/export.csv?vrf_id={v}",
    ]:
        assert c.get("/api/ipam" + path, headers=h).status_code == 200, path
    assert (
        c.get(f"/api/ipam/lookup?vrf_id={v}&address=192.0.2.99", headers=h).json()[
            "classification"
        ]
        == "available"
    )
    assert (
        c.post(
            f"/api/ipam/assignments/{a}/release", headers=h, json={"reason": REASON}
        ).json()["state"]
        == "released"
    )
    x.admin.role = "viewer"
    x.db.commit()
    assert c.get("/api/ipam", headers=h).status_code == 403
    assert (
        c.post(
            "/api/ipam/vrfs", headers=h, json={"name": "Denied", "reason": REASON}
        ).status_code
        == 403
    )


def test_request_rate_limit_is_shared_postgresql_budget(api):
    x, c, h = api
    x.db.add(
        ReadBudget(
            user_id=x.admin.id,
            minute=s.clock().replace(second=0, microsecond=0),
            count=120,
        )
    )
    x.db.commit()
    assert c.get("/api/ipam", headers=h).status_code == 429


@pytest.mark.parametrize(
    "version,protocol",
    [("6.49.17", "rest7"), ("7.16.2", "api6_recorded"), ("6.49.17", "api7_recorded")],
)
def test_routeros_protocols_are_not_interchangeable(version, protocol, plan):
    with pytest.raises(ValidationError):
        batch(plan, routeros_version=version, protocol=protocol)


@pytest.mark.parametrize(
    "dns",
    ["bad name.invalid", "<script>.invalid", "-bad.invalid", "a..invalid", "node"],
)
def test_dns_validation(dns):
    with pytest.raises(ValidationError):
        AssignmentInput(prefix_id=1, owner="Synthetic", dns_name=dns, reason=REASON)


def test_concurrent_reservations_use_real_transactions(postgres_db):
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4
    from sqlalchemy.orm import Session
    from sqlalchemy import delete
    from app.models.entities import User, AuditLog

    engine = postgres_db.bind.engine
    name = "noc99-concurrency-" + uuid4().hex
    actor = vrf = prefix = None
    try:
        with Session(engine) as db:
            u = User(
                username=name,
                email=name + "@example.invalid",
                password_hash="synthetic-unusable-hash",
                role="operator",
                is_active=True,
            )
            db.add(u)
            db.flush()
            actor = u.id
            v = s.create_vrf(db, VRFInput(name=name, reason=REASON), actor)
            vrf = v.id
            p = s.create_prefix(
                db,
                PrefixInput(vrf_id=vrf, network="192.0.2.0/24", reason=REASON),
                actor,
            )
            prefix = p.id
            db.commit()

        def reserve(_):
            with Session(engine) as db:
                try:
                    row = s.allocate(
                        db,
                        AssignmentInput(
                            prefix_id=prefix,
                            address="192.0.2.42",
                            owner="Synthetic concurrency",
                            reason=REASON,
                        ),
                        actor,
                    )
                    db.commit()
                    return "allocated"
                except ValueError:
                    db.rollback()
                    return "conflict"

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(reserve, range(4)))
        assert results.count("allocated") == 1 and results.count("conflict") == 3
    finally:
        if actor:
            with Session(engine) as db:
                db.execute(delete(Change).where(Change.actor_id == actor))
                if prefix:
                    db.execute(delete(Assignment).where(Assignment.prefix_id == prefix))
                    db.execute(delete(Prefix).where(Prefix.id == prefix))
                if vrf:
                    db.execute(delete(VRF).where(VRF.id == vrf))
                db.execute(delete(AuditLog).where(AuditLog.user_id == actor))
                db.execute(delete(User).where(User.id == actor))
                db.commit()


@pytest.mark.parametrize(
    "failure,status",
    [("auth", 401), ("rate", 429), ("timeout", None), ("malformed", 200)],
)
def test_exact_read_transport_failures_remain_read_only(failure, status):
    import httpx
    from app.integrations.read_only import ReadPolicy, ReadClient, IntegrationFailure

    calls = []

    def reply(req):
        calls.append((req.method, req.url.path))
        if failure == "timeout":
            raise httpx.ReadTimeout("Synthetic timeout")
        return httpx.Response(
            status, content=b"not-json" if failure == "malformed" else b"{}"
        )

    p = ReadPolicy(
        "https://router.example.invalid",
        ("https://router.example.invalid",),
        frozenset(["/rest/ip/address"]),
        True,
        True,
        retries=0,
    )
    with ReadClient(p, transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(IntegrationFailure):
            client.request("/rest/ip/address")
        with pytest.raises(IntegrationFailure):
            client.request("/rest/ip/address", method="POST")
    assert calls == [("GET", "/rest/ip/address")]


def test_point_to_point_accepts_registered_other_endpoint(plan):
    from app.models.entities import NetworkLink

    x = plan
    z = Device(
        source="synthetic",
        external_id="noc99-ptp-z",
        name="Synthetic remote endpoint",
        wisp_site_id=x.b.id,
    )
    x.db.add(z)
    x.db.flush()
    link = NetworkLink(
        name="Synthetic IPAM point-to-point",
        a_site_id=x.a.id,
        z_site_id=x.b.id,
        a_device_id=x.ap.id,
        z_device_id=z.id,
    )
    x.db.add(link)
    x.db.flush()
    p = s.create_prefix(
        x.db,
        PrefixInput(
            vrf_id=x.vrf.id,
            network="192.0.2.0/31",
            site_id=x.a.id,
            link_id=link.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    a = alloc(
        x, prefix_id=p.id, address="192.0.2.0", device_id=x.ap.id, kind="point_to_point"
    )
    b = alloc(
        x, prefix_id=p.id, address="192.0.2.1", device_id=z.id, kind="point_to_point"
    )
    assert a.device_id != b.device_id
    assert "point_to_point_documentation" not in {
        r["rule"] for r in s.recommendations(x.db, p)
    }


def test_vlan_interface_discrepancy_is_only_suspected(plan):
    x = plan
    v = s.create_vlan(
        x.db,
        VLANInput(
            vrf_id=x.vrf.id,
            site_id=x.a.id,
            vid=99,
            name="Documented management",
            reason=REASON,
        ),
        x.admin.id,
    )
    p = s.create_prefix(
        x.db,
        PrefixInput(
            vrf_id=x.vrf.id,
            network="10.100.0.0/24",
            site_id=x.a.id,
            vlan_id=v.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    discovery.ingest(
        x.db,
        batch(
            x,
            responses={
                "/rest/ip/address": [
                    {"address": "10.100.0.10/24", "interface": "vlan100"}
                ],
                "/rest/interface/vlan": [{"name": "vlan100", "vlan-id": "100"}],
            },
        ),
        x.admin.id,
    )
    r = s.recommendations(x.db, p)
    assert any(
        v["rule"] == "vlan_interface_documentation_differs"
        and v["confidence"] == "suspected_inconsistency"
        for v in r
    )


def test_static_candidates_skip_documented_and_observed_pools(plan):
    x = plan
    p = s.create_pool(
        x.db,
        PoolInput(
            prefix_id=x.prefix.id,
            name="DHCP protected",
            first="10.99.0.2",
            last="10.99.0.100",
            reason=REASON,
        ),
        x.admin.id,
    )
    with pytest.raises(ValueError):
        alloc(x, address="10.99.0.20")
    assert str(alloc(x).address) == "10.99.0.101"
    discovery.ingest(
        x.db,
        batch(
            x,
            responses={
                "/rest/ip/pool": [
                    {
                        "name": "Observed dynamic pool",
                        "ranges": "10.99.0.102-10.99.0.200",
                    }
                ]
            },
        ),
        x.admin.id,
    )
    assert str(alloc(x).address) == "10.99.0.201"
    assert str(alloc(x, pool_id=p.id).address) == "10.99.0.2"


def test_pool_must_not_include_gateway(plan):
    x = plan
    with pytest.raises(ValueError):
        s.create_pool(
            x.db,
            PoolInput(
                prefix_id=x.prefix.id,
                name="Bad DHCP network",
                first="10.99.0.1",
                last="10.99.0.100",
                reason=REASON,
            ),
            x.admin.id,
        )
