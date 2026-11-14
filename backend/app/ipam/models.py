"""IPAM records reference existing inventory; observations are never allocations."""

from datetime import datetime, timezone
from sqlalchemy import (
    String,
    Integer,
    DateTime,
    Boolean,
    JSON,
    ForeignKey,
    Index,
    UniqueConstraint,
    CheckConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, CIDR
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def now():
    return datetime.now(timezone.utc)


class VRF(Base):
    __tablename__ = "ipam_vrfs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    rd: Mapped[str | None] = mapped_column(String(100), nullable=True)
    purpose: Mapped[str] = mapped_column(String(255), default="")


class VLAN(Base):
    __tablename__ = "ipam_vlans"
    __table_args__ = (
        UniqueConstraint("vrf_id", "site_id", "vid", name="uq_ipam_vlan_context"),
        CheckConstraint("vid BETWEEN 1 AND 4094", name="ck_ipam_vlan_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    vrf_id: Mapped[int] = mapped_column(ForeignKey("ipam_vrfs.id"), index=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    vid: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default="documented")


class Interface(Base):
    __tablename__ = "ipam_interfaces"
    __table_args__ = (
        UniqueConstraint("device_id", "external_id", name="uq_ipam_interface_device"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(30), default="physical")
    vrf_id: Mapped[int] = mapped_column(ForeignKey("ipam_vrfs.id"), index=True)
    vlan_id: Mapped[int | None] = mapped_column(
        ForeignKey("ipam_vlans.id"), nullable=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("ipam_interfaces.id"), nullable=True
    )


class Prefix(Base):
    __tablename__ = "ipam_prefixes"
    __table_args__ = (
        UniqueConstraint("vrf_id", "network", name="uq_ipam_prefix_context"),
        CheckConstraint("kind IN ('block','subnet')", name="ck_ipam_prefix_kind"),
        Index(
            "ix_ipam_prefix_network_gist",
            "network",
            postgresql_using="gist",
            postgresql_ops={"network": "inet_ops"},
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    vrf_id: Mapped[int] = mapped_column(ForeignKey("ipam_vrfs.id"), index=True)
    network: Mapped[str] = mapped_column(CIDR)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("ipam_prefixes.id"), nullable=True, index=True
    )
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id"), nullable=True, index=True
    )
    vlan_id: Mapped[int | None] = mapped_column(
        ForeignKey("ipam_vlans.id"), nullable=True, index=True
    )
    link_id: Mapped[int | None] = mapped_column(
        ForeignKey("network_links.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="subnet")
    purpose: Mapped[str] = mapped_column(String(255), default="")
    scope: Mapped[str] = mapped_column(String(30), default="private")
    owner: Mapped[str] = mapped_column(String(120), default="documented NOC plan")
    gateway: Mapped[str | None] = mapped_column(INET, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Pool(Base):
    __tablename__ = "ipam_pools"
    id: Mapped[int] = mapped_column(primary_key=True)
    prefix_id: Mapped[int] = mapped_column(ForeignKey("ipam_prefixes.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    first: Mapped[str] = mapped_column(INET)
    last: Mapped[str] = mapped_column(INET)
    kind: Mapped[str] = mapped_column(String(30), default="dhcp")
    nat_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)


class Assignment(Base):
    __tablename__ = "ipam_assignments"
    __table_args__ = (
        Index(
            "uq_ipam_active_address",
            "vrf_id",
            "address",
            unique=True,
            postgresql_where=text("state IN ('assigned','reserved')"),
        ),
        CheckConstraint(
            "state IN ('assigned','reserved','released')",
            name="ck_ipam_assignment_state",
        ),
        CheckConstraint(
            "masklen(address) = CASE family(address) WHEN 4 THEN 32 ELSE 128 END",
            name="ck_ipam_assignment_host",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    vrf_id: Mapped[int] = mapped_column(ForeignKey("ipam_vrfs.id"), index=True)
    prefix_id: Mapped[int] = mapped_column(ForeignKey("ipam_prefixes.id"), index=True)
    address: Mapped[str] = mapped_column(INET)
    state: Mapped[str] = mapped_column(String(20), default="reserved")
    kind: Mapped[str] = mapped_column(String(30), default="static")
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id"), nullable=True, index=True
    )
    interface_id: Mapped[int | None] = mapped_column(
        ForeignKey("ipam_interfaces.id"), nullable=True
    )
    service_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_services.id"), nullable=True, index=True
    )
    pool_id: Mapped[int | None] = mapped_column(
        ForeignKey("ipam_pools.id"), nullable=True
    )
    dns_name: Mapped[str | None] = mapped_column(String(253), nullable=True)
    owner: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))


class DNSRecord(Base):
    __tablename__ = "ipam_dns_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("ipam_assignments.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(253))
    record_type: Mapped[str] = mapped_column(String(10))
    value: Mapped[str] = mapped_column(String(253))


class Observation(Base):
    __tablename__ = "ipam_observations"
    __table_args__ = (
        UniqueConstraint("batch_key", "ordinal", name="uq_ipam_observation_event"),
        Index("ix_ipam_observation_address", "vrf_id", "address", "observed_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_key: Mapped[str] = mapped_column(String(120))
    ordinal: Mapped[int] = mapped_column(Integer)
    vrf_id: Mapped[int] = mapped_column(ForeignKey("ipam_vrfs.id"), index=True)
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(30))
    address: Mapped[str | None] = mapped_column(INET, nullable=True)
    network: Mapped[str | None] = mapped_column(CIDR, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    evidence: Mapped[dict] = mapped_column(JSON)


class DiscoveryBatch(Base):
    __tablename__ = "ipam_discovery_batches"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ImportBatch(Base):
    __tablename__ = "ipam_import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    digest: Mapped[str] = mapped_column(String(64))
    rows: Mapped[list] = mapped_column(JSON)
    errors: Mapped[list] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="preview")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Change(Base):
    __tablename__ = "ipam_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(500))
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


class ReadBudget(Base):
    __tablename__ = "ipam_read_budgets"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer)
