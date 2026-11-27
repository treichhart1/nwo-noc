"""NOC110: real PostgreSQL cross-module workflows, synthetic inputs/mock delivery only."""

import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, func
from pydantic import ValidationError
from app.models.entities import Incident, MonitorCheck, Device, AuditLog
from app.customers.models import CustomerAccount
from app.field.models import WorkOrder, QRLabel
from app.notifications.models import NotificationDelivery
from app.notifications.providers import MockSender
from app.notifications.worker import run_batch
from app.customer_status import service as comm
from app.customer_status.models import Publication, Message
from app.customer_status.contracts import (
    SourceInput,
    MessageInput,
    MessageDecision,
    Mutation,
)
from app.down_detector import engine as detector
from app.down_detector.models import OutageEpisode
from app.down_detector.contracts import Observation as DetectorObservation
from app.tower_health import service as tower
from app.tower_health.contracts import Observation, Metrics, Power
from app.unified import service as topology, models as um
from app.field import service as field_service
from app.field.contracts import LabelInput, WorkInput as FieldWorkInput
from app.ipam import service as ipam
from app.ipam.contracts import VRFInput, PrefixInput, AssignmentInput
from app.ipam.models import Assignment

# Canonical fixtures are reused, not parallel inventories or substitute services.
from test_rf_postgres_integration import postgres_db
from test_powercode_customers import inventory, imported
from test_notifications import network
from test_equipment import stock
from test_field import field
from test_signup import signup
from test_unified import unified
from test_customer_network_status import x as communications
from test_fiber_sync import x as fiber
from test_grafana_analytics import x as analytics
from test_finance import x as finances
import test_customer_network_status as ct
import test_field as ft
import test_signup as st
import test_unified as ut
import test_fiber_sync as fs
import test_grafana_analytics as ga
import test_finance as fin

R = "Synthetic NOC110 integration acceptance; documentation only and no external delivery"


@pytest.fixture(autouse=True)
def prohibit_external_http(monkeypatch):
    import httpx, urllib.request

    def denied(*args, **kwargs):
        pytest.fail("Cross-module acceptance must not contact external services")

    original_send = httpx.Client.send

    def mock_only(client, *args, **kwargs):
        if not isinstance(client._transport, httpx.MockTransport):
            denied()
        return original_send(client, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", mock_only)
    monkeypatch.setattr(urllib.request, "urlopen", denied)


def body(cls, **kw):
    return cls(operation_key=uuid4(), reason=R, **kw)


def approve_message(x, result):
    return comm.decide_message(
        x.db,
        x.admin,
        x.db.get(Message, result["id"]),
        body(MessageDecision, action="approve", fingerprint=result["fingerprint"]),
    )


@pytest.mark.parametrize("failure_kind", ["power", "backhaul", "sector"])
def test_a_power_evidence_to_outage_customers_mock_sms_and_verified_restoration(
    communications,
    failure_kind,
):
    x = communications
    start = comm.clock() - timedelta(minutes=12)
    kind, ident = {
        "power": ("site", x.a.id),
        "backhaul": ("link", x.up.id),
        "sector": ("sector", x.sector.id),
    }[failure_kind]
    for seconds in [0, 30, 60]:
        at = start + timedelta(seconds=seconds)
        if failure_kind != "power":
            detector.ingest(
                x.db,
                DetectorObservation(
                    event_id=f"NOC114-{failure_kind}-{seconds}",
                    kind=kind,
                    target_id=ident,
                    source="synthetic",
                    observed_at=at,
                    signals=(
                        {"microwave": False, "interface": False, "collector": True}
                        if kind == "link"
                        else {"icmp": False, "snmp": False, "collector": True}
                    ),
                ),
                now=at,
            )
            continue
        tower.ingest(
            x.db,
            Observation(
                event_id="NOC110-power-" + str(seconds),
                site_id=x.a.id,
                device_id=x.ap.id,
                source="synthetic",
                observed_at=at,
                metrics=Metrics(collector_ok=True),
                power=Power(ac_present=False, ups_state="fault"),
            ),
            now=at,
        )
    detector.reconcile(x.db, now=start + timedelta(seconds=60))
    episode = x.db.scalar(
        select(OutageEpisode).where(OutageEpisode.key == f"{kind}:{ident}")
    )
    assert (
        episode
        and episode.classification
        == {
            "power": "confirmed_commercial_power_outage",
            "backhaul": "backhaul_outage",
            "sector": "sector_outage",
        }[failure_kind]
    )
    x.incident = x.db.get(Incident, episode.incident_id)
    x.p = x.db.get(
        Publication,
        comm.create(x.db, x.admin, body(SourceInput, incident_id=x.incident.id))["id"],
    )
    message = comm.prepare_message(
        x.db, x.admin, x.p, body(MessageInput, impact_confirmed=True)
    )
    sender = MockSender()
    run_batch(x.db, sms=sender)
    assert not sender.calls  # A detected incident never approves a campaign.
    approve_message(x, message)
    run_batch(x.db, sms=sender)
    if failure_kind == "backhaul":
        assert not sender.calls
        preview = comm.impact_preview(x.db, x.p)
        assert preview["eligible_count"] == 0
        assert preview["classification_counts"].get("unknown")
    else:
        assert sender.calls and set(
            x.db.scalars(
                select(NotificationDelivery.state).where(
                    NotificationDelivery.channel == "sms"
                )
            )
        ) == {"simulated"}
    delivered = len(sender.calls)
    run_batch(x.db, sms=sender)
    assert len(sender.calls) == delivered
    for seconds in [180, 210, 240]:
        at = start + timedelta(seconds=seconds)
        if failure_kind != "power":
            detector.ingest(
                x.db,
                DetectorObservation(
                    event_id=f"NOC114-restored-{failure_kind}-{seconds}",
                    kind=kind,
                    target_id=ident,
                    source="synthetic",
                    observed_at=at,
                    signals=(
                        {"microwave": True, "interface": True, "collector": True}
                        if kind == "link"
                        else {"icmp": True, "snmp": True, "collector": True}
                    ),
                ),
                now=at,
            )
            continue
        tower.ingest(
            x.db,
            Observation(
                event_id="NOC110-restored-" + str(seconds),
                site_id=x.a.id,
                device_id=x.ap.id,
                source="synthetic",
                observed_at=at,
                metrics=Metrics(collector_ok=True),
                power=Power(ac_present=True, ups_state="online"),
            ),
            now=at,
        )
    detector.reconcile(x.db, now=start + timedelta(seconds=240))
    assert x.incident.status == "resolved"
    for ago in [301, 201, 101, 1]:
        x.db.add(
            MonitorCheck(
                device_id=x.ap.id,
                status="up",
                checked_at=comm.clock() - timedelta(seconds=ago),
            )
        )
    x.db.flush()
    proof = comm.assess(x.db, x.admin, x.p, body(Mutation))
    if failure_kind == "backhaul":
        # This fixture has no documented monitored link endpoints. Never fabricate recovery.
        assert not proof["verified"]
        assert "no_documented_monitored_devices" in proof["evidence"]["reasons"]
        assert not sender.calls
        assert x.p.published_revision_id is None
        return
    assert proof["verified"]
    restored = comm.prepare_message(
        x.db,
        x.admin,
        x.p,
        body(
            MessageInput,
            kind="restoration",
            event_type="restoration",
            recovery_id=proof["id"],
            impact_confirmed=True,
        ),
    )
    before = len(sender.calls)
    run_batch(x.db, sms=sender)
    assert len(sender.calls) == before
    approve_message(x, restored)
    run_batch(x.db, sms=sender)
    assert len(sender.calls) > before
    final_deliveries = len(sender.calls)
    run_batch(x.db, sms=sender)
    assert len(sender.calls) == final_deliveries
    assert (
        x.db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.user_id == x.admin.id,
                AuditLog.action.like("customer_status.%"),
            )
        )
        > 0
    )
    # Public publication is independently disabled; no public incident is published here.
    assert x.p.published_revision_id is None


def test_b_microwave_failure_redundancy_incident_and_field_history(unified):
    x = unified
    c = ut.circuit(x, x.service.id)
    ut.path(x, c, x.core, x.customer, [x.wire, x.access])
    alternate, cable, strands = ut.cable(x, x.core, x.customer)
    ut.assign(x, c, [strands[0]])
    ut.path(
        x,
        c,
        x.core,
        x.customer,
        [alternate],
        verified=True,
        strands={alternate: strands[0].id},
    )
    result = topology.impact(x.db, failed_edges=[x.wire])
    assert x.service.id in result["preserved_services"]
    assert x.service.id not in result["estimated_services"]
    from app.down_detector.contracts import Observation as DownObservation

    at = comm.clock()
    for ago in [120, 90, 60]:
        detector.ingest(
            x.db,
            DownObservation(
                event_id="NOC110-backhaul-" + str(ago),
                kind="link",
                target_id=x.up.id,
                source="synthetic",
                observed_at=at - timedelta(seconds=ago),
                signals={"microwave": False, "interface": False, "collector": True},
            ),
            now=at,
        )
    detector.reconcile(x.db, now=at)
    e = x.db.scalar(select(OutageEpisode).where(OutageEpisode.key == f"link:{x.up.id}"))
    assert e and e.incident_id
    work = field_service.create_work(
        x.db,
        x.admin,
        body(
            FieldWorkInput,
            site_id=x.a.id,
            incident_id=e.incident_id,
            description=R,
            technician_id=x.tech.id,
        ),
    )
    assert x.db.get(WorkOrder, work["id"]).incident_id == e.incident_id
    assert (
        field_service.work_detail(x.db, x.tech, x.db.get(WorkOrder, work["id"]))[
            "incident"
        ]["id"]
        == e.incident_id
    )


def test_c_qr_replacement_preserves_device_ipam_topology_and_incident(field):
    x = field
    replacement = ft.replacement_fixture(x)
    v = ipam.create_vrf(x.db, VRFInput(name="NOC110 management", reason=R), x.admin.id)
    p = ipam.create_prefix(
        x.db,
        PrefixInput(
            vrf_id=v.id,
            network="192.0.2.0/24",
            scope="documentation",
            site_id=x.a.id,
            reason=R,
        ),
        x.admin.id,
    )
    a = ipam.allocate(
        x.db,
        AssignmentInput(
            prefix_id=p.id,
            address="192.0.2.110",
            state="assigned",
            owner=R,
            device_id=x.ap.id,
            reason=R,
        ),
        x.admin.id,
    )
    n = ut.node(x, "radio", device_id=x.ap.id)
    label = field_service.label(x.db, x.admin, body(LabelInput, asset_id=x.old.id))
    assert (
        field_service.resolve(x.db, x.tech, x.db.get(QRLabel, label["id"]).identifier)[
            "asset_id"
        ]
        == x.old.id
    )
    before = x.db.scalar(select(func.count()).select_from(Device))
    result = field_service.replacement(x.db, x.tech, ft.row(x), replacement)
    assert result["inventory"]["ipam_changes"] == 0
    assert x.db.get(Assignment, a.id).device_id == x.ap.id
    assert x.db.get(um.Node, n).device_id == x.ap.id
    assert x.spare.device_id == x.ap.id and x.old.device_id is None
    assert x.db.scalar(select(func.count()).select_from(Device)) == before
    detail = field_service.work_detail(x.db, x.tech, ft.row(x))
    assert detail["incident"]["id"] == x.incident.id
    assert any(e["kind"] == "equipment_replaced" for e in detail["events"])


def test_d_signup_qualification_operator_installation_reuses_private_workflow(signup):
    x = signup
    accounts = x.db.scalar(select(func.count()).select_from(CustomerAccount))
    a = st.application(x)
    q = st.s.qualify(
        x.db, x.admin, a, body(st.QualifyInput, expected_revision=a.revision)
    )
    assert (
        q["snapshot"]["preliminary"]
        and q["snapshot"]["status"] == "requires_site_survey"
    )
    for state in [
        "under_review",
        "qualification_pending",
        "qualified",
        "quote_pending",
    ]:
        st.s.update(
            x.db,
            x.admin,
            a,
            body(
                st.Update,
                expected_revision=a.revision,
                state=state,
                proposed_site_id=x.a.id,
                approved_qualification=state == "qualified",
                evidence=R,
            ),
        )
    quote = st.quote(x, a)
    for action in ["approve_quote", "issue_quote", "record_acceptance"]:
        st.s.control(
            x.db,
            x.admin,
            a,
            body(
                st.Control,
                expected_revision=a.revision,
                action=action,
                quote_id=quote["id"],
                evidence=R,
            ),
        )
    st.s.update(
        x.db,
        x.admin,
        a,
        body(
            st.Update,
            expected_revision=a.revision,
            state="installation_pending",
            evidence=R,
        ),
    )
    b = body(
        st.WorkInput,
        expected_revision=a.revision,
        kind="installation",
        site_id=x.a.id,
        technician_id=x.tech.id,
        equipment=["Synthetic approved kit"],
        scheduled_at=comm.clock() + timedelta(days=1),
    )
    w = st.s.make_work(x.db, x.admin, a, b)
    b.expected_revision = a.revision
    assert st.s.make_work(x.db, x.admin, a, b)["work_order_id"] == w["work_order_id"]
    assert x.db.get(WorkOrder, w["work_order_id"]).site_id == x.a.id
    assert x.db.scalar(select(func.count()).select_from(CustomerAccount)) == accounts
    assert (
        "Synthetic Applicant" not in x.db.get(WorkOrder, w["work_order_id"]).description
    )


def test_e_approved_fiber_provenance_operational_customer_path_and_draft_rejection(
    fiber,
):
    x = fiber
    bad = fs.rec(
        "node", "draft", role="tower", label="Draft design", publication="draft"
    )
    with pytest.raises(ValidationError):
        fs.envelope(x, [bad])
    fs.apply(x, fs.envelope(x, fs.connected_customer(x)))
    feed = fs.binding(x, "connection", "feed")
    result = topology.impact(x.db, failed_edges=[feed.edge_id])
    assert x.service.id in result["estimated_services"]
    provenance = fs.s.provenance(
        x.db, x.user, node_id=fs.binding(x, "node", "tower").node_id
    )
    assert provenance
    assert fs.binding(x, "node", "tower").external_id == "tower"
    assert x.db.get(um.Node, fs.binding(x, "node", "tower").node_id).site_id == x.z.id
    # Designer revisions remain external provenance; no local engineering editor is invoked.
    assert x.source.applied_cursor == 1


def test_f_recorded_metrics_grafana_history_missing_and_stale_states(
    analytics, monkeypatch
):
    x = analytics
    ga.seed(x)
    assert (
        ga.s.series(x.db, ga.query(x, metric="cpu_percent"))["points"][0]["value"] == 42
    )
    assert (
        ga.s.series(x.db, ga.query(x, metric="rssi_dbm"))["points"][0]["value"] == -65
    )
    assert ga.s.series(x.db, ga.query(x, metric="battery_voltage"))["points"] == []
    health = ga.grafana.check(x.db, x.u, ga.b(ga.HealthInput, mode="mock"))
    assert health["status"] == "ok" and not ga.s.health(x.db)["grafana_live_verified"]
    ga.seed(x, at=ga.s.now() - timedelta(hours=2), cpu=None)
    assert "PRIVATE" not in ga.s.prometheus(x.db)
    assert ga.s.series(x.db, ga.query(x, metric="cpu_percent"))["points"]
    recorded_now = ga.s.now()
    monkeypatch.setattr(ga.s, "now", lambda: recorded_now + timedelta(hours=2))
    assert "noc_cpu_percent{" not in ga.s.prometheus(x.db)
    assert ga.s.health(x.db)["metrics_state"] == "stale"


def test_g_revenue_shared_costs_contribution_roi_and_estimate_separation(finances):
    x = finances
    preview = fin.import_charge(x)
    fin.s.approve_import(
        x.db, x.u, preview["id"], fin.b(fin.ApprovalInput, digest=preview["digest"])
    )
    fin.entry(x, scope="service1", amount="225")
    shared = fin.entry(
        x, scope="network", kind="opex", category="backhaul", amount="100"
    )
    allocation = fin.s.allocate(
        x.db,
        x.u,
        shared["id"],
        fin.b(
            fin.AllocationInput,
            method="equal",
            targets=[
                {"scope_id": x.scopes["tower0"]},
                {"scope_id": x.scopes["tower1"]},
            ],
        ),
    )
    assert sum(Decimal(r["amount"]) for r in allocation["shares"]) == Decimal("100")
    fin.entry(x, scope="tower0", kind="capex", amount="250")
    fin.attest(x)
    r = fin.report(x)
    assert r["contribution"] == "25.00" and r["operational_roi_percent"] == "10.00"
    assert fin.report(x, "network")["totals"]["opex"] == "100.00"
    fin.entry(x, amount="999", basis="estimated")
    assert fin.report(x)["totals"]["revenue"] == "75.00"
    assert fin.report(x, revenue_basis="estimated")["totals"]["revenue"] == "999.00"
    assert "not collected cash" in r["basis_label"]


def test_reviewed_integration_manifest_is_complete_and_single_head():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "integration_manifest", root / "scripts/release/integration_manifest.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.audit()
    assert result["direct_ancestors"] == 112 and result["logical_replays"] == 1
    assert (
        result["pr86_merge_tree_unchanged"]
        and result["baseline_commits_beyond_default"] == 298
    )
    assert not any(p["default_contains_tip"] for p in result["pull_requests"])
    assert len(result["migrations"]) == 34
    assert result["migrations"][-1]["revision"] == "0034_customer_retention"
    assert result["migrations"][-1]["parent"] == "0033_powercode_integration"
    assert len({c["environment"] for c in result["configuration"]}) == len(
        result["configuration"]
    )
