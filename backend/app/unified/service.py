"""Stored-record graph only. No sockets, discovery, provisioning or notification delivery."""

from collections import defaultdict, deque
from datetime import timedelta
import math
from sqlalchemy import select, func, or_, text
from app.models.entities import Site, Device, Sector, NetworkLink, Incident, AuditLog
from app.equipment.models import Asset, Location
from app.customers.models import CustomerService
from app.field.models import WorkOrder, QRLabel, now
from app.field import service as field
from app.equipment import service as inv
from app.maintenance_ops.models import Document
from app.ipam.models import Prefix, Interface, Assignment
from app.core.config import settings
from .models import *

LIMIT_NODES = 20000
LIMIT_EDGES = 50000


def reader(u):
    if u.role not in {"admin", "operator"}:
        raise PermissionError("Private network operator required")


def event(db, u, action, reason, data):
    inv.clean(data)
    db.add(Event(actor_id=u.id, action=action, reason=reason, data=data))
    db.add(
        AuditLog(
            user_id=u.id,
            action="unified_" + action,
            entity_type="unified_network",
            entity_id=str(data.get("id", "graph")),
            details={"documentation_only": True},
        )
    )


def fields(b):
    return b.model_dump(exclude={"operation_key", "reason"})


def get(db, cls, id):
    return field.get(db, cls, id)


def node_site(db, n):
    if n.site_id:
        return n.site_id
    if n.sector_id:
        return get(db, Sector, n.sector_id).site_id
    if n.service_id:
        return get(db, CustomerService, n.service_id).site_id
    if n.device_id:
        return get(db, Device, n.device_id).wisp_site_id
    a = get(db, Asset, n.asset_id)
    return (
        a.site_id
        if hasattr(a, "site_id") and a.site_id
        else (get(db, Location, a.location_id).site_id if a.location_id else None)
    )


def node_device(db, n):
    return n.device_id or (get(db, Asset, n.asset_id).device_id if n.asset_id else None)


def create_node(db, u, b):
    for key, cls in [
        ("site_id", Site),
        ("asset_id", Asset),
        ("device_id", Device),
        ("sector_id", Sector),
        ("service_id", CustomerService),
    ]:
        if getattr(b, key):
            get(db, cls, getattr(b, key))
    device = b.device_id or (
        get(db, Asset, b.asset_id).device_id if b.asset_id else None
    )
    if device:
        assets = select(Asset.id).where(Asset.device_id == device)
        if db.scalar(
            select(Node.id).where(
                or_(Node.device_id == device, Node.asset_id.in_(assets))
            )
        ):
            raise ValueError(
                "Monitored device already represented; reuse its canonical node"
            )
    row = Node(**fields(b))
    db.add(row)
    db.flush()
    event(db, u, "node_created", b.reason, {"id": row.id})
    return inv.view(row)


def create_port(db, u, b):
    n = get(db, Node, b.node_id)
    if b.kind == "pon" and n.kind != "olt":
        raise ValueError("PON port requires OLT asset")
    if b.kind.startswith("splitter") and n.kind != "splitter":
        raise ValueError("Splitter asset required")
    if b.kind == "ont" and n.kind != "ont":
        raise ValueError("ONT asset required")
    if b.split_ratio and n.kind != "splitter":
        raise ValueError("Split ratio belongs to splitter")
    r = Port(**fields(b))
    db.add(r)
    db.flush()
    event(db, u, "port_created", b.reason, {"id": r.id})
    return inv.view(r)


def create_edge(db, u, b):
    a, z = get(db, Node, b.a_id), get(db, Node, b.z_id)
    for p, n in [(b.a_port_id, a), (b.z_port_id, z)]:
        if p and get(db, Port, p).node_id != n.id:
            raise ValueError("Port/endpoint mismatch")
    if b.relation in {"pon_splitter", "splitter_ont"}:
        expected = (
            ("olt", "splitter") if b.relation == "pon_splitter" else ("splitter", "ont")
        )
        if (
            (a.kind, z.kind) != expected
            or b.technology != "gpon"
            or not (b.a_port_id and b.z_port_id)
        ):
            raise ValueError("Ordered GPON assets and ports required")
        pa, pz = get(db, Port, b.a_port_id), get(db, Port, b.z_port_id)
        if b.relation == "pon_splitter" and (
            pa.kind != "pon" or pz.kind != "splitter_input"
        ):
            raise ValueError("PON-to-splitter-input ports required")
        if b.relation == "splitter_ont" and (
            pa.kind != "splitter_output" or pz.kind != "ont"
        ):
            raise ValueError("Splitter-output-to-ONT ports required")
        if db.scalar(
            select(Edge.id).where(
                Edge.z_port_id == b.z_port_id,
                Edge.relation.in_(["pon_splitter", "splitter_ont"]),
                Edge.state != "out_of_service",
            )
        ):
            raise ValueError("Downstream GPON port already connected")
        if pa.kind == "splitter_output" and db.scalar(
            select(Edge.id).where(
                Edge.a_port_id == pa.id, Edge.state != "out_of_service"
            )
        ):
            raise ValueError("Splitter output already assigned")
    if b.network_link_id:
        l = get(db, NetworkLink, b.network_link_id)
        if {node_site(db, a), node_site(db, z)} != {l.a_site_id, l.z_site_id}:
            raise ValueError("Existing link endpoints differ")
        if (b.technology == "fiber") != (l.link_type == "fiber"):
            raise ValueError("Existing link technology conflict")
    r = Edge(**fields(b))
    db.add(r)
    db.flush()
    event(db, u, "edge_created", b.reason, {"id": r.id})
    return inv.view(r)


def create_cable(db, u, b):
    edge = get(db, Edge, b.edge_id)
    if edge.layer != "physical" or edge.technology != "fiber":
        raise ValueError("Physical fiber edge required")
    r = Cable(**fields(b))
    db.add(r)
    db.flush()
    strands = [Strand(cable_id=r.id, number=i + 1) for i in range(r.strand_count)]
    db.add_all(strands)
    db.flush()
    db.add_all(
        [
            End(strand_id=s.id, side=side, node_id=n)
            for s in strands
            for side, n in [("a", edge.a_id), ("z", edge.z_id)]
        ]
    )
    event(db, u, "cable_created", b.reason, {"id": r.id, "strands": r.strand_count})
    db.flush()
    return inv.view(r)


def create_pair(db, u, b):
    if len(set(b.strand_ids)) != 2:
        raise ValueError("Two distinct strands required")
    rows = [get(db, Strand, id) for id in b.strand_ids]
    if rows[0].cable_id != rows[1].cable_id:
        raise ValueError("Documented pair must share a cable")
    r = Pair(code=b.code, cable_id=rows[0].cable_id)
    db.add(r)
    db.flush()
    db.add_all([PairMember(pair_id=r.id, strand_id=s.id) for s in rows])
    event(db, u, "pair_created", b.reason, {"id": r.id, "strands": b.strand_ids})
    return inv.view(r)


def create_join(db, u, b):
    n = get(db, Node, b.node_id)
    if n.kind not in {
        "splice_enclosure",
        "patch_panel",
        "odf",
        "cabinet",
        "hybrid",
        "pop",
        "core",
        "headend",
        "tower",
    }:
        raise ValueError("Documented splice/patch location required")
    ends = [get(db, End, id) for id in b.end_ids]
    if ends[0].strand_id == ends[1].strand_id or any(e.node_id != n.id for e in ends):
        raise ValueError("Join must connect distinct cable strand ends at one location")
    if b.optical_test_document_id:
        d = get(db, Document, b.optical_test_document_id)
        if d.deleted_at or not d.approved_revision:
            raise ValueError("Approved optical test document required")
        if d.site_id != node_site(db, n):
            raise ValueError("Test document/site mismatch")
    r = Join(**b.model_dump(exclude={"operation_key", "reason", "end_ids"}))
    db.add(r)
    db.flush()
    db.add_all([JoinEnd(end_id=e.id, join_id=r.id) for e in ends])
    event(
        db,
        u,
        "join_created",
        b.reason,
        {
            "id": r.id,
            "ends": b.end_ids,
            "test_evidence_recorded": bool(b.optical_test_document_id),
        },
    )
    return inv.view(r)


def reserve(db, u, b):
    rows = db.scalars(
        select(Strand)
        .where(Strand.id.in_(b.strand_ids))
        .order_by(Strand.id)
        .with_for_update()
    ).all()
    if len(rows) != len(b.strand_ids):
        raise ValueError("Unknown strand")
    if b.circuit_id:
        get(db, Circuit, b.circuit_id)
    # Validate the entire set before mutating any row.
    for s in rows:
        edge = get(db, Edge, get(db, Cable, s.cable_id).edge_id)
        if s.revision != b.expected_revisions[s.id]:
            raise ValueError("Strand changed; refresh")
        if b.action in {"reserve", "assign"}:
            if edge.state not in {"installed", "active", "reserved"}:
                raise ValueError("Installed cable required")
            if s.state != "available" and not (
                s.state == "reserved"
                and s.circuit_id == b.circuit_id
                and b.action == "assign"
            ):
                raise ValueError(
                    "Strand is not available; expiry does not automatically release reservations"
                )
        elif b.action == "release":
            if db.scalar(
                select(Hop.path_id)
                .join(Path, Hop.path_id == Path.id)
                .where(Hop.strand_id == s.id, Path.state == "active")
            ):
                raise ValueError(
                    "Retire active paths before releasing documented service"
                )
        elif s.state in {"assigned", "reserved"}:
            raise ValueError("Release documented assignment first")
    for s in rows:
        s.state = {
            "reserve": "reserved",
            "assign": "assigned",
            "release": "available",
            "out_of_service": "out_of_service",
        }[b.action]
        s.circuit_id = b.circuit_id if b.action in {"reserve", "assign"} else None
        s.reserved_until = b.reserved_until if b.action == "reserve" else None
        s.revision += 1
    event(
        db,
        u,
        "strands_" + b.action,
        b.reason,
        {"strands": b.strand_ids, "circuit_id": b.circuit_id, "no_provisioning": True},
    )
    return {"strands": [inv.view(s) for s in rows]}


def circuit(db, u, b):
    if b.service_id:
        get(db, CustomerService, b.service_id)
    r = Circuit(**fields(b))
    db.add(r)
    db.flush()
    event(db, u, "circuit_created", b.reason, {"id": r.id})
    return inv.view(r)


def dependency_values(nodes, edges, assets, devices, services, node_ids, edge_ids):
    return {
        "joins": {},
        "end_ports": {},
        "nodes": {
            str(id): nodes[id].revision for id in sorted(node_ids) if id in nodes
        },
        "edges": {
            str(id): edges[id].revision for id in sorted(edge_ids) if id in edges
        },
        "assets": {
            str(n.asset_id): [
                assets[n.asset_id].revision,
                assets[n.asset_id].location_id,
                assets[n.asset_id].state,
            ]
            for id in node_ids
            if (n := nodes.get(id)) and n.asset_id in assets
        },
        "device_sites": {
            str(n.device_id): devices[n.device_id].wisp_site_id
            for id in node_ids
            if (n := nodes.get(id)) and n.device_id in devices
        },
        "services": {
            str(n.service_id): [
                services[n.service_id].site_id,
                services[n.service_id].device_id,
                services[n.service_id].sector_id,
                services[n.service_id].fiber_link_id,
                services[n.service_id].mapping_state,
            ]
            for id in node_ids
            if (n := nodes.get(id)) and n.service_id in services
        },
    }


def capture_dependencies(db, node_ids, edge_ids, strand_ids=()):
    ns = {n.id: n for n in db.scalars(select(Node).where(Node.id.in_(node_ids)))}
    es = {e.id: e for e in db.scalars(select(Edge).where(Edge.id.in_(edge_ids)))}
    assets = {
        a.id: a
        for a in db.scalars(
            select(Asset).where(
                Asset.id.in_([n.asset_id for n in ns.values() if n.asset_id])
            )
        )
    }
    devices = {
        d.id: d
        for d in db.scalars(
            select(Device).where(
                Device.id.in_([n.device_id for n in ns.values() if n.device_id])
            )
        )
    }
    services = {
        s.id: s
        for s in db.scalars(
            select(CustomerService).where(
                CustomerService.id.in_(
                    [n.service_id for n in ns.values() if n.service_id]
                )
            )
        )
    }
    value = dependency_values(ns, es, assets, devices, services, node_ids, edge_ids)
    end_rows = list(db.scalars(select(End).where(End.strand_id.in_(strand_ids))))
    ends = [e.id for e in end_rows]
    value["end_ports"] = {
        str(e.id): e.port_id for e in end_rows if e.port_id is not None
    }
    value["joins"] = {
        str(e): j
        for e, j in db.execute(
            select(JoinEnd.end_id, JoinEnd.join_id)
            .join(Join, Join.id == JoinEnd.join_id)
            .where(JoinEnd.end_id.in_(ends), Join.active.is_(True))
        )
    }
    return value


def create_path(db, u, b):
    c = get(db, Circuit, b.circuit_id)
    src, dst = get(db, Node, b.source_id), get(db, Node, b.destination_id)
    if src.kind not in {"upstream", "core", "headend"}:
        raise ValueError("Path must start at documented upstream/core/headend")
    if c.service_id and dst.service_id != c.service_id:
        raise ValueError("Path must end at its customer service identity")
    current = src.id
    visited = {current}
    previous_fiber = None
    for h in b.hops:
        e = get(db, Edge, h.edge_id)
        if e.layer != "physical":
            raise ValueError(
                "Service paths require explicit physical transports; logical dependencies alone do not establish continuity"
            )
        if current not in {e.a_id, e.z_id}:
            raise ValueError("Noncontiguous path")
        next_id = e.z_id if current == e.a_id else e.a_id
        if next_id in visited:
            raise ValueError("Cyclic path")
        if b.state == "active" and (
            e.state not in {"active", "installed"}
            or get(db, Node, next_id).state not in {"active", "installed"}
        ):
            raise ValueError("Active path requires installed components")
        if e.technology == "fiber":
            cable = db.scalar(select(Cable).where(Cable.edge_id == e.id))
            if not cable or not h.strand_id:
                raise ValueError("Fiber path needs cable and explicit strand")
            s = get(db, Strand, h.strand_id)
            if s.cable_id != cable.id or (
                b.state == "active" and (s.state != "assigned" or s.circuit_id != c.id)
            ):
                raise ValueError("Strand/circuit assignment mismatch")
            entry = db.scalar(
                select(End).where(End.strand_id == s.id, End.node_id == current)
            )
            exit = db.scalar(
                select(End).where(End.strand_id == s.id, End.node_id == next_id)
            )
            if previous_fiber:
                ja = db.get(JoinEnd, previous_fiber)
                jb = db.get(JoinEnd, entry.id)
                if (
                    not ja
                    or not jb
                    or ja.join_id != jb.join_id
                    or not get(db, Join, ja.join_id).active
                ):
                    raise ValueError("Missing documented splice/patch continuity")
            previous_fiber = exit.id
        else:
            if h.strand_id:
                raise ValueError("Strand supplied for nonfiber transport")
            previous_fiber = None
        current = next_id
        visited.add(current)
    if current != dst.id:
        raise ValueError("Path destination mismatch")
    r = Path(**b.model_dump(exclude={"operation_key", "reason", "hops"}))
    r.verified_at = now() if b.verified_until else None
    r.inventory_snapshot = capture_dependencies(
        db,
        visited,
        {h.edge_id for h in b.hops},
        {h.strand_id for h in b.hops if h.strand_id},
    )
    db.add(r)
    db.flush()
    db.add_all(
        [Hop(path_id=r.id, position=i, **h.model_dump()) for i, h in enumerate(b.hops)]
    )
    event(
        db,
        u,
        "path_created",
        b.reason,
        {"id": r.id, "circuit_id": c.id, "topology_nodes": sorted(visited)},
    )
    db.flush()
    return inv.view(r)


def state_change(db, u, b):
    cls = {"node": Node, "edge": Edge, "circuit": Circuit, "path": Path, "join": Join}[
        b.target
    ]
    r = get(db, cls, b.id)
    if b.target in {"node", "edge", "circuit"}:
        if b.expected_revision != r.revision:
            raise ValueError("Refresh changed component")
        if b.state is None:
            raise ValueError("State required")
        r.state = b.state
        r.revision += 1
    elif b.target == "path":
        if b.state not in {"out_of_service", "planned"}:
            raise ValueError(
                "Create a newly validated path to activate/reverify; history retained"
            )
        r.state = b.state
        r.verified_until = None
    else:
        if b.active is not False:
            raise ValueError("Create new join to reactivate")
        r.active = False
        # Endpoint history stays in immutable event; freeing ends allows a new documented splice.
        old = db.scalars(select(JoinEnd).where(JoinEnd.join_id == r.id)).all()
        for end in old:
            db.delete(end)
    event(
        db,
        u,
        "state_changed",
        b.reason,
        {"id": r.id, "target": b.target, "state": b.state, "active": b.active},
    )
    return inv.view(r)


def association(db, u, b):
    component = get(db, Node, b.node_id) if b.node_id else get(db, Edge, b.edge_id)
    nodes = (
        [component]
        if b.node_id
        else [get(db, Node, component.a_id), get(db, Node, component.z_id)]
    )
    sites = {sid for n in nodes if (sid := node_site(db, n)) is not None}
    assets = {n.asset_id for n in nodes if n.asset_id}
    if b.document_id:
        d = get(db, Document, b.document_id)
        if d.deleted_at or (d.site_id not in sites and d.asset_id not in assets):
            raise ValueError("Document must belong to component site/asset")
    if b.work_order_id:
        w = get(db, WorkOrder, b.work_order_id)
        if w.site_id not in sites and w.asset_id not in assets:
            raise ValueError("Work order/component location mismatch")
    if b.incident_id:
        i = get(db, Incident, b.incident_id)
        linked = bool(
            b.edge_id
            and component.network_link_id is not None
            and i.link_id == component.network_link_id
        )
        if i.site_id not in sites and not linked:
            raise ValueError("Incident/component mismatch")
    stmt = select(Association)
    for k, v in fields(b).items():
        stmt = stmt.where(getattr(Association, k) == v)
    old = db.scalar(stmt)
    if old:
        return inv.view(old)
    r = Association(**fields(b))
    db.add(r)
    db.flush()
    event(db, u, "associated", b.reason, {"id": r.id, **fields(b)})
    return inv.view(r)


def observe(db, u, b):
    p = get(db, Port, b.port_id)
    if p.kind not in {"pon", "ont", "splitter_input", "splitter_output"}:
        raise ValueError("GPON port required")
    if b.source == "noc-gpon-fixture-v1" and not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError(
            "Fixture collector requires isolated staging; live collector is not implemented"
        )
    values = b.metrics.model_dump()
    old = db.scalar(
        select(Observation).where(
            Observation.port_id == p.id,
            Observation.source == b.source,
            Observation.observed_at == b.observed_at,
        )
    )
    if old:
        if old.metrics != values:
            raise ValueError("Conflicting duplicate sample")
        return inv.view(old)
    r = Observation(
        port_id=p.id,
        observed_at=b.observed_at,
        source=b.source,
        metrics=values,
        original={
            "contract": "NOC-owned read-only GPON observation v1",
            "values": values,
        },
    )
    db.add(r)
    db.flush()
    event(db, u, "observation_recorded", b.reason, {"id": r.id})
    return inv.view(r)


def load_graph(db):
    nodes = db.scalars(select(Node).order_by(Node.id).limit(LIMIT_NODES + 1)).all()
    edges = db.scalars(select(Edge).order_by(Edge.id).limit(LIMIT_EDGES + 1)).all()
    if len(nodes) > LIMIT_NODES or len(edges) > LIMIT_EDGES:
        raise ValueError(
            "Graph scope exceeds supported bound; partition documented inventory"
        )
    return {n.id: n for n in nodes}, {e.id: e for e in edges}


def trace_strand(db, id):
    get(db, Strand, id)
    # One bulk query per table; cycles and disconnected tails are explicit.
    strands = db.scalars(select(Strand).order_by(Strand.id).limit(100001)).all()
    ends = db.scalars(select(End).order_by(End.id).limit(200001)).all()
    if len(strands) > 100000 or len(ends) > 200000:
        raise ValueError("Strand graph bound exceeded")
    joins = db.execute(
        select(
            JoinEnd.end_id,
            JoinEnd.join_id,
            Join.optical_test_document_id,
            Join.optical_tested_at,
        )
        .join(Join, JoinEnd.join_id == Join.id)
        .where(Join.active.is_(True))
    ).all()
    paired = defaultdict(list)
    bystrand = defaultdict(list)
    adj = defaultdict(list)
    byend = {e.id: e for e in ends}
    for e in ends:
        bystrand[e.strand_id].append(e.id)
    for group in bystrand.values():
        if len(group) == 2:
            adj[group[0]].append(group[1])
            adj[group[1]].append(group[0])
    for e, j, doc, at in joins:
        paired[j].append((e, doc, at))
    incomplete = []
    for j, members in paired.items():
        if len(members) != 2:
            incomplete.append({"join_id": j, "reason": "Join lacks exactly two ends"})
            continue
        a, b = members[0][0], members[1][0]
        if a not in byend or b not in byend or byend[a].node_id != byend[b].node_id:
            incomplete.append({"join_id": j, "reason": "Join location conflict"})
            continue
        adj[a].append(b)
        adj[b].append(a)
    seen = set()
    queue = deque(bystrand[id][:1])
    parent = {x: None for x in queue}
    cycle = False
    while queue:
        v = queue.popleft()
        if v in seen:
            continue
        seen.add(v)
        if len(seen) > 10000:
            raise ValueError("Trace bound10000 ends exceeded")
        for w in adj[v]:
            if w in seen and parent.get(v) != w:
                cycle = True
            elif w not in parent:
                parent[w] = v
                queue.append(w)
    result = sorted({byend[e].strand_id for e in seen})
    return {
        "start_strand_id": id,
        "strand_ids": result,
        "ends": [inv.view(byend[e]) for e in sorted(seen)],
        "open_ends": [e for e in seen if len(adj[e]) < 2],
        "cycles": cycle,
        "issues": incomplete,
        "optical_continuity": "unverified; documented adjacency is not an optical test",
        "test_documents": sorted(
            {doc for e, j, doc, at in joins if e in seen and doc is not None}
        ),
    }


def path_status(db, nodes, edges, paths, hops, failed_nodes, failed_edges):
    strand_ids = {h.strand_id for rows in hops.values() for h in rows if h.strand_id}
    strandmap = {
        v.id: v for v in db.scalars(select(Strand).where(Strand.id.in_(strand_ids)))
    }
    cablemap = {
        v.id: v
        for v in db.scalars(
            select(Cable).where(Cable.id.in_({v.cable_id for v in strandmap.values()}))
        )
    }
    end_rows = list(db.scalars(select(End).where(End.strand_id.in_(strand_ids))))
    endmap = {(e.strand_id, e.node_id): e.id for e in end_rows}
    endpoint_ports = {str(e.id): e.port_id for e in end_rows if e.port_id is not None}
    joined = {
        end: join
        for end, join in db.execute(
            select(JoinEnd.end_id, JoinEnd.join_id)
            .join(Join, JoinEnd.join_id == Join.id)
            .where(Join.active.is_(True))
        )
    }
    port_ids = {p for e in edges.values() for p in (e.a_port_id, e.z_port_id) if p}
    verification_start = min(
        [p.verified_at for p in paths if p.verified_at], default=now()
    )
    port_disruptions = dict(
        db.execute(
            select(Observation.port_id, func.max(Observation.observed_at))
            .where(
                Observation.port_id.in_(port_ids),
                Observation.observed_at >= verification_start,
                Observation.metrics["status"].as_string() == "down",
            )
            .group_by(Observation.port_id)
        ).all()
    )
    down_ports = {
        o.port_id
        for o in db.scalars(
            select(Observation)
            .where(Observation.port_id.in_(port_ids))
            .distinct(Observation.port_id)
            .order_by(
                Observation.port_id,
                Observation.observed_at.desc(),
                Observation.id.desc(),
            )
        )
        if 0 <= (now() - o.observed_at).total_seconds() <= 300
        and o.metrics.get("status") == "down"
    }
    native_assets = {
        v.id: v
        for v in db.scalars(
            select(Asset).where(
                Asset.id.in_([n.asset_id for n in nodes.values() if n.asset_id])
            )
        )
    }
    native_devices = {
        v.id: v
        for v in db.scalars(
            select(Device).where(
                Device.id.in_(
                    {n.device_id for n in nodes.values() if n.device_id}
                    | {a.device_id for a in native_assets.values() if a.device_id}
                )
            )
        )
    }
    native_services = {
        v.id: v
        for v in db.scalars(
            select(CustomerService).where(
                CustomerService.id.in_(
                    [n.service_id for n in nodes.values() if n.service_id]
                )
            )
        )
    }
    result = {}
    now_at = now()
    for p in paths:
        rows = sorted(hops.get(p.id, []), key=lambda h: h.position)
        current = p.source_id
        visited = {current}
        issues = []
        dependencies = set()
        blocked = False
        previous_end = None
        if not rows:
            issues.append("Missing path hops")
        for i, h in enumerate(rows):
            e = edges.get(h.edge_id)
            if not e or h.position != i or current not in {e.a_id, e.z_id}:
                issues.append("Missing or noncontiguous path record")
                break
            next_node = e.z_id if current == e.a_id else e.a_id
            if e.technology == "fiber":
                strand = strandmap.get(h.strand_id)
                cable = cablemap.get(strand.cable_id) if strand else None
                if (
                    not strand
                    or not cable
                    or cable.edge_id != e.id
                    or strand.state != "assigned"
                    or strand.circuit_id != p.circuit_id
                ):
                    issues.append("Missing or changed fiber assignment")
                if previous_end is not None and (
                    previous_end not in joined
                    or joined.get(previous_end)
                    != joined.get(endmap.get((h.strand_id, current)))
                ):
                    issues.append("Splice/patch continuity no longer documented")
                previous_end = endmap.get((h.strand_id, next_node))
            else:
                previous_end = None
            dependencies.add(e.id)
            current = e.z_id if current == e.a_id else e.a_id
            if current in visited:
                issues.append("Path cycle")
                break
            visited.add(current)
            if e.a_port_id in down_ports or e.z_port_id in down_ports:
                issues.append("Contradictory fresh GPON down observation")
                blocked = True
            if e.state not in {"installed", "active"}:
                issues.append("Transport not installed/active")
            if e.id in failed_edges:
                blocked = True
        if current != p.destination_id:
            issues.append("Path destination not reached")
        if any(n not in nodes for n in visited):
            issues.append("Missing node")
        if any(
            nodes[n].state not in {"installed", "active"} for n in visited if n in nodes
        ):
            issues.append("Component not installed/active")
        if visited & failed_nodes:
            blocked = True
        for eid in dependencies:
            edge = edges[eid]
            if p.verified_at and any(
                port_disruptions.get(pid) and port_disruptions[pid] >= p.verified_at
                for pid in (edge.a_port_id, edge.z_port_id)
            ):
                issues.append(
                    "GPON disruption after verification; end-to-end recheck required"
                )
        current_snapshot = dependency_values(
            nodes,
            edges,
            native_assets,
            native_devices,
            native_services,
            visited,
            dependencies,
        )
        path_ends = {
            endmap.get((h.strand_id, nid))
            for h in rows
            if h.strand_id
            for nid in visited
        }
        current_snapshot["joins"] = {
            str(e): j for e, j in joined.items() if e in path_ends
        }
        current_snapshot["end_ports"] = {
            str(e): endpoint_ports[str(e)]
            for e in path_ends
            if str(e) in endpoint_ports
        }
        if not p.inventory_snapshot or p.inventory_snapshot != current_snapshot:
            issues.append(
                "Inventory or connection changed since verification; reverify end-to-end service"
            )
        for nid in visited:
            node = nodes.get(nid)
            asset = native_assets.get(node.asset_id) if node else None
            device = (
                native_devices.get(
                    node.device_id or (asset.device_id if asset else None)
                )
                if node
                else None
            )
            if (
                device
                and p.verified_at
                and device.monitor_last_down_at
                and device.monitor_last_down_at >= p.verified_at
            ):
                issues.append(
                    "Monitoring disruption after verification; recheck required"
                )
            if asset and asset.state not in {"installed", "in_service"}:
                issues.append("Asset no longer installed/in service")
            if (
                device
                and device.monitor_status == "down"
                and device.monitor_last_checked_at
                and 0 <= (now() - device.monitor_last_checked_at).total_seconds() <= 300
            ):
                issues.append("Contradictory fresh device-down observation")
                blocked = True
        result[p.id] = {
            "id": p.id,
            "circuit_id": p.circuit_id,
            "state": p.state,
            "node_ids": sorted(visited),
            "edge_ids": sorted(dependencies),
            "blocked": blocked,
            "issues": issues,
            "functioning_alternate_verified": bool(
                p.state == "active"
                and p.verified_at
                and p.verified_until
                and now_at < p.verified_until <= now_at + timedelta(days=1)
                and not blocked
                and not issues
            ),
            "verification_expires_at": (
                p.verified_until.isoformat() if p.verified_until else None
            ),
            "verification_evidence": p.verification_evidence,
        }
    return result


def impact(db, failed_nodes=(), failed_edges=()):
    nodes, edges = load_graph(db)
    fn, fe = set(failed_nodes), set(failed_edges)
    if fn - set(nodes) or fe - set(edges):
        raise ValueError("Unknown failed component")
    dependents = defaultdict(set)
    for edge in edges.values():
        if (
            edge.layer == "logical"
            and edge.relation == "depends_on"
            and edge.state in {"active", "installed"}
        ):
            dependents[edge.z_id].add(edge.a_id)
    queue = deque(fn)
    while queue:
        for child in dependents[queue.popleft()]:
            if child not in fn:
                fn.add(child)
                queue.append(child)
    circuits = db.scalars(
        select(Circuit)
        .where(Circuit.state != "out_of_service")
        .order_by(Circuit.id)
        .limit(10001)
    ).all()
    paths = db.scalars(
        select(Path)
        .where(Path.state != "out_of_service")
        .order_by(Path.id)
        .limit(20001)
    ).all()
    if len(circuits) > 10000 or len(paths) > 20000:
        raise ValueError("Impact circuit/path scope exceeded")
    rows = db.scalars(
        select(Hop).where(Hop.path_id.in_([p.id for p in paths])).limit(1280001)
    ).all()
    if len(rows) > 1280000:
        raise ValueError("Impact hop bound exceeded")
    hops = defaultdict(list)
    for h in rows:
        hops[h.path_id].append(h)
    status = path_status(db, nodes, edges, paths, hops, fn, fe)
    byc = defaultdict(list)
    for v in status.values():
        byc[v["circuit_id"]].append(v)
    services = {
        s.id: s
        for s in db.scalars(
            select(CustomerService).where(
                CustomerService.id.in_([c.service_id for c in circuits if c.service_id])
            )
        )
    }
    findings = []
    affected = set()
    uncertain = set()
    preserved = set()
    for c in circuits:
        allp = byc[c.id]
        blocked = [p for p in allp if p["blocked"]]
        verified = [p for p in allp if p["functioning_alternate_verified"]]
        if verified:
            category = "preserved_by_verified_path"
            preserved.add(c.service_id) if c.service_id else None
        elif not allp or any(p["issues"] for p in allp) or not c.topology_complete:
            category = "estimated_unknown"
            uncertain.add(c.service_id) if c.service_id else None
        elif blocked and len(blocked) == len(allp):
            category = "potentially_affected"
            affected.add(c.service_id) if c.service_id else None
        elif blocked:
            category = "estimated_unknown"
            uncertain.add(c.service_id) if c.service_id else None
        else:
            category = "no_documented_failed_dependency"
        findings.append(
            {
                "circuit_id": c.id,
                "service_id": c.service_id,
                "purpose": c.purpose,
                "category": category,
                "paths": allp,
                "reason": "Only current explicit end-to-end verification preserves alternate service; incomplete topology stays uncertain",
                "topology_complete": c.topology_complete,
            }
        )
    # A service can have several transport circuits. Any verified end-to-end path preserves it.
    affected -= preserved
    uncertain -= preserved
    uncertain -= affected
    target_ids = sorted(affected | uncertain)
    customers = sorted(
        {
            services[id].customer_id
            for id in target_ids
            if id in services
            and services[id].status == "active"
            and services[id].source_deleted_at is None
        }
    )
    impacted_nodes = fn | {
        n for v in status.values() if v["blocked"] for n in v["node_ids"]
    }
    return {
        "failed_node_ids": sorted(fn),
        "failed_edge_ids": sorted(fe),
        "potentially_affected_services": sorted(affected),
        "estimated_services": sorted(uncertain),
        "preserved_services": sorted(preserved),
        "customer_ids_for_approval": customers,
        "unique_customer_count": len(customers),
        "circuits": findings,
        "infrastructure": [
            {"node_id": n, "kind": nodes[n].kind}
            for n in sorted(impacted_nodes)
            if n in nodes
        ],
        "notification_policy": "Review against existing NOC-97 incident and NOC-96 consent/approval policy; no delivery or automatic incident creation",
        "external_delivery": False,
        "model_not_observed_outage": True,
    }


def capacity(db, edge_ids=None):
    edges = db.scalars(
        select(Edge).where(Edge.id.in_(edge_ids))
        if edge_ids is not None
        else select(Edge).order_by(Edge.id).limit(1001)
    ).all()
    if len(edges) > 1000:
        raise ValueError("Capacity scope1000 edges; filter map first")
    ids = [e.id for e in edges]
    cables = {
        c.edge_id: c for c in db.scalars(select(Cable).where(Cable.edge_id.in_(ids)))
    }
    counts = defaultdict(dict)
    for cable_id, state, count in db.execute(
        select(Strand.cable_id, Strand.state, func.count())
        .where(Strand.cable_id.in_([c.id for c in cables.values()]))
        .group_by(Strand.cable_id, Strand.state)
    ):
        counts[cable_id][state] = count
    links = {
        l.id: l
        for l in db.scalars(
            select(NetworkLink).where(
                NetworkLink.id.in_(
                    [e.network_link_id for e in edges if e.network_link_id]
                )
            )
        )
    }
    from app.models.entities import RFAnalyticsPoint

    rf = {
        v.link_id: v
        for v in db.scalars(
            select(RFAnalyticsPoint)
            .where(
                RFAnalyticsPoint.link_id.in_(list(links)),
            )
            .distinct(RFAnalyticsPoint.link_id)
            .order_by(
                RFAnalyticsPoint.link_id,
                RFAnalyticsPoint.sampled_at.desc(),
                RFAnalyticsPoint.id.desc(),
            )
        )
    }
    results = []
    for e in edges:
        l = links.get(e.network_link_id)
        c = cables.get(e.id)
        results.append(
            {
                "edge_id": e.id,
                "technology": e.technology,
                "state": e.state,
                "configured_mbps": l.capacity_mbps if l else e.configured_capacity_mbps,
                "estimated_mbps": e.estimated_capacity_mbps,
                "observed_mbps": None,
                "observed_capacity_mbps": None,
                "utilization_pct": None,
                "measurement_state": "unavailable; inventory capacity is not observed throughput",
                "strand_capacity": counts[c.id] if c else None,
                "documented_strands": c.strand_count if c else None,
                "source_network_link_id": e.network_link_id,
                "recommendation": (
                    "Document additional strands or reviewed expansion; no automatic allocation"
                    if c and counts[c.id].get("available", 0) == 0
                    else None
                ),
            }
        )
        point = rf.get(e.network_link_id)
        if point:
            fresh = 0 <= (now() - point.sampled_at).total_seconds() <= 300
            results[-1].update(
                last_sampled_at=point.sampled_at.isoformat(),
                measurement_state=(
                    "invalid" if not point.valid else "fresh" if fresh else "stale"
                ),
                rf_source_profile=point.source_profile,
            )
            if fresh and point.valid:
                results[-1].update(
                    observed_capacity_mbps=point.capacity_mbps,
                    tx_throughput_mbps=point.tx_throughput_mbps,
                    rx_throughput_mbps=point.rx_throughput_mbps,
                    observed_mbps_basis="Maximum of observed TX/RX direction; not an invented total RF capacity",
                    observed_mbps=max(
                        [
                            v
                            for v in [
                                point.tx_throughput_mbps,
                                point.rx_throughput_mbps,
                            ]
                            if v is not None
                        ],
                        default=None,
                    ),
                    utilization_pct=point.utilization_percent,
                    signal_dbm=point.signal_dbm,
                    snr_db=point.snr_db,
                    frequency_mhz=point.frequency_mhz,
                    channel_width_mhz=point.bandwidth_mhz,
                )
    return results


def port_health(db, ids):
    ports = db.scalars(select(Port).where(Port.id.in_(ids))).all()
    latest = {
        o.port_id: o
        for o in db.scalars(
            select(Observation)
            .where(Observation.port_id.in_(ids))
            .distinct(Observation.port_id)
            .order_by(
                Observation.port_id,
                Observation.observed_at.desc(),
                Observation.id.desc(),
            )
        )
    }
    connection_counts = dict(
        db.execute(
            select(Edge.a_port_id, func.count())
            .where(Edge.a_port_id.in_(ids), Edge.state == "active")
            .group_by(Edge.a_port_id)
        ).all()
    )
    output = []
    for p in ports:
        o = latest.get(p.id)
        fresh = bool(o and 0 <= (now() - o.observed_at).total_seconds() <= 300)
        connected = connection_counts.get(p.id, 0)
        m = o.metrics if fresh else {}
        cap = m.get("observed_capacity_mbps")
        traffic = m.get("throughput_mbps")
        output.append(
            inv.view(p)
            | {
                "observed_metrics": m,
                "telemetry_state": (
                    "fresh" if fresh else "stale" if o else "unavailable"
                ),
                "last_observation": o.observed_at.isoformat() if o else None,
                "documented_connections": connected,
                "configured_mbps": p.configured_capacity_mbps,
                "observed_utilization_pct": (
                    round(100 * traffic / cap, 2)
                    if cap and traffic is not None
                    else None
                ),
                "provisioning": "documentation_only",
            }
        )
    return output


def node_context(db, u, id):
    reader(u)
    n = get(db, Node, id)
    site = node_site(db, n)
    device = node_device(db, n)
    canonical = {
        "site_id": site,
        "asset_id": n.asset_id,
        "device_id": device,
        "sector_id": n.sector_id,
        "customer_service_id": n.service_id,
    }
    device_data = None
    if device:
        d = get(db, Device, device)
        fresh = bool(
            d.monitor_last_checked_at
            and 0 <= (now() - d.monitor_last_checked_at).total_seconds() <= 300
        )
        device_data = {
            "vendor": d.vendor,
            "model": d.model,
            "monitoring_state": d.monitor_status if fresh else "unknown",
            "last_checked": d.monitor_last_checked_at,
            "last_successful_check": d.monitor_last_up_at,
            "rf_history_url": "/api/rf/devices/" + str(device),
            "native_device_id": device,
        }
    associations = db.scalars(
        select(Association).where(Association.node_id == id).limit(100)
    ).all()
    allowed = []
    for relation in associations:
        if relation.document_id:
            d = get(db, Document, relation.document_id)
            if d.deleted_at or (d.visibility == "admin" and u.role != "admin"):
                continue
        allowed.append(relation)
    associations = allowed
    return {
        "node": inv.view(n),
        "canonical": canonical,
        "monitoring": device_data,
        "ports": port_health(
            db,
            [
                p
                for p in db.scalars(
                    select(Port.id).where(Port.node_id == id).limit(500)
                )
            ],
        ),
        "associations": [inv.view(a) for a in associations],
        "ipam_prefix_ids": (
            list(db.scalars(select(Prefix.id).where(Prefix.site_id == site).limit(100)))
            if site
            else []
        ),
        "ipam_interface_ids": (
            list(
                db.scalars(
                    select(Interface.id).where(Interface.device_id == device).limit(100)
                )
            )
            if device
            else []
        ),
        "qr_identifiers": list(
            db.scalars(
                select(QRLabel.identifier)
                .where(
                    or_(
                        QRLabel.site_id == site if site else False,
                        QRLabel.asset_id == n.asset_id if n.asset_id else False,
                    ),
                    QRLabel.revoked_at.is_(None),
                )
                .limit(10)
            )
        ),
        "documents_use_existing_permissions": True,
    }


def qualification(db, lat, lon, technology):
    from app.signup.service import distance

    nodes, edges = load_graph(db)
    site_ids = {n.site_id for n in nodes.values() if n.site_id}
    sites = {s.id: s for s in db.scalars(select(Site).where(Site.id.in_(site_ids)))}
    eligible = []
    for n in nodes.values():
        t = sites.get(n.site_id)
        if t and t.latitude is not None and t.longitude is not None:
            km = distance(lat, lon, t.latitude, t.longitude)
            if km <= 30:
                eligible.append(
                    {
                        "node_id": n.id,
                        "site_id": t.id,
                        "distance_km": round(km, 3),
                        "state": n.state,
                    }
                )
    fiber = db.scalars(select(Cable).order_by(Cable.id).limit(1001)).all()
    if len(fiber) > 1000:
        raise ValueError("Qualification cable scope exceeded")
    from app.signup.service import contains
    from app.signup.models import Area

    areas = db.scalars(select(Area).order_by(Area.id).limit(1001)).all()
    if len(areas) > 1000:
        raise ValueError("Qualification area scope exceeded")
    matched = [a for a in areas if contains(a.polygon, lat, lon)]
    observed_capacity = capacity(db, [c.edge_id for c in fiber]) if fiber else []
    local_edges = set()
    for cable in fiber:
        if not cable.geometry:
            continue
        best = float("inf")
        for a, b in zip(cable.geometry, cable.geometry[1:]):
            scale = math.cos(math.radians(lat))
            ax, ay = (a[0] - lon) * scale, a[1] - lat
            bx, by = (b[0] - lon) * scale, b[1] - lat
            dx, dy = bx - ax, by - ay
            t = (
                max(0, min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
                if dx * dx + dy * dy
                else 0
            )
            best = min(best, math.hypot(ax + t * dx, ay + t * dy) * 111.195)
        if best <= 1:
            local_edges.add(cable.edge_id)
    observed_capacity = [c for c in observed_capacity if c["edge_id"] in local_edges]
    installed = [c for c in observed_capacity if c["state"] in {"installed", "active"}]
    free = [
        c
        for c in installed
        if c["strand_capacity"] and c["strand_capacity"].get("available", 0) > 0
    ]
    relevant = [a for a in matched if a.technology in {technology, "hybrid"}]
    status = "insufficient_information"
    if any(a.status == "active" for a in relevant):
        status = "potentially_serviceable"
    elif any(a.status == "planned" for a in relevant):
        status = "construction_required"
    elif technology == "wireless" and eligible:
        status = "site_survey_required"
    elif technology in {"fiber", "hybrid"} and eligible and free:
        status = "site_survey_required"
    elif technology == "fiber" and eligible and installed and not free:
        status = "capacity_upgrade_required"
    elif any(
        a.technology == "service_boundary" and a.status == "active" for a in areas
    ) and not any(a.technology == "service_boundary" for a in matched):
        status = "not_currently_serviceable"
    reachable = {
        n
        for c in observed_capacity
        for n in [edges[c["edge_id"]].a_id, edges[c["edge_id"]].z_id]
    }
    adjacency = defaultdict(set)
    for edge in edges.values():
        if (
            edge.layer == "physical"
            and edge.technology in {"fiber", "ethernet", "gpon"}
            and edge.state in {"active", "installed"}
        ):
            adjacency[edge.a_id].add(edge.z_id)
            adjacency[edge.z_id].add(edge.a_id)
    queue = deque(reachable)
    while queue:
        for n in adjacency[queue.popleft()]:
            if n not in reachable:
                reachable.add(n)
                queue.append(n)
        if len(reachable) > 2000:
            raise ValueError("Qualification transport scope exceeds2000 nodes")
    ponids = list(
        db.scalars(
            select(Port.id)
            .where(Port.node_id.in_(reachable), Port.kind == "pon")
            .limit(501)
        )
    )
    if len(ponids) > 500:
        raise ValueError("Qualification PON scope exceeds500")
    pon = port_health(db, ponids)
    if (
        technology in {"fiber", "hybrid"}
        and observed_capacity
        and pon
        and all(
            p["configured_subscriber_limit"] is not None
            and p["telemetry_state"] == "fresh"
            and p["observed_metrics"].get("subscribers") is not None
            and p["observed_metrics"]["subscribers"] >= p["configured_subscriber_limit"]
            for p in pon
        )
    ):
        status = "capacity_upgrade_required"
    if technology == "fiber" and relevant and installed and not free:
        status = "capacity_upgrade_required"
    return {
        "status": status,
        "preliminary": True,
        "nearby_sites": sorted(eligible, key=lambda v: v["distance_km"])[:10],
        "coverage_area_ids": [a.id for a in relevant],
        "documented_fiber_capacity": observed_capacity,
        "uncertainty": [
            "Nearby infrastructure does not establish route, connection rights, optical continuity, RF LOS, spare PON capacity or construction feasibility",
            "Inventory capacity and geographic proximity never guarantee service, price or installation date",
        ],
        "recommendation": "Operator survey and explicit qualification approval required; no provisioning",
        "available_pon_resources": {
            "ports": pon,
            "status": "documented resources only; missing subscriber/port data is unknown",
        },
    }


def import_existing(db, u, b):
    """Explicit import of stored NOC identities; never discovers network equipment."""
    sites = db.scalars(select(Site).order_by(Site.id).limit(1001)).all()
    links = db.scalars(select(NetworkLink).order_by(NetworkLink.id).limit(1001)).all()
    devices = db.scalars(select(Device).order_by(Device.id).limit(2001)).all()
    sectors = db.scalars(select(Sector).order_by(Sector.id).limit(1001)).all()
    if (
        len(sites) > 1000
        or len(links) > 1000
        or len(devices) > 2000
        or len(sectors) > 1000
    ):
        raise ValueError("Import bound exceeded; use explicit component records")
    existing = db.scalars(select(Node)).all()
    site_nodes = {n.site_id: n for n in existing if n.site_id}
    device_nodes = {
        node_device(db, n): n for n in existing if n.asset_id or n.device_id
    }
    sector_nodes = {n.sector_id: n for n in existing if n.sector_id}
    from .contracts import NodeInput, EdgeInput

    created = 0
    for site in sites:
        if site.id not in site_nodes:
            v = create_node(
                db,
                u,
                NodeInput(
                    operation_key=b.operation_key,
                    reason=b.reason,
                    kind=(
                        site.site_type
                        if site.site_type
                        in {"tower", "pop", "core", "headend", "upstream"}
                        else "pop"
                    ),
                    site_id=site.id,
                ),
            )
            site_nodes[site.id] = get(db, Node, v["id"])
            created += 1
    for d in devices:
        if d.id not in device_nodes:
            asset = db.scalar(select(Asset).where(Asset.device_id == d.id))
            create_node(
                db,
                u,
                NodeInput(
                    operation_key=b.operation_key,
                    reason=b.reason,
                    kind="equipment",
                    **({"asset_id": asset.id} if asset else {"device_id": d.id}),
                ),
            )
            created += 1
    for s in sectors:
        if s.id not in sector_nodes:
            create_node(
                db,
                u,
                NodeInput(
                    operation_key=b.operation_key,
                    reason=b.reason,
                    kind="sector",
                    sector_id=s.id,
                ),
            )
            created += 1
    oldlinks = set(
        db.scalars(
            select(Edge.network_link_id).where(Edge.network_link_id.is_not(None))
        )
    )
    for link in links:
        if link.id not in oldlinks:
            technology = (
                "fiber"
                if link.link_type == "fiber"
                else (
                    "ethernet"
                    if link.link_type == "ethernet"
                    else "microwave" if link.licensed else "wireless"
                )
            )
            create_edge(
                db,
                u,
                EdgeInput(
                    operation_key=b.operation_key,
                    reason=b.reason,
                    label=link.name,
                    a_id=site_nodes[link.a_site_id].id,
                    z_id=site_nodes[link.z_site_id].id,
                    layer="physical",
                    relation="transport",
                    technology=technology,
                    network_link_id=link.id,
                    state="planned" if link.status == "planned" else "installed",
                    evidence="Existing NOC documented link; installation/status is not verified availability",
                ),
            )
    event(
        db,
        u,
        "inventory_imported",
        b.reason,
        {"nodes_created": created, "network_calls": 0},
    )
    return {
        "nodes_created": created,
        "links_created": len(links) - len(oldlinks),
        "network_calls": 0,
        "warning": "No service paths or optical continuity inferred; reconcile source changes explicitly",
    }


def graph_view(
    db,
    technology=None,
    site_id=None,
    state=None,
    health=None,
    capacity_filter=None,
    offset=0,
):
    nodes, edges = load_graph(db)
    sites = {s.id: s for s in db.scalars(select(Site))}
    assets = {
        a.id: a
        for a in db.scalars(
            select(Asset).where(
                Asset.id.in_([n.asset_id for n in nodes.values() if n.asset_id])
            )
        )
    }
    locations = {
        l.id: l
        for l in db.scalars(
            select(Location).where(
                Location.id.in_(
                    [a.location_id for a in assets.values() if a.location_id]
                )
            )
        )
    }
    device_ids = {n.device_id for n in nodes.values() if n.device_id} | {
        a.device_id for a in assets.values() if a.device_id
    }
    devices = {
        d.id: d for d in db.scalars(select(Device).where(Device.id.in_(device_ids)))
    }
    sectors = {
        s.id: s
        for s in db.scalars(
            select(Sector).where(
                Sector.id.in_([n.sector_id for n in nodes.values() if n.sector_id])
            )
        )
    }
    services = {
        s.id: s
        for s in db.scalars(
            select(CustomerService).where(
                CustomerService.id.in_(
                    [n.service_id for n in nodes.values() if n.service_id]
                )
            )
        )
    }
    mapped = {}
    for n in nodes.values():
        a = assets.get(n.asset_id)
        d = devices.get(n.device_id or (a.device_id if a else None))
        sector = sectors.get(n.sector_id)
        service = services.get(n.service_id)
        loc = locations.get(a.location_id) if a else None
        sid = (
            n.site_id
            or (d.wisp_site_id if d else None)
            or (sector.site_id if sector else None)
            or (service.site_id if service else None)
            or (loc.site_id if loc else None)
        )
        site = sites.get(sid)
        fresh = bool(
            d
            and d.monitor_last_checked_at
            and 0 <= (now() - d.monitor_last_checked_at).total_seconds() <= 300
        )
        status = d.monitor_status if fresh else "unknown"
        lat = (
            d.latitude
            if d and d.latitude is not None
            else site.latitude if site else None
        )
        lon = (
            d.longitude
            if d and d.longitude is not None
            else site.longitude if site else None
        )
        mapped[n.id] = {
            "id": n.id,
            "identifier": n.identifier,
            "kind": n.kind,
            "state": n.state,
            "revision": n.revision,
            "site_id": sid,
            "asset_id": n.asset_id,
            "device_id": d.id if d else None,
            "sector_id": n.sector_id,
            "service_id": n.service_id,
            "label": (
                site.name
                if n.site_id and site
                else (
                    a.asset_tag
                    if a
                    else (
                        d.name
                        if d
                        else sector.name if sector else "Service#" + str(n.service_id)
                    )
                )
            ),
            "latitude": lat,
            "longitude": lon,
            "coordinate_basis": (
                "device GPS"
                if d and d.latitude is not None
                else "parent site only; exact asset/customer coordinates unavailable"
            ),
            "monitoring_state": status,
            "last_successful_check": (
                d.monitor_last_up_at.isoformat() if d and d.monitor_last_up_at else None
            ),
            "vendor": d.vendor if d else None,
            "model": d.model if d else None,
            "configured_sector_capacity_mbps": sector.capacity_mbps if sector else None,
            "sector_frequency_mhz": sector.frequency_mhz if sector else None,
            "sector_channel_width_mhz": sector.channel_width_mhz if sector else None,
        }
    allowed = {
        n["id"]
        for n in mapped.values()
        if (not site_id or n["site_id"] == site_id)
        and (not health or n["monitoring_state"] == health)
    }
    selected = [
        e
        for e in edges.values()
        if (not technology or e.technology == technology)
        and (not state or e.state == state)
        and (e.a_id in allowed or e.z_id in allowed)
    ]
    if len(selected) > 1000 and capacity_filter:
        raise ValueError("Filter technology/site before capacity filtering")
    if capacity_filter:
        caps = {r["edge_id"]: r for r in capacity(db, [e.id for e in selected])}
        selected = [
            e
            for e in selected
            if caps[e.id]["strand_capacity"] is not None
            and (
                (caps[e.id]["strand_capacity"].get("available", 0) > 0)
                == (capacity_filter == "available")
            )
        ]
    selected = sorted(selected, key=lambda e: e.id)
    page = selected[offset : offset + 1000]
    visible = {id for e in page for id in [e.a_id, e.z_id]}
    if not technology and not state and not capacity_filter:
        visible |= set(sorted(allowed)[offset : offset + 1000])
    edgeids = [e.id for e in page]
    cables = {
        c.edge_id: inv.view(c)
        for c in db.scalars(select(Cable).where(Cable.edge_id.in_(edgeids)))
    }
    alarms = db.scalars(
        select(Incident)
        .where(Incident.status.in_(["open", "acknowledged", "investigating"]))
        .order_by(Incident.id.desc())
        .limit(1001)
    ).all()
    from app.signup.models import Area

    areas = db.scalars(select(Area).order_by(Area.id).limit(201)).all()
    return {
        "nodes": [mapped[n] for n in sorted(visible)],
        "edges": [inv.view(e) | {"cable": cables.get(e.id)} for e in page],
        "total_nodes": len(nodes),
        "total_edges": len(selected),
        "next_offset": (
            offset + 1000
            if len(selected) > offset + 1000 or len(allowed) > offset + 1000
            else None
        ),
        "alarms": [
            {
                "id": i.id,
                "site_id": i.site_id,
                "device_id": i.device_id,
                "network_link_id": i.link_id,
                "severity": i.severity,
            }
            for i in alarms[:1000]
        ],
        "alarms_truncated": len(alarms) > 1000,
        "service_areas": [
            {
                "id": a.id,
                "name": a.name,
                "technology": a.technology,
                "state": a.status,
                "polygon": a.polygon,
            }
            for a in areas[:200]
        ],
        "service_areas_truncated": len(areas) > 200,
        "private": True,
        "external_map_tiles": False,
        "capacity": capacity(db, edgeids),
    }


def recommendations(db):
    nodes, edges = load_graph(db)
    rows = []
    degree = defaultdict(int)
    for e in edges.values():
        if e.layer == "physical" and e.state in {"active", "installed"}:
            degree[e.a_id] += 1
            degree[e.z_id] += 1
    for n in nodes.values():
        if degree[n.id] == 1:
            rows.append(
                {
                    "kind": "possible_single_dependency",
                    "node_id": n.id,
                    "evidence": {"documented_physical_degree": 1},
                    "confidence": "documentation_limited",
                    "recommendation": "Review service paths and shared risks; one recorded link is not proof of no alternate",
                }
            )
        if degree[n.id] == 0:
            rows.append(
                {
                    "kind": "documentation_gap",
                    "node_id": n.id,
                    "evidence": {"documented_physical_degree": 0},
                    "confidence": "documented_gap",
                    "recommendation": "Document actual transport; do not infer an outage",
                }
            )
    if len(edges) <= 1000:
        for c in capacity(db):
            if (
                c["strand_capacity"] is not None
                and c["strand_capacity"].get("available", 0) == 0
            ):
                rows.append(
                    {
                        "kind": "strand_exhaustion",
                        "edge_id": c["edge_id"],
                        "evidence": c,
                        "confidence": "documented_capacity_only",
                        "recommendation": "Review reserved/assigned strands and approved expansion",
                    }
                )
            if c["utilization_pct"] is not None and c["utilization_pct"] >= 80:
                rows.append(
                    {
                        "kind": "observed_congestion",
                        "edge_id": c["edge_id"],
                        "evidence": c,
                        "confidence": "single_fresh_sample",
                        "recommendation": "Review historical RF/capacity trends before deciding an upgrade",
                    }
                )
    modeled = impact(db)
    for circuit in modeled["circuits"]:
        paths = circuit["paths"]
        if (
            circuit["topology_complete"]
            and paths
            and not any(p["issues"] for p in paths)
        ):
            shared_nodes = set(paths[0]["node_ids"]).intersection(
                *(set(p["node_ids"]) for p in paths[1:])
            )
            shared_nodes = {
                id
                for id in shared_nodes
                if nodes[id].kind not in {"core", "headend", "upstream", "customer"}
            }
            shared_edges = set(paths[0]["edge_ids"]).intersection(
                *(set(p["edge_ids"]) for p in paths[1:])
            )
            if shared_nodes or shared_edges:
                rows.append(
                    {
                        "kind": "recorded_shared_dependency",
                        "circuit_id": circuit["circuit_id"],
                        "service_id": circuit["service_id"],
                        "evidence": {
                            "documented_path_ids": [p["id"] for p in paths],
                            "common_node_ids": sorted(shared_nodes),
                            "common_edge_ids": sorted(shared_edges),
                        },
                        "confidence": "documented_paths_only",
                        "recommendation": "Review common backhaul/equipment and physically diverse alternatives; undiscovered paths and shared ducts remain unknown",
                    }
                )
    return {
        "items": rows[:1000],
        "truncated": len(rows) > 1000,
        "automation": False,
        "limitations": [
            "Recorded degree is not a graph cut proof; hidden routes/shared ducts may exist",
            "No fabricated optical/RF values or failure probabilities",
        ],
    }


def review_circuit(db, u, id, b):
    c = get(db, Circuit, id)
    if c.revision != b.expected_revision:
        raise ValueError("Circuit changed; refresh")
    if b.topology_complete and not db.scalar(
        select(Path.id).where(Path.circuit_id == id, Path.state == "active").limit(1)
    ):
        raise ValueError("Document active paths before attesting completeness")
    c.topology_complete = b.topology_complete
    c.completeness_evidence = b.completeness_evidence
    c.revision += 1
    event(
        db,
        u,
        "circuit_reviewed",
        b.reason,
        {
            "id": id,
            "topology_complete": c.topology_complete,
            "evidence": b.completeness_evidence,
        },
    )
    return inv.view(c)


def incident_impact(db, u, id):
    """Review existing NOC-97 scope; never invent a confirmed root cause or send notifications."""
    incident = get(db, Incident, id)
    if incident.status in {"resolved", "closed"}:
        raise ValueError(
            "Historical incident; review topology rather than generating current notification candidates"
        )
    nodes, edges = load_graph(db)
    fn = set()
    fe = set()
    explicit = db.scalars(
        select(Association).where(Association.incident_id == id)
    ).all()
    fn |= {a.node_id for a in explicit if a.node_id}
    fe |= {a.edge_id for a in explicit if a.edge_id}
    if incident.device_id:
        fn |= {
            n.id
            for n in nodes.values()
            if (
                n.device_id == incident.device_id
                or (
                    n.asset_id
                    and get(db, Asset, n.asset_id).device_id == incident.device_id
                )
            )
        }
    elif incident.link_id:
        fe |= {e.id for e in edges.values() if e.network_link_id == incident.link_id}
    elif incident.site_id:
        # A customer served by a tower is not necessarily physically at that tower.
        fn |= {
            n.id
            for n in nodes.values()
            if not n.service_id and node_site(db, n) == incident.site_id
        }
    if not fn and not fe:
        return {
            "source_incident_id": id,
            "status": "insufficient_topology",
            "customer_ids_for_approval": [],
            "external_delivery": False,
            "reason": "No matching canonical component or explicit incident association",
        }
    result = impact(db, fn, fe)
    result.update(
        source_incident_id=id,
        scope_review_required=True,
        root_cause="not established by topology analysis",
    )
    event(
        db,
        u,
        "incident_impact_reviewed",
        "Operator requested stored incident customer impact review",
        {
            "incident_id": id,
            "customer_ids_for_approval": result["customer_ids_for_approval"],
            "preserved_services": result["preserved_services"],
            "external_delivery": False,
        },
    )
    return result


def map_end(db, u, b):
    end = get(db, End, b.end_id)
    strand = get(db, Strand, end.strand_id)
    if strand.revision != b.expected_strand_revision:
        raise ValueError("Strand changed; refresh")
    if b.port_id:
        p = get(db, Port, b.port_id)
        if p.node_id != end.node_id or p.kind not in {
            "fiber",
            "pon",
            "ont",
            "patch",
            "splitter_input",
            "splitter_output",
        }:
            raise ValueError("Matching physical optical port required")
        other = db.scalar(select(End.id).where(End.port_id == p.id, End.id != end.id))
        if other:
            raise ValueError("Port already terminates a different strand end")
    old = end.port_id
    end.port_id = b.port_id
    strand.revision += 1
    event(
        db,
        u,
        "strand_end_mapped",
        b.reason,
        {
            "id": end.id,
            "strand_id": strand.id,
            "previous_port_id": old,
            "port_id": b.port_id,
        },
    )
    db.flush()
    return inv.view(end) | {"strand_revision": strand.revision}
