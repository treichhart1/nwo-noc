from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, BigInteger, JSON, String, Text, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

def utcnow():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class Integration(Base):
    __tablename__ = "integrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(40), default="not_configured")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_received_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_skipped_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_item_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class Site(Base):
    __tablename__ = "sites"
    __table_args__ = (Index("ix_sites_name", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    site_type: Mapped[str] = mapped_column(String(40), default="tower")
    status: Mapped[str] = mapped_column(String(30), default="active")
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    tower_height_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    structure_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    site_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    access_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    utility_provider: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ups_present: Mapped[bool] = mapped_column(Boolean, default=False)
    generator_present: Mapped[bool] = mapped_column(Boolean, default=False)
    customer_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_devices_source_external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(30), default="uisp")
    external_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(255))
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    device_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    role: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ip_address: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mac_address: Mapped[str | None] = mapped_column(String(120), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    site_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    site_name: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    wisp_site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True)
    parent_device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpu: Mapped[float | None] = mapped_column(Float, nullable=True)
    ram: Mapped[float | None] = mapped_column(Float, nullable=True)
    frequency: Mapped[float | None] = mapped_column(Float, nullable=True)
    channel_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    uplink_capacity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    downlink_capacity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    management_vlan: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asset_tag: Mapped[str | None] = mapped_column(String(120), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(160), nullable=True)
    install_height_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    azimuth: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    monitor_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    monitor_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    monitor_port: Mapped[int] = mapped_column(Integer, default=443)
    monitor_interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    monitor_timeout_seconds: Mapped[int] = mapped_column(Integer, default=3)
    monitor_status: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    monitor_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    monitor_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    monitor_last_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    monitor_last_down_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    monitor_consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class NetworkLink(Base):
    __tablename__ = "network_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    link_type: Mapped[str] = mapped_column(String(50), default="wireless")
    status: Mapped[str] = mapped_column(String(30), default="active")
    a_site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    z_site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    a_device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    z_device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    frequency_ghz: Mapped[float | None] = mapped_column(Float, nullable=True)
    channel_width_mhz: Mapped[float | None] = mapped_column(Float, nullable=True)
    licensed: Mapped[bool] = mapped_column(Boolean, default=False)
    license_call_sign: Mapped[str | None] = mapped_column(String(80), nullable=True)
    capacity_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_signal_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_signal_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class Sector(Base):
    __tablename__ = "sectors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"), index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="active")
    technology: Mapped[str | None] = mapped_column(String(80), nullable=True)
    frequency_mhz: Mapped[float | None] = mapped_column(Float, nullable=True)
    channel_width_mhz: Mapped[float | None] = mapped_column(Float, nullable=True)
    azimuth: Mapped[float | None] = mapped_column(Float, nullable=True)
    beamwidth: Mapped[float | None] = mapped_column(Float, nullable=True)
    customer_count: Mapped[int] = mapped_column(Integer, default=0)
    capacity_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(120))
    entity_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    severity: Mapped[str] = mapped_column(String(30), default="warning", index=True)
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True)
    link_id: Mapped[int | None] = mapped_column(ForeignKey("network_links.id", ondelete="SET NULL"), nullable=True, index=True)
    customer_impact: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="scheduled", index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True)
    link_id: Mapped[int | None] = mapped_column(ForeignKey("network_links.id", ondelete="SET NULL"), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expected_customer_impact: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class TelemetrySample(Base):
    __tablename__ = "telemetry_samples"
    __table_args__ = (Index("ix_telemetry_samples_device_sampled_id", "device_id", "sampled_at", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    ram_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    packet_loss_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal_dbm: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict)


class MonitorCheck(Base):
    __tablename__ = "monitor_checks"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="tcp", server_default="tcp")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

class MonitorEvent(Base):
    __tablename__ = "monitor_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class RFCollectionRun(Base):
    """Sanitized RF ingestion history; never store tokens or device payloads."""
    __tablename__ = "rf_collection_runs"
    __table_args__ = (Index("ix_rf_collection_runs_started_at", "started_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(20))
    received: Mapped[int] = mapped_column(Integer, default=0)
    staged: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)


class IntegrationBinding(Base):
    """Durable source identity and sanitized provenance; no credentials/raw JSON."""
    __tablename__ = "integration_bindings"
    __table_args__ = (UniqueConstraint("integration", "kind", "external_id", name="uq_integration_binding"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    integration: Mapped[str] = mapped_column(ForeignKey("integrations.name"))
    kind: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(120))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), nullable=True)
    link_id: Mapped[int | None] = mapped_column(ForeignKey("network_links.id", ondelete="CASCADE"), nullable=True)
    sector_id: Mapped[int | None] = mapped_column(ForeignKey("sectors.id", ondelete="CASCADE"), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    digest: Mapped[str] = mapped_column(String(64))
    values: Mapped[dict] = mapped_column(JSON, default=dict)
