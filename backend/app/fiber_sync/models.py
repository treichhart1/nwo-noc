from sqlalchemy import (
    Column as C,
    Integer as I,
    String as S,
    Boolean as B,
    DateTime as D,
    JSON,
    Text,
    ForeignKey as FK,
    UniqueConstraint as U,
    CheckConstraint as CK,
    Index,
)
from app.core.database import Base
from app.field.models import now


class Source(Base):
    __tablename__ = "fiber_sync_sources"
    id = C(I, primary_key=True)
    code = C(S(60), nullable=False, unique=True)
    name = C(S(120), nullable=False)
    instance_id = C(S(120), nullable=False)
    project_id = C(S(120), nullable=False)
    adapter = C(S(20), nullable=False, default="manual")
    enabled = C(B, nullable=False, default=False)
    ingest_enabled = C(B, nullable=False, default=False)
    schedule_enabled = C(B, nullable=False, default=False)
    interval_seconds = C(I, nullable=False, default=3600)
    next_sync_at = C(D(timezone=True))
    revision = C(I, nullable=False, default=1)
    applied_cursor = C(I, nullable=False, default=0)
    recovery_required = C(B, nullable=False, default=False)
    encrypted_secret = C(Text)
    fixture = C(JSON, nullable=False, default=dict)
    last_attempt_at = C(D(timezone=True))
    last_preview_at = C(D(timezone=True))
    last_success_at = C(D(timezone=True))
    last_error_code = C(S(100))
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(D(timezone=True), nullable=False, default=now)
    __table_args__ = (
        U("instance_id", "project_id", name="uq_fiber_source_project"),
        CK("adapter IN ('manual','mock')", name="ck_fiber_source_adapter"),
        CK("interval_seconds BETWEEN 60 AND 86400", name="ck_fiber_source_interval"),
    )


class Binding(Base):
    __tablename__ = "fiber_sync_bindings"
    __table_args__ = (
        U("source_id", "kind", "external_id", name="uq_fiber_binding"),
        CK(
            "num_nonnulls(site_id,asset_id,device_id,service_id,document_id,work_order_id,prefix_id,interface_id)<=1",
            name="ck_fiber_binding_reference",
        ),
        CK(
            "num_nonnulls(node_id,port_id,edge_id,cable_id,strand_id,join_id,circuit_id,path_id,association_id)<=1",
            name="ck_fiber_binding_target",
        ),
    )
    id = C(I, primary_key=True)
    source_id = C(I, FK("fiber_sync_sources.id"), nullable=False, index=True)
    kind = C(S(30), nullable=False)
    external_id = C(S(120), nullable=False)
    claim_engineering_fields = C(B, nullable=False, default=False)
    current_revision = C(I, nullable=False, default=0)
    operational_revision = C(I, nullable=False, default=0)
    current_digest = C(S(64))
    last_target_digest = C(S(64))
    active = C(B, nullable=False, default=True)
    created_by = C(I, FK("users.id"), nullable=False)
    node_id = C(I, FK("unified_nodes.id"), index=True)
    port_id = C(I, FK("unified_ports.id"))
    edge_id = C(I, FK("unified_edges.id"), index=True)
    cable_id = C(I, FK("unified_cables.id"))
    strand_id = C(I, FK("unified_strands.id"))
    join_id = C(I, FK("unified_fiber_joins.id"))
    circuit_id = C(I, FK("unified_circuits.id"))
    path_id = C(I, FK("unified_paths.id"))
    association_id = C(I, FK("unified_associations.id"))
    site_id = C(I, FK("sites.id"))
    asset_id = C(I, FK("equipment_assets.id"))
    device_id = C(I, FK("devices.id"))
    service_id = C(I, FK("customer_services.id"))
    document_id = C(I, FK("maintenance_documents.id"))
    work_order_id = C(I, FK("field_work_orders.id"))
    prefix_id = C(I, FK("ipam_prefixes.id"))
    interface_id = C(I, FK("ipam_interfaces.id"))


class Run(Base):
    __tablename__ = "fiber_sync_runs"
    __table_args__ = (
        U("source_id", "envelope_id", "attempt", name="uq_fiber_run_attempt"),
        Index("ix_fiber_run_latest", "source_id", "created_at"),
        CK(
            "status IN ('preview','conflict','applied','rolled_back','failed','superseded')",
            name="ck_fiber_run_status",
        ),
    )
    id = C(I, primary_key=True)
    source_id = C(I, FK("fiber_sync_sources.id"), nullable=False, index=True)
    envelope_id = C(S(36), nullable=False)
    attempt = C(I, nullable=False, default=1)
    envelope_digest = C(S(64), nullable=False)
    payload = C(JSON, nullable=False)
    baseline_digest = C(S(64), nullable=False)
    plan_digest = C(S(64), nullable=False)
    report = C(JSON, nullable=False)
    status = C(S(20), nullable=False)
    actor_id = C(I, FK("users.id"), nullable=False)
    origin = C(S(20), nullable=False, default="manual")
    created_at = C(D(timezone=True), nullable=False, default=now)
    applied_at = C(D(timezone=True))
    approved_by = C(I, FK("users.id"))
    rolled_back_at = C(D(timezone=True))


class Version(Base):
    __tablename__ = "fiber_sync_versions"
    __table_args__ = (
        U("source_id", "kind", "external_id", "revision", name="uq_fiber_version"),
    )
    id = C(I, primary_key=True)
    source_id = C(I, FK("fiber_sync_sources.id"), nullable=False, index=True)
    run_id = C(I, FK("fiber_sync_runs.id"), nullable=False, index=True)
    kind = C(S(30), nullable=False)
    external_id = C(S(120), nullable=False)
    revision = C(I, nullable=False)
    digest = C(S(64), nullable=False)
    payload = C(JSON, nullable=False)
    created_at = C(D(timezone=True), nullable=False, default=now)


class Change(Base):
    __tablename__ = "fiber_sync_changes"
    id = C(I, primary_key=True)
    run_id = C(I, FK("fiber_sync_runs.id"), nullable=False, index=True)
    binding_id = C(I, FK("fiber_sync_bindings.id"), nullable=False)
    target_kind = C(S(30), nullable=False)
    target_id = C(I)
    before = C(JSON, nullable=False)
    after = C(JSON, nullable=False)
    created_at = C(D(timezone=True), nullable=False, default=now)


class Nonce(Base):
    __tablename__ = "fiber_sync_nonces"
    source_id = C(I, FK("fiber_sync_sources.id"), primary_key=True)
    nonce = C(S(36), primary_key=True)
    created_at = C(D(timezone=True), nullable=False, default=now, index=True)
