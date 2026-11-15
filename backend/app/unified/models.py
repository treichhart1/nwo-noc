from sqlalchemy import (
    Column as C,
    Integer as I,
    String as S,
    Boolean as B,
    DateTime as D,
    Float as F,
    JSON,
    ForeignKey as FK,
    UniqueConstraint as U,
    CheckConstraint as CK,
    Index,
)
from app.core.database import Base
from app.field.models import now, uid


class Node(Base):
    __tablename__ = "unified_nodes"
    __table_args__ = (
        CK(
            "num_nonnulls(site_id,asset_id,device_id,sector_id,service_id)=1",
            name="ck_unified_node_identity",
        ),
    )
    id = C(I, primary_key=True)
    identifier = C(S(36), unique=True, nullable=False, default=uid)
    kind = C(S(40), nullable=False)
    site_id = C(I, FK("sites.id"), unique=True)
    asset_id = C(I, FK("equipment_assets.id"), unique=True)
    device_id = C(I, FK("devices.id"), unique=True)
    sector_id = C(I, FK("sectors.id"), unique=True)
    service_id = C(I, FK("customer_services.id"), unique=True)
    state = C(S(30), nullable=False, default="installed")
    revision = C(I, nullable=False, default=1)
    created_at = C(D(timezone=True), nullable=False, default=now)


class Port(Base):
    __tablename__ = "unified_ports"
    __table_args__ = (
        U("node_id", "label", name="uq_unified_port"),
        CK(
            "split_ratio IS NULL OR split_ratio IN (2,4,8,16,32,64,128)",
            name="ck_unified_split_ratio",
        ),
    )
    id = C(I, primary_key=True)
    node_id = C(I, FK("unified_nodes.id"), nullable=False, index=True)
    label = C(S(80), nullable=False)
    kind = C(S(30), nullable=False)
    split_ratio = C(I)
    configured_capacity_mbps = C(F)
    configured_subscriber_limit = C(I)
    evidence = C(S(1000), nullable=False)
    revision = C(I, nullable=False, default=1)


class Edge(Base):
    __tablename__ = "unified_edges"
    __table_args__ = (
        U("a_id", "z_id", "layer", "label", name="uq_unified_edge"),
        CK("a_id<>z_id", name="ck_unified_edge_endpoints"),
        Index("ix_unified_edge_layer_state", "layer", "state"),
    )
    id = C(I, primary_key=True)
    label = C(S(160), nullable=False)
    a_id = C(I, FK("unified_nodes.id"), nullable=False, index=True)
    z_id = C(I, FK("unified_nodes.id"), nullable=False, index=True)
    layer = C(S(20), nullable=False)
    relation = C(S(30), nullable=False)
    technology = C(S(30), nullable=False)
    network_link_id = C(I, FK("network_links.id"), unique=True)
    a_port_id = C(I, FK("unified_ports.id"))
    z_port_id = C(I, FK("unified_ports.id"))
    state = C(S(30), nullable=False, default="planned")
    configured_capacity_mbps = C(F)
    estimated_capacity_mbps = C(F)
    evidence = C(S(1000), nullable=False)
    revision = C(I, nullable=False, default=1)


class Cable(Base):
    __tablename__ = "unified_cables"
    __table_args__ = (
        CK("strand_count BETWEEN 1 AND 1728", name="ck_unified_strand_count"),
    )
    id = C(I, primary_key=True)
    code = C(S(100), nullable=False, unique=True)
    edge_id = C(I, FK("unified_edges.id"), nullable=False, unique=True)
    strand_count = C(I, nullable=False)
    geometry = C(JSON, nullable=False, default=list)
    revision = C(I, nullable=False, default=1)


class Circuit(Base):
    __tablename__ = "unified_circuits"
    id = C(I, primary_key=True)
    code = C(S(100), nullable=False, unique=True)
    service_id = C(I, FK("customer_services.id"), index=True)
    purpose = C(S(30), nullable=False)
    bandwidth_mbps = C(F)
    state = C(S(30), nullable=False, default="planned")
    topology_complete = C(B, nullable=False, default=False)
    completeness_evidence = C(S(1000), nullable=False)
    revision = C(I, nullable=False, default=1)


class Strand(Base):
    __tablename__ = "unified_strands"
    __table_args__ = (
        U("cable_id", "number", name="uq_unified_strand_number"),
        CK("number BETWEEN 1 AND 1728", name="ck_unified_strand_number"),
        Index("ix_unified_strand_state", "cable_id", "state"),
    )
    id = C(I, primary_key=True)
    cable_id = C(I, FK("unified_cables.id"), nullable=False, index=True)
    number = C(I, nullable=False)
    state = C(S(30), nullable=False, default="available")
    circuit_id = C(I, FK("unified_circuits.id"), index=True)
    reserved_until = C(D(timezone=True))
    revision = C(I, nullable=False, default=1)


class End(Base):
    __tablename__ = "unified_strand_ends"
    __table_args__ = (
        U("strand_id", "side", name="uq_unified_strand_side"),
        CK("side IN ('a','z')", name="ck_unified_strand_side"),
    )
    id = C(I, primary_key=True)
    strand_id = C(I, FK("unified_strands.id"), nullable=False, index=True)
    side = C(S(1), nullable=False)
    node_id = C(I, FK("unified_nodes.id"), nullable=False, index=True)
    port_id = C(I, FK("unified_ports.id"))


class Join(Base):
    __tablename__ = "unified_fiber_joins"
    id = C(I, primary_key=True)
    kind = C(S(20), nullable=False)
    node_id = C(I, FK("unified_nodes.id"), nullable=False, index=True)
    evidence = C(S(1000), nullable=False)
    optical_test_document_id = C(I, FK("maintenance_documents.id"))
    optical_tested_at = C(D(timezone=True))
    active = C(B, nullable=False, default=True)


class JoinEnd(Base):
    __tablename__ = "unified_join_ends"
    end_id = C(I, FK("unified_strand_ends.id"), primary_key=True)
    join_id = C(I, FK("unified_fiber_joins.id"), nullable=False, index=True)


class Pair(Base):
    __tablename__ = "unified_fiber_pairs"
    id = C(I, primary_key=True)
    code = C(S(100), nullable=False, unique=True)
    cable_id = C(I, FK("unified_cables.id"), nullable=False, index=True)


class PairMember(Base):
    __tablename__ = "unified_pair_members"
    strand_id = C(I, FK("unified_strands.id"), primary_key=True)
    pair_id = C(I, FK("unified_fiber_pairs.id"), nullable=False, index=True)


class Path(Base):
    __tablename__ = "unified_paths"
    __table_args__ = (U("circuit_id", "name", name="uq_unified_path_name"),)
    id = C(I, primary_key=True)
    circuit_id = C(I, FK("unified_circuits.id"), nullable=False, index=True)
    name = C(S(100), nullable=False)
    state = C(S(30), nullable=False, default="planned")
    source_id = C(I, FK("unified_nodes.id"), nullable=False)
    destination_id = C(I, FK("unified_nodes.id"), nullable=False)
    inventory_snapshot = C(JSON, nullable=False, default=dict)
    verified_at = C(D(timezone=True))
    verified_until = C(D(timezone=True))
    verification_evidence = C(S(1000), nullable=False)


class Hop(Base):
    __tablename__ = "unified_path_hops"
    __table_args__ = (U("path_id", "edge_id", name="uq_unified_path_edge"),)
    path_id = C(I, FK("unified_paths.id"), primary_key=True)
    position = C(I, primary_key=True)
    edge_id = C(I, FK("unified_edges.id"), nullable=False, index=True)
    strand_id = C(I, FK("unified_strands.id"), index=True)


class Observation(Base):
    __tablename__ = "unified_observations"
    __table_args__ = (
        U("port_id", "observed_at", "source", name="uq_unified_observation"),
        Index("ix_unified_observation_latest", "port_id", "observed_at", "id"),
    )
    id = C(I, primary_key=True)
    port_id = C(I, FK("unified_ports.id"), nullable=False, index=True)
    observed_at = C(D(timezone=True), nullable=False)
    source = C(S(120), nullable=False)
    metrics = C(JSON, nullable=False)
    original = C(JSON, nullable=False)


class Association(Base):
    __tablename__ = "unified_associations"
    __table_args__ = (
        CK(
            "num_nonnulls(node_id,edge_id)=1 AND num_nonnulls(document_id,work_order_id,incident_id)=1",
            name="ck_unified_association_target",
        ),
    )
    id = C(I, primary_key=True)
    node_id = C(I, FK("unified_nodes.id"), index=True)
    edge_id = C(I, FK("unified_edges.id"), index=True)
    document_id = C(I, FK("maintenance_documents.id"))
    work_order_id = C(I, FK("field_work_orders.id"))
    incident_id = C(I, FK("incidents.id"))
    created_at = C(D(timezone=True), nullable=False, default=now)


class Event(Base):
    __tablename__ = "unified_events"
    id = C(I, primary_key=True)
    actor_id = C(I, FK("users.id"), nullable=False)
    action = C(S(80), nullable=False)
    reason = C(S(2000), nullable=False)
    data = C(JSON, nullable=False)
    created_at = C(D(timezone=True), nullable=False, default=now, index=True)


# PostgreSQL enforces allocation consistency even outside the HTTP handlers.
for cls in (Node, Edge, Circuit, Path):
    cls.__table__.append_constraint(
        CK(
            "state IN ('planned','installed','reserved','active','out_of_service')",
            name="ck_" + cls.__tablename__ + "_state",
        )
    )
Edge.__table__.append_constraint(
    CK("layer IN ('physical','logical')", name="ck_unified_edge_layer")
)
Edge.__table__.append_constraint(
    CK(
        "technology IN ('wireless','microwave','fiber','ethernet','hybrid','gpon','upstream')",
        name="ck_unified_edge_technology",
    )
)
Strand.__table__.append_constraint(
    CK(
        "(state IN ('available','out_of_service') AND circuit_id IS NULL) OR (state IN ('reserved','assigned') AND circuit_id IS NOT NULL)",
        name="ck_unified_strand_allocation",
    )
)
Index(
    "uq_unified_gpon_input",
    Edge.z_port_id,
    unique=True,
    postgresql_where=__import__("sqlalchemy").text(
        "relation IN ('pon_splitter','splitter_ont') AND state<>'out_of_service'"
    ),
)

Index(
    "uq_unified_terminal_port",
    End.port_id,
    unique=True,
    postgresql_where=__import__("sqlalchemy").text("port_id IS NOT NULL"),
)
