"""Synthetic documented topology only; no scans/device operations/provider calls."""

from uuid import uuid4
from datetime import timedelta
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.exc import IntegrityError, DBAPIError
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import get_db
from app.core.config import settings
from app.models.entities import Device, Site, NetworkLink, AuditLog
from app.customers.models import CustomerService
from app.unified import service as s
from app.unified.models import *
from app.unified.contracts import *
from app.field.models import now
from test_signup import signup, form, application
from test_field import field, auth
from test_equipment import stock
from test_notifications import network
from test_powercode_customers import inventory, imported
from test_rf_postgres_integration import postgres_db

R = "Synthetic operator reviewed unified network documentation"


def b(cls, **kw):
    return cls(operation_key=uuid4(), reason=R, **kw)


def node(x, kind, **kw):
    return s.create_node(x.db, x.admin, b(NodeInput, kind=kind, **kw))["id"]


def edge(x, a, z, technology="wireless", **kw):
    return s.create_edge(
        x.db,
        x.admin,
        b(
            EdgeInput,
            label="Synthetic " + str(uuid4()),
            a_id=a,
            z_id=z,
            layer="physical",
            relation="transport",
            technology=technology,
            state="installed",
            evidence=R,
            **kw
        ),
    )["id"]


def circuit(x, service=None, complete=True):
    return s.circuit(
        x.db,
        x.admin,
        b(
            CircuitInput,
            code=str(uuid4()),
            service_id=service,
            purpose="business" if service else "transport",
            state="active",
            topology_complete=complete,
            completeness_evidence=R,
        ),
    )["id"]


def path(x, c, a, z, edges, verified=False, strands=None):
    return s.create_path(
        x.db,
        x.admin,
        b(
            PathInput,
            circuit_id=c,
            name=str(uuid4()),
            source_id=a,
            destination_id=z,
            state="active",
            hops=[HopInput(edge_id=e, strand_id=(strands or {}).get(e)) for e in edges],
            verified_until=now() + timedelta(minutes=5) if verified else None,
            verification_evidence=R,
        ),
    )["id"]


@pytest.fixture
def unified(signup):
    x = signup
    x.core = node(x, "core", site_id=x.root.id)
    x.tower_node = node(x, "tower", site_id=x.a.id)
    x.pop = node(x, "pop", site_id=x.b.id)
    x.service = x.db.scalar(select(CustomerService))
    x.customer = node(x, "customer", service_id=x.service.id)
    x.wire = edge(x, x.core, x.tower_node, "microwave")
    x.access = edge(x, x.tower_node, x.customer)
    return x


def test_canonical_identity_and_import_idempotency(unified):
    x = unified
    with x.db.begin_nested(), pytest.raises(IntegrityError):
        node(x, "tower", site_id=x.a.id)
    one = s.import_existing(x.db, x.admin, b(Mutation))
    two = s.import_existing(x.db, x.admin, b(Mutation))
    assert two["nodes_created"] == 0 and two["links_created"] == 0
    assert (
        x.db.scalar(
            select(func.count()).select_from(Node).where(Node.site_id == x.a.id)
        )
        == 1
    )
    assert one["network_calls"] == 0


@pytest.mark.parametrize(
    "kind",
    [
        "olt",
        "ont",
        "splitter",
        "cabinet",
        "splice_enclosure",
        "patch_panel",
        "odf",
        "radio",
    ],
)
def test_equipment_kinds_reuse_existing_device(unified, kind):
    x = unified
    d = Device(source="synthetic", external_id=str(uuid4()), name="Synthetic " + kind)
    x.db.add(d)
    x.db.flush()
    id = node(x, kind, device_id=d.id)
    assert s.node_context(x.db, x.admin, id)["canonical"]["device_id"] == d.id
    with pytest.raises(ValueError):
        node(x, kind, device_id=d.id)


@pytest.mark.parametrize(
    "change",
    [
        {"site_id": 1, "asset_id": 2},
        {"kind": "olt", "site_id": 1},
        {"kind": "customer", "site_id": 1},
    ],
)
def test_invalid_identity_contract(change):
    with pytest.raises(ValueError):
        b(NodeInput, **({"kind": "tower"} | change))


def cable(x, a, z, count=4):
    e = edge(x, a, z, "fiber")
    c = s.create_cable(
        x.db,
        x.admin,
        b(
            CableInput,
            code=str(uuid4()),
            edge_id=e,
            strand_count=count,
            geometry=[[-84.21, 41.4], [-84.2, 41.4]],
        ),
    )
    return (
        e,
        c,
        x.db.scalars(
            select(Strand).where(Strand.cable_id == c["id"]).order_by(Strand.number)
        ).all(),
    )


def assign(x, c, rows, action="assign"):
    return s.reserve(
        x.db,
        x.admin,
        b(
            StrandInput,
            strand_ids=[r.id for r in rows],
            expected_revisions={r.id: r.revision for r in rows},
            action=action,
            circuit_id=c,
        ),
    )


def test_cable_strands_pairs_native_duplicates(unified):
    x = unified
    e, c, rows = cable(x, x.core, x.pop)
    assert (
        len(rows) == 4
        and x.db.scalar(
            select(func.count())
            .select_from(End)
            .where(End.strand_id.in_([r.id for r in rows]))
        )
        == 8
    )
    pair = s.create_pair(
        x.db,
        x.admin,
        b(PairInput, code="Synthetic pair", strand_ids=[rows[0].id, rows[1].id]),
    )
    assert pair["cable_id"] == c["id"]
    with x.db.begin_nested(), pytest.raises(IntegrityError):
        x.db.add(Strand(cable_id=c["id"], number=1))
        x.db.flush()
    with x.db.begin_nested(), pytest.raises(IntegrityError):
        x.db.add(Strand(cable_id=c["id"], number=5))
        x.db.flush()


def test_reservation_release_expiry_and_conflict(unified):
    x = unified
    e, c, rows = cable(x, x.core, x.pop)
    cir = circuit(x)
    assign(x, cir, [rows[0]], "reserve")
    assert rows[0].state == "reserved"
    rows[0].reserved_until = now() - timedelta(days=1)
    with pytest.raises(ValueError):
        assign(x, circuit(x), [rows[0]], "reserve")
    assign(x, cir, [rows[0]])
    assert rows[0].state == "assigned"
    s.reserve(
        x.db,
        x.admin,
        b(
            StrandInput,
            strand_ids=[rows[0].id],
            expected_revisions={rows[0].id: rows[0].revision},
            action="release",
        ),
    )
    assert rows[0].state == "available" and rows[0].circuit_id is None


def test_reservation_whole_set_atomic_and_revision(unified):
    x = unified
    e, c, rows = cable(x, x.core, x.pop)
    cir = circuit(x)
    assign(x, cir, [rows[1]])
    with pytest.raises(ValueError):
        assign(x, circuit(x), rows[:2], "reserve")
    assert rows[0].state == "available"
    with pytest.raises(ValueError):
        s.reserve(
            x.db,
            x.admin,
            b(
                StrandInput,
                strand_ids=[rows[0].id],
                expected_revisions={rows[0].id: 99},
                action="reserve",
                circuit_id=cir,
            ),
        )


def test_planned_cable_cannot_assign(unified):
    x = unified
    e, c, rows = cable(x, x.core, x.pop)
    x.db.get(Edge, e).state = "planned"
    with pytest.raises(ValueError):
        assign(x, circuit(x), rows[:1])


@pytest.mark.parametrize("join_kind", ["splice", "patch"])
def test_strand_trace_join_and_disconnect(unified, join_kind):
    x = unified
    e, c, rows = cable(x, x.core, x.pop)
    e2, c2, r2 = cable(x, x.pop, x.customer)
    end1 = x.db.scalar(
        select(End).where(End.strand_id == rows[0].id, End.node_id == x.pop)
    )
    end2 = x.db.scalar(
        select(End).where(End.strand_id == r2[0].id, End.node_id == x.pop)
    )
    j = s.create_join(
        x.db,
        x.admin,
        b(
            JoinInput,
            kind=join_kind,
            node_id=x.pop,
            end_ids=[end1.id, end2.id],
            evidence=R,
        ),
    )
    x.db.flush()
    trace = s.trace_strand(x.db, rows[0].id)
    assert trace["strand_ids"] == sorted([rows[0].id, r2[0].id]) and not trace["cycles"]
    assert "unverified" in trace["optical_continuity"]
    s.state_change(
        x.db, x.admin, b(StateInput, target="join", id=j["id"], active=False)
    )
    x.db.flush()
    assert s.trace_strand(x.db, rows[0].id)["strand_ids"] == [rows[0].id]


def test_splice_wrong_location_rejected(unified):
    x = unified
    e, c, rows = cable(x, x.core, x.pop)
    ends = x.db.scalars(
        select(End).where(
            End.strand_id.in_([r.id for r in rows[:2]]), End.node_id == x.pop
        )
    ).all()
    with pytest.raises(ValueError):
        s.create_join(
            x.db,
            x.admin,
            b(
                JoinInput,
                kind="splice",
                node_id=x.tower_node,
                end_ids=[e.id for e in ends],
                evidence=R,
            ),
        )


def test_hybrid_path_and_active_strand_release_guard(unified):
    x = unified
    e, c, rows = cable(x, x.core, x.tower_node)
    cir = circuit(x, x.service.id)
    assign(x, cir, rows[:1])
    p = path(x, cir, x.core, x.customer, [e, x.access], True, {e: rows[0].id})
    r = s.impact(x.db, [], [x.wire])
    assert x.service.id in r["preserved_services"]
    with pytest.raises(ValueError):
        s.reserve(
            x.db,
            x.admin,
            b(
                StrandInput,
                strand_ids=[rows[0].id],
                expected_revisions={rows[0].id: rows[0].revision},
                action="release",
            ),
        )
    s.state_change(
        x.db, x.admin, b(StateInput, target="path", id=p, state="out_of_service")
    )
    s.reserve(
        x.db,
        x.admin,
        b(
            StrandInput,
            strand_ids=[rows[0].id],
            expected_revisions={rows[0].id: rows[0].revision},
            action="release",
        ),
    )
    assert rows[0].state == "available"


@pytest.mark.parametrize(
    "verified,expected", [(True, "preserved_services"), (False, "estimated_services")]
)
def test_redundancy_requires_functioning_verified_alternate(
    unified, verified, expected
):
    x = unified
    c = circuit(x, x.service.id)
    path(x, c, x.core, x.customer, [x.wire, x.access])
    alt = edge(x, x.core, x.customer, "ethernet")
    path(x, c, x.core, x.customer, [alt], verified)
    result = s.impact(x.db, [], [x.wire])
    assert x.service.id in result[expected]
    assert not result["external_delivery"]
    if verified:
        assert (
            x.service.id not in result["potentially_affected_services"]
            and x.service.id not in result["estimated_services"]
        )


def test_shared_upstream_failure_breaks_both_paths(unified):
    x = unified
    c = circuit(x, x.service.id)
    path(x, c, x.core, x.customer, [x.wire, x.access], True)
    alt = edge(x, x.core, x.customer, "fiber")
    # A second complete path through a different transport but the same source.
    e = edge(x, x.core, x.customer, "ethernet")
    path(x, c, x.core, x.customer, [e], True)
    result = s.impact(x.db, [x.core], [])
    assert (
        x.service.id in result["potentially_affected_services"]
        and result["unique_customer_count"] == 1
    )


def test_missing_topology_and_unverified_paths_stay_uncertain(unified):
    x = unified
    c = circuit(x, x.service.id, False)
    path(x, c, x.core, x.customer, [x.wire, x.access])
    r = s.impact(x.db, [], [x.wire])
    assert x.service.id in r["estimated_services"]


def test_logical_dependency_cycle_is_safe(unified):
    x = unified
    c = circuit(x, x.service.id)
    path(x, c, x.core, x.customer, [x.wire, x.access])
    for a, z in [(x.tower_node, x.pop), (x.pop, x.tower_node)]:
        s.create_edge(
            x.db,
            x.admin,
            b(
                EdgeInput,
                label=str(uuid4()),
                a_id=a,
                z_id=z,
                layer="logical",
                relation="depends_on",
                technology="hybrid",
                state="installed",
                evidence=R,
            ),
        )
    r = s.impact(x.db, [x.pop], [])
    assert x.service.id in r["potentially_affected_services"]


def test_noncontiguous_and_cyclic_path_rejected(unified):
    x = unified
    c = circuit(x)
    with pytest.raises(ValueError):
        path(x, c, x.core, x.customer, [x.access, x.wire])
    with pytest.raises(ValueError):
        b(
            PathInput,
            circuit_id=c,
            name="Synthetic bad path",
            source_id=x.core,
            destination_id=x.customer,
            hops=[HopInput(edge_id=x.wire), HopInput(edge_id=x.wire)],
            verification_evidence=R,
        )


def gpon(x):
    ids = []
    for kind in ["olt", "splitter", "ont"]:
        d = Device(
            source="synthetic", external_id=str(uuid4()), name="Synthetic " + kind
        )
        x.db.add(d)
        x.db.flush()
        ids.append(node(x, kind, device_id=d.id))
    ports = []
    for n, kind in [
        (ids[0], "pon"),
        (ids[1], "splitter_input"),
        (ids[1], "splitter_output"),
        (ids[2], "ont"),
    ]:
        ports.append(
            s.create_port(
                x.db,
                x.admin,
                b(
                    PortInput,
                    node_id=n,
                    label=str(uuid4()),
                    kind=kind,
                    split_ratio=8 if kind == "splitter_input" else None,
                    evidence=R,
                ),
            )["id"]
        )
    return ids, ports


def test_gpon_olt_splitter_ont_and_optical_observations(unified):
    x = unified
    n, p = gpon(x)
    for a, z, pa, pz, rel in [
        (n[0], n[1], p[0], p[1], "pon_splitter"),
        (n[1], n[2], p[2], p[3], "splitter_ont"),
    ]:
        s.create_edge(
            x.db,
            x.admin,
            b(
                EdgeInput,
                label=str(uuid4()),
                a_id=a,
                z_id=z,
                a_port_id=pa,
                z_port_id=pz,
                layer="physical",
                relation=rel,
                technology="gpon",
                state="installed",
                evidence=R,
            ),
        )
    obs = b(
        ObservationInput,
        port_id=p[0],
        observed_at=now() - timedelta(seconds=1),
        source="noc-gpon-fixture-v1",
        metrics=Metrics(
            status="up", rx_dbm=-18.5, observed_capacity_mbps=2000, throughput_mbps=800
        ),
    )
    r = s.observe(x.db, x.admin, obs)
    assert s.observe(x.db, x.admin, obs)["id"] == r["id"]
    h = s.port_health(x.db, [p[0]])[0]
    assert (
        h["observed_utilization_pct"] == 40 and h["observed_metrics"]["rx_dbm"] == -18.5
    )
    assert h["provisioning"] == "documentation_only"


@pytest.mark.parametrize("case", ["missing", "stale"])
def test_missing_and_stale_gpon_metrics_not_zero(unified, case):
    x = unified
    n, p = gpon(x)
    if case == "stale":
        s.observe(
            x.db,
            x.admin,
            b(
                ObservationInput,
                port_id=p[0],
                observed_at=now() - timedelta(hours=1),
                source="noc-gpon-fixture-v1",
                metrics=Metrics(),
            ),
        )
    h = s.port_health(x.db, [p[0]])[0]
    assert h["observed_utilization_pct"] is None and not h["observed_metrics"]


def test_gpon_malformed_and_live_fixture_rejected(unified, monkeypatch):
    x = unified
    n, p = gpon(x)
    with pytest.raises(ValueError):
        Metrics(rx_dbm=float("nan"))
    monkeypatch.setattr(settings, "offline_mode", False)
    with pytest.raises(ValueError):
        s.observe(
            x.db,
            x.admin,
            b(
                ObservationInput,
                port_id=p[0],
                observed_at=now(),
                source="noc-gpon-fixture-v1",
                metrics=Metrics(),
            ),
        )


def test_capacity_configured_vs_missing_observed(unified):
    x = unified
    x.db.get(Edge, x.wire).configured_capacity_mbps = 500
    r = s.capacity(x.db, [x.wire])[0]
    assert (
        r["configured_mbps"] == 500
        and r["observed_mbps"] is None
        and r["utilization_pct"] is None
    )


def test_fiber_qualification_requires_local_documented_route(unified):
    x = unified
    e, c, rows = cable(x, x.core, x.pop)
    x.db.get(Site, x.root.id).latitude = 41.4
    x.db.get(Site, x.root.id).longitude = -84.2
    x.db.flush()
    r = s.qualification(x.db, 41.4, -84.2, "fiber")
    assert r["status"] == "site_survey_required" and r["preliminary"]
    r = s.qualification(x.db, 10, 10, "fiber")
    assert (
        r["status"] == "insufficient_information" and not r["documented_fiber_capacity"]
    )
    assign(x, circuit(x), rows)
    assert (
        s.qualification(x.db, 41.4, -84.2, "fiber")["status"]
        == "capacity_upgrade_required"
    )


def test_graph_filters_no_management_ips_and_ipam_context(unified):
    x = unified
    e, c, rows = cable(x, x.core, x.pop)
    r = s.graph_view(x.db, technology="fiber")
    assert len(r["edges"]) == 1 and r["edges"][0]["id"] == e
    assert not r["external_map_tiles"] and "management_ip" not in str(r)
    ctx = s.node_context(x.db, x.admin, x.tower_node)
    assert ctx["canonical"]["site_id"] == x.a.id and "ipam_prefix_ids" in ctx


def test_existing_work_order_association_and_history(unified):
    x = unified
    a = s.association(
        x.db,
        x.admin,
        b(AssociationInput, node_id=x.tower_node, work_order_id=x.w["id"]),
    )
    assert a["work_order_id"] == x.w["id"]
    assert (
        s.association(
            x.db,
            x.admin,
            b(AssociationInput, node_id=x.tower_node, work_order_id=x.w["id"]),
        )["id"]
        == a["id"]
    )
    with pytest.raises(ValueError):
        s.association(
            x.db, x.admin, b(AssociationInput, node_id=x.pop, work_order_id=x.w["id"])
        )
    assert x.db.scalar(select(func.count()).select_from(Event)) > 0


def test_unauthorized_api_and_public_boundary(unified):
    from app.signup.public_app import app as public

    x = unified
    app.dependency_overrides[get_db] = lambda: x.db
    public.dependency_overrides[get_db] = lambda: x.db
    try:
        with TestClient(app) as client, TestClient(public) as external:
            assert client.get("/api/unified/graph").status_code == 401
            assert (
                client.get("/api/unified/graph", headers=auth(x.tech)).status_code
                == 403
            )
            assert (
                client.get("/api/unified/graph", headers=auth(x.admin)).status_code
                == 200
            )
            assert external.get("/api/unified/graph").status_code == 404
            assert (
                client.post(
                    "/api/unified/nodes",
                    headers=auth(x.other),
                    json=b(NodeInput, kind="equipment", device_id=x.ap.id).model_dump(
                        mode="json"
                    ),
                ).status_code
                == 403
            )
    finally:
        app.dependency_overrides.clear()
        public.dependency_overrides.clear()


def test_retired_splice_invalidates_previously_verified_path(unified):
    x = unified
    e, c, rows = cable(x, x.core, x.pop)
    e2, c2, r2 = cable(x, x.pop, x.customer)
    ci = circuit(x, x.service.id)
    assign(x, ci, rows[:1])
    assign(x, ci, r2[:1])
    ends = [
        x.db.scalar(select(End.id).where(End.strand_id == r.id, End.node_id == x.pop))
        for r in [rows[0], r2[0]]
    ]
    j = s.create_join(
        x.db,
        x.admin,
        b(JoinInput, kind="patch", node_id=x.pop, end_ids=ends, evidence=R),
    )
    x.db.flush()
    path(x, ci, x.core, x.customer, [e, e2], True, {e: rows[0].id, e2: r2[0].id})
    assert x.service.id in s.impact(x.db)["preserved_services"]
    s.state_change(
        x.db, x.admin, b(StateInput, target="join", id=j["id"], active=False)
    )
    x.db.flush()
    assert x.service.id not in s.impact(x.db)["preserved_services"]


def test_duplicate_gpon_input_rejected(unified):
    x = unified
    n, p = gpon(x)
    body = b(
        EdgeInput,
        label=str(uuid4()),
        a_id=n[0],
        z_id=n[1],
        a_port_id=p[0],
        z_port_id=p[1],
        layer="physical",
        relation="pon_splitter",
        technology="gpon",
        state="installed",
        evidence=R,
    )
    s.create_edge(x.db, x.admin, body)
    with pytest.raises(ValueError):
        s.create_edge(x.db, x.admin, body)


def test_conflicting_duplicate_observation_rejected(unified):
    x = unified
    n, p = gpon(x)
    at = now() - timedelta(seconds=1)
    body = b(
        ObservationInput,
        port_id=p[0],
        observed_at=at,
        source="noc-gpon-fixture-v1",
        metrics=Metrics(status="up"),
    )
    s.observe(x.db, x.admin, body)
    body.metrics = Metrics(status="down")
    with pytest.raises(ValueError):
        s.observe(x.db, x.admin, body)


def test_changed_circuit_or_strand_cannot_preserve_service(unified):
    x = unified
    e, c, rows = cable(x, x.core, x.customer)
    ci = circuit(x, x.service.id)
    assign(x, ci, rows[:1])
    path(x, ci, x.core, x.customer, [e], True, {e: rows[0].id})
    rows[0].circuit_id = circuit(x)
    x.db.flush()
    assert x.service.id not in s.impact(x.db)["preserved_services"]


def test_public_role_cannot_access_unified_api(unified):
    from app.signup.public_app import app as public

    with TestClient(public) as client:
        assert client.get("/api/unified/graph").status_code == 404


def test_native_event_and_hop_history_are_append_only(unified):
    x = unified
    with pytest.raises(DBAPIError), x.db.begin_nested():
        x.db.execute(text("UPDATE unified_events SET reason='changed'"))
    ci = circuit(x, x.service.id)
    p = path(x, ci, x.core, x.customer, [x.wire, x.access])
    with pytest.raises(DBAPIError), x.db.begin_nested():
        x.db.execute(text("DELETE FROM unified_path_hops WHERE path_id=:p"), {"p": p})


def test_read_only_operator_cannot_mutate_graph(unified):
    x = unified
    x.other.role = "operator"
    app.dependency_overrides[get_db] = lambda: x.db
    try:
        with TestClient(app) as client:
            assert (
                client.get("/api/unified/graph", headers=auth(x.other)).status_code
                == 200
            )
            assert (
                client.post(
                    "/api/unified/import-existing",
                    headers=auth(x.other),
                    json=b(Mutation).model_dump(mode="json"),
                ).status_code
                == 403
            )
    finally:
        app.dependency_overrides.clear()


def test_work_order_component_context_assignment_guard(unified):
    x = unified
    s.association(
        x.db,
        x.admin,
        b(AssociationInput, node_id=x.tower_node, work_order_id=x.w["id"]),
    )
    x.db.flush()
    app.dependency_overrides[get_db] = lambda: x.db
    try:
        with TestClient(app) as client:
            url = "/api/field/work-orders/" + str(x.w["id"]) + "/unified-context"
            assert (
                client.get(url, headers=auth(x.tech)).json()["items"][0]["node_id"]
                == x.tower_node
            )
            assert client.get(url, headers=auth(x.other)).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_native_rf_analytics_reused_without_polling(unified):
    from app.models.entities import TelemetrySample, RFAnalyticsPoint

    x = unified
    l = NetworkLink(
        name=str(uuid4()),
        a_site_id=x.root.id,
        z_site_id=x.a.id,
        capacity_mbps=500,
        link_type="licensed-microwave",
    )
    x.db.add(l)
    x.db.flush()
    x.db.get(Edge, x.wire).network_link_id = l.id
    t = TelemetrySample(device_id=x.ap.id)
    x.db.add(t)
    x.db.flush()
    p = RFAnalyticsPoint(
        source_sample_id=t.id,
        device_id=x.ap.id,
        link_id=l.id,
        series_key="radio",
        vendor="ubiquiti",
        source_profile="noc-synthetic",
        mapping_digest="0" * 64,
        sampled_at=now() - timedelta(seconds=1),
        valid=True,
        capacity_mbps=200,
        tx_throughput_mbps=170,
        utilization_percent=85,
        signal_dbm=-67,
    )
    x.db.add(p)
    x.db.flush()
    r = s.capacity(x.db, [x.wire])[0]
    assert (
        r["configured_mbps"] == 500
        and r["observed_capacity_mbps"] == 200
        and r["utilization_pct"] == 85
        and r["signal_dbm"] == -67
    )
    assert any(
        r["kind"] == "observed_congestion" for r in s.recommendations(x.db)["items"]
    )


def test_signup_qualification_consumes_unified_foundation(unified):
    from app.signup import service as signup_service
    from app.signup.contracts import QualifyInput

    x = unified
    a = application(x)
    r = signup_service.qualify(
        x.db,
        x.admin,
        a,
        b(
            QualifyInput,
            expected_revision=a.revision,
            latitude=41.4,
            longitude=-84.2,
            coordinate_source=R,
        ),
    )
    assert r["snapshot"]["unified_network"]["preliminary"] and a.state == "submitted"


@pytest.mark.parametrize("count", [1, 12, 288, 1728])
def test_cable_strand_scale_bounds(unified, count):
    x = unified
    e, c, rows = cable(x, x.core, x.pop, count)
    assert (
        len(rows) == count
        and s.capacity(x.db, [e])[0]["strand_capacity"]["available"] == count
    )


def test_circuit_can_be_reviewed_after_path_documentation(unified):
    x = unified
    c = circuit(x, x.service.id, False)
    row = x.db.get(Circuit, c)
    with pytest.raises(ValueError):
        s.review_circuit(
            x.db,
            x.admin,
            c,
            b(
                CircuitReview,
                expected_revision=row.revision,
                topology_complete=True,
                completeness_evidence=R,
            ),
        )
    path(x, c, x.core, x.customer, [x.wire, x.access])
    s.review_circuit(
        x.db,
        x.admin,
        c,
        b(
            CircuitReview,
            expected_revision=row.revision,
            topology_complete=True,
            completeness_evidence=R,
        ),
    )
    assert row.topology_complete


def test_incident_relationship_requires_actual_shared_identity(unified):
    from app.models.entities import Incident

    x = unified
    i = Incident(
        title="Synthetic unrelated incident", site_id=x.b.id, severity="warning"
    )
    x.db.add(i)
    x.db.flush()
    with pytest.raises(ValueError):
        s.association(
            x.db, x.admin, b(AssociationInput, node_id=x.tower_node, incident_id=i.id)
        )
    r = s.association(
        x.db, x.admin, b(AssociationInput, node_id=x.pop, incident_id=i.id)
    )
    assert r["incident_id"] == i.id


def test_document_reference_uses_existing_site_and_visibility(unified):
    from app.maintenance_ops.models import Category, Document

    x = unified
    cat = Category(code="noc104-document", name="Synthetic optical tests")
    x.db.add(cat)
    x.db.flush()
    d = Document(
        site_id=x.a.id,
        category_id=cat.id,
        title="Synthetic private optical report",
        visibility="admin",
        created_by=x.admin.id,
    )
    x.db.add(d)
    x.db.flush()
    r = s.association(
        x.db, x.admin, b(AssociationInput, node_id=x.tower_node, document_id=d.id)
    )
    x.db.flush()
    assert any(
        v["id"] == r["id"]
        for v in s.node_context(x.db, x.admin, x.tower_node)["associations"]
    )
    x.other.role = "operator"
    assert not s.node_context(x.db, x.other, x.tower_node)["associations"]
    with pytest.raises(ValueError):
        s.association(
            x.db, x.admin, b(AssociationInput, node_id=x.pop, document_id=d.id)
        )


def test_strand_trace_detects_cycle_without_inventing_optical_evidence(unified):
    x = unified
    e, c, rows = cable(x, x.core, x.pop)
    f, d, other = cable(x, x.pop, x.core)
    for n in [x.core, x.pop]:
        ends = [
            x.db.scalar(select(End.id).where(End.strand_id == v.id, End.node_id == n))
            for v in [rows[0], other[0]]
        ]
        s.create_join(
            x.db,
            x.admin,
            b(JoinInput, node_id=n, kind="splice", end_ids=ends, evidence=R),
        )
        x.db.flush()
    trace = s.trace_strand(x.db, rows[0].id)
    assert trace["cycles"] and "unverified" in trace["optical_continuity"]


def test_fresh_gpon_down_contradicts_functioning_path_attestation(unified):
    x = unified
    n, p = gpon(x)
    ci = circuit(x)
    e = edge(x, x.core, n[0], "ethernet")
    g = s.create_edge(
        x.db,
        x.admin,
        b(
            EdgeInput,
            label=str(uuid4()),
            a_id=n[0],
            z_id=n[1],
            a_port_id=p[0],
            z_port_id=p[1],
            layer="physical",
            relation="pon_splitter",
            technology="gpon",
            state="installed",
            evidence=R,
        ),
    )["id"]
    path(x, ci, x.core, n[1], [e, g], True)
    s.observe(
        x.db,
        x.admin,
        b(
            ObservationInput,
            port_id=p[0],
            observed_at=now() - timedelta(seconds=1),
            source="noc-gpon-fixture-v1",
            metrics=Metrics(status="down"),
        ),
    )
    x.db.flush()
    result = s.impact(x.db)
    assert not result["circuits"][0]["paths"][0]["functioning_alternate_verified"]


def test_native_reservation_uniqueness_rolls_back_failed_batch(unified):
    x = unified
    e, c, rows = cable(x, x.core, x.pop)
    ci = circuit(x)
    before = rows[0].revision
    with pytest.raises(RuntimeError), x.db.begin_nested():
        assign(x, ci, rows[:2], "reserve")
        x.db.flush()
        raise RuntimeError("Synthetic transaction interruption")
    x.db.expire_all()
    assert rows[0].state == "available" and rows[0].revision == before


def test_concurrent_reservations_have_one_winner(unified):
    import os, subprocess, sys, concurrent.futures
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    x = unified
    source = make_url(os.environ["RF_INTEGRATION_TEST_DATABASE_URL"])
    if source.database != "nwo_noc" or source.host not in {
        "localhost",
        "127.0.0.1",
        "db",
    }:
        pytest.fail("Disposable integration database required")
    name = "noc104_reserve_" + uuid4().hex
    admin = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
    engine = None
    created = False
    try:
        with admin.connect() as c:
            c.execute(text("CREATE DATABASE " + name))
            created = True
        url = source.set(database=name)
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env={
                **os.environ,
                "DATABASE_URL": url.render_as_string(hide_password=False),
            },
            check=True,
            capture_output=True,
            timeout=60,
        )
        engine = create_engine(url)
        from app.models.entities import User

        with Session(engine) as db:
            u = User(
                username="synthetic-reserver",
                email="reserver@example.invalid",
                password_hash="synthetic-unusable",
                role="admin",
                is_active=True,
            )
            a = Site(name="Synthetic A")
            z = Site(name="Synthetic Z")
            db.add_all([u, a, z])
            db.flush()
            na = Node(kind="core", site_id=a.id)
            nz = Node(kind="pop", site_id=z.id)
            db.add_all([na, nz])
            db.flush()
            e = Edge(
                label="Synthetic fiber",
                a_id=na.id,
                z_id=nz.id,
                layer="physical",
                relation="transport",
                technology="fiber",
                state="installed",
                evidence=R,
            )
            db.add(e)
            db.flush()
            cab = Cable(code="SYN", edge_id=e.id, strand_count=1)
            db.add(cab)
            db.flush()
            st = Strand(cable_id=cab.id, number=1)
            db.add(st)
            ci = [
                Circuit(
                    code="SYN" + str(i), purpose="transport", completeness_evidence=R
                )
                for i in range(2)
            ]
            db.add_all(ci)
            db.commit()
            uid = u.id
            sid = st.id
            cids = [c.id for c in ci]

        def reserve_one(cid):
            with Session(engine) as db:
                try:
                    s.reserve(
                        db,
                        db.get(User, uid),
                        b(
                            StrandInput,
                            strand_ids=[sid],
                            expected_revisions={sid: 1},
                            action="reserve",
                            circuit_id=cid,
                        ),
                    )
                    db.commit()
                    return "reserved"
                except ValueError:
                    db.rollback()
                    return "conflict"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve_one, cids))
        assert sorted(results) == ["conflict", "reserved"]
        with Session(engine) as db:
            assert db.get(Strand, sid).revision == 2
    finally:
        if engine:
            engine.dispose()
        if created:
            with admin.connect() as c:
                c.execute(text("DROP DATABASE " + name))
        admin.dispose()


@pytest.mark.parametrize(
    "status,expected",
    [("active", "potentially_serviceable"), ("planned", "construction_required")],
)
def test_documented_coverage_qualifies_preliminarily(unified, status, expected):
    from app.signup.models import Area

    x = unified
    x.db.add(
        Area(
            name="Synthetic documented area",
            technology="fiber",
            status=status,
            polygon=[[-85, 41], [-84, 41], [-84, 42], [-85, 42], [-85, 41]],
            evidence=R,
            updated_by=x.admin.id,
        )
    )
    x.db.flush()
    assert s.qualification(x.db, 41.4, -84.2, "fiber")["status"] == expected


def test_outside_service_boundary_is_not_currently_serviceable(unified):
    from app.signup.models import Area

    x = unified
    x.db.add(
        Area(
            name="Synthetic service boundary",
            technology="service_boundary",
            status="active",
            polygon=[[-85, 41], [-84, 41], [-84, 42], [-85, 42], [-85, 41]],
            evidence=R,
            updated_by=x.admin.id,
        )
    )
    x.db.flush()
    assert (
        s.qualification(x.db, 10, 10, "wireless")["status"]
        == "not_currently_serviceable"
    )


def test_existing_outage_incident_preserves_verified_alternate(unified):
    x = unified
    ci = circuit(x, x.service.id)
    path(x, ci, x.core, x.customer, [x.wire, x.access])
    backup = edge(x, x.core, x.customer, "microwave")
    path(x, ci, x.core, x.customer, [backup], True)
    r = s.incident_impact(x.db, x.admin, x.incident.id)
    assert (
        r["source_incident_id"] == x.incident.id
        and x.service.id in r["preserved_services"]
        and x.service.id not in r["potentially_affected_services"]
    )
    assert (
        not r["customer_ids_for_approval"]
        and r["scope_review_required"]
        and not r["external_delivery"]
    )


def test_resolved_incident_does_not_generate_current_notification_candidates(unified):
    x = unified
    x.incident.status = "resolved"
    with pytest.raises(ValueError):
        s.incident_impact(x.db, x.admin, x.incident.id)


def test_path_verification_requires_recheck_after_connection_state_change(unified):
    x = unified
    c = circuit(x, x.service.id)
    path(x, c, x.core, x.customer, [x.wire, x.access], True)
    assert x.service.id in s.impact(x.db)["preserved_services"]
    e = x.db.get(Edge, x.wire)
    s.state_change(
        x.db,
        x.admin,
        b(
            StateInput,
            target="edge",
            id=e.id,
            expected_revision=e.revision,
            state="out_of_service",
        ),
    )
    s.state_change(
        x.db,
        x.admin,
        b(
            StateInput,
            target="edge",
            id=e.id,
            expected_revision=e.revision,
            state="installed",
        ),
    )
    x.db.flush()
    assert x.service.id not in s.impact(x.db)["preserved_services"]


def test_path_verification_invalidated_by_canonical_customer_mapping_change(unified):
    x = unified
    c = circuit(x, x.service.id)
    path(x, c, x.core, x.customer, [x.wire, x.access], True)
    x.service.site_id = x.b.id
    x.db.flush()
    assert x.service.id not in s.impact(x.db)["preserved_services"]


def test_recorded_device_recovery_does_not_reuse_old_end_to_end_proof(unified):
    x = unified
    d = Device(
        source="synthetic",
        external_id=str(uuid4()),
        name="Synthetic monitored router",
        wisp_site_id=x.a.id,
    )
    x.db.add(d)
    x.db.flush()
    n = node(x, "equipment", device_id=d.id)
    e = edge(x, x.core, n, "ethernet")
    f = edge(x, n, x.customer, "wireless")
    ci = circuit(x, x.service.id)
    p = path(x, ci, x.core, x.customer, [e, f], True)
    d.monitor_status = "up"
    d.monitor_last_checked_at = now()
    d.monitor_last_down_at = x.db.get(Path, p).verified_at + timedelta(microseconds=1)
    x.db.flush()
    assert x.service.id not in s.impact(x.db)["preserved_services"]


def test_verification_expiration_cannot_be_in_past():
    with pytest.raises(ValueError):
        b(
            PathInput,
            circuit_id=1,
            name="Invalid historical proof",
            source_id=1,
            destination_id=2,
            hops=[HopInput(edge_id=1)],
            verified_until=now() - timedelta(seconds=1),
            verification_evidence=R,
        )


def test_common_backhaul_recommendation_cites_all_documented_paths(unified):
    x = unified
    ci = circuit(x, x.service.id)
    p = path(x, ci, x.core, x.customer, [x.wire, x.access])
    extra = edge(x, x.tower_node, x.pop)
    last = edge(x, x.pop, x.customer)
    second = path(x, ci, x.core, x.customer, [x.wire, extra, last])
    r = [
        r
        for r in s.recommendations(x.db)["items"]
        if r["kind"] == "recorded_shared_dependency" and r["circuit_id"] == ci
    ][0]
    assert (
        set(r["evidence"]["documented_path_ids"]) == {p, second}
        and x.wire in r["evidence"]["common_edge_ids"]
        and x.tower_node in r["evidence"]["common_node_ids"]
    )


def test_patch_port_mapping_is_unique_and_invalidates_old_verification(unified):
    x = unified
    e, c, rows = cable(x, x.core, x.customer)
    end = x.db.scalar(
        select(End).where(End.strand_id == rows[0].id, End.node_id == x.core)
    )
    port = s.create_port(
        x.db,
        x.admin,
        b(
            PortInput,
            node_id=x.core,
            label="Synthetic patch1",
            kind="patch",
            evidence=R,
        ),
    )
    s.map_end(
        x.db,
        x.admin,
        b(
            EndMapping,
            end_id=end.id,
            port_id=port["id"],
            expected_strand_revision=rows[0].revision,
        ),
    )
    ci = circuit(x, x.service.id)
    assign(x, ci, rows[:1])
    path(x, ci, x.core, x.customer, [e], True, {e: rows[0].id})
    assert x.service.id in s.impact(x.db)["preserved_services"]
    other = x.db.scalar(
        select(End).where(End.strand_id == rows[1].id, End.node_id == x.core)
    )
    with pytest.raises(ValueError):
        s.map_end(
            x.db,
            x.admin,
            b(
                EndMapping,
                end_id=other.id,
                port_id=port["id"],
                expected_strand_revision=rows[1].revision,
            ),
        )
    s.map_end(
        x.db,
        x.admin,
        b(
            EndMapping,
            end_id=end.id,
            port_id=None,
            expected_strand_revision=rows[0].revision,
        ),
    )
    assert x.service.id not in s.impact(x.db)["preserved_services"]
