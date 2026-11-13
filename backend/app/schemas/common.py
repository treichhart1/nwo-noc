from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserOut(ORMModel):
    id: int
    username: str
    email: str
    role: str

class IntegrationOut(ORMModel):
    name: str
    status: str
    last_sync_at: datetime | None
    last_error: str | None
    last_item_count: int

class DeviceOut(ORMModel):
    id: int
    source: str
    external_id: str
    name: str
    hostname: str | None
    model: str | None
    model_name: str | None
    vendor: str | None
    category: str | None
    device_type: str | None
    role: str | None
    status: str | None
    authorized: bool
    enabled: bool
    ip_address: str | None
    mac_address: str | None
    firmware_version: str | None
    site_id: str | None
    site_name: str | None
    wisp_site_id: int | None
    parent_device_id: int | None
    latitude: float | None
    longitude: float | None
    signal: float | None
    cpu: float | None
    ram: float | None
    frequency: float | None
    channel_width: float | None
    uplink_capacity: int | None
    downlink_capacity: int | None
    management_vlan: int | None
    asset_tag: str | None
    serial_number: str | None
    install_height_ft: float | None
    azimuth: float | None
    last_seen: datetime | None
    monitor_enabled: bool
    monitor_host: str | None
    monitor_port: int
    monitor_interval_seconds: int
    monitor_timeout_seconds: int
    monitor_status: str
    monitor_latency_ms: float | None
    monitor_last_checked_at: datetime | None
    monitor_last_up_at: datetime | None
    monitor_last_down_at: datetime | None
    monitor_consecutive_failures: int
    created_at: datetime
    updated_at: datetime

class DevicePatch(BaseModel):
    wisp_site_id: int | None = None
    parent_device_id: int | None = None
    role: str | None = None
    management_vlan: int | None = None
    asset_tag: str | None = None
    serial_number: str | None = None
    install_height_ft: float | None = None
    azimuth: float | None = None
    monitor_enabled: bool | None = None
    monitor_host: str | None = None
    monitor_port: int | None = Field(default=None, ge=1, le=65535)
    monitor_interval_seconds: int | None = Field(default=None, ge=15, le=3600)
    monitor_timeout_seconds: int | None = Field(default=None, ge=1, le=30)

class SiteCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    site_type: str = "tower"
    status: str = "active"
    address: str | None = None
    city: str | None = None
    state: str | None = "OH"
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    tower_height_ft: float | None = None
    structure_owner: str | None = None
    site_contact: str | None = None
    access_instructions: str | None = None
    utility_provider: str | None = None
    ups_present: bool = False
    generator_present: bool = False
    customer_count: int = 0
    notes: str | None = None

class SiteUpdate(SiteCreate):
    pass

class SiteOut(SiteCreate, ORMModel):
    id: int
    device_count: int = 0
    active_count: int = 0
    offline_count: int = 0
    unauthorized_count: int = 0
    health_score: int = 100
    created_at: datetime
    updated_at: datetime

class NetworkLinkCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    link_type: str = "wireless"
    status: str = "active"
    a_site_id: int
    z_site_id: int
    a_device_id: int | None = None
    z_device_id: int | None = None
    vendor: str | None = None
    model: str | None = None
    frequency_ghz: float | None = None
    channel_width_mhz: float | None = None
    licensed: bool = False
    license_call_sign: str | None = None
    capacity_mbps: float | None = None
    rx_signal_a: float | None = None
    rx_signal_z: float | None = None
    notes: str | None = None

class NetworkLinkOut(NetworkLinkCreate, ORMModel):
    id: int
    a_site_name: str | None = None
    z_site_name: str | None = None
    created_at: datetime
    updated_at: datetime

class SectorCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    site_id: int
    device_id: int | None = None
    status: str = "active"
    technology: str | None = None
    frequency_mhz: float | None = None
    channel_width_mhz: float | None = None
    azimuth: float | None = None
    beamwidth: float | None = None
    customer_count: int = 0
    capacity_mbps: float | None = None
    notes: str | None = None

class SectorOut(SectorCreate, ORMModel):
    id: int
    site_name: str | None = None
    created_at: datetime
    updated_at: datetime

class DashboardOut(BaseModel):
    total_devices: int
    active_devices: int
    unauthorized_devices: int
    offline_devices: int
    warning_devices: int
    mapped_devices: int
    sites: int
    links: int = 0
    sectors: int = 0
    subscribers: int = 0
    health_score: int
    integrations: list[IntegrationOut]

class AlertOut(BaseModel):
    id: str
    severity: str
    title: str
    message: str
    device_id: int | None
    device_name: str | None
    site_name: str | None
    created_at: datetime

class TopologyNode(BaseModel):
    id: str
    label: str
    kind: str
    status: str
    site_name: str | None = None
    device_id: int | None = None

class TopologyEdge(BaseModel):
    source: str
    target: str
    label: str | None = None
    status: str | None = None

class TopologyOut(BaseModel):
    nodes: list[TopologyNode]
    edges: list[TopologyEdge]

class IncidentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    severity: str = "warning"
    site_id: int | None = None
    device_id: int | None = None
    link_id: int | None = None
    customer_impact: int = Field(default=0, ge=0)
    summary: str | None = None
    started_at: datetime | None = None

class IncidentUpdate(BaseModel):
    title: str | None = None
    severity: str | None = None
    status: str | None = None
    site_id: int | None = None
    device_id: int | None = None
    link_id: int | None = None
    customer_impact: int | None = Field(default=None, ge=0)
    summary: str | None = None
    root_cause: str | None = None
    resolution: str | None = None

class IncidentOut(ORMModel):
    id: int
    title: str
    severity: str
    status: str
    site_id: int | None
    site_name: str | None = None
    device_id: int | None
    device_name: str | None = None
    link_id: int | None
    link_name: str | None = None
    customer_impact: int
    summary: str | None
    root_cause: str | None
    resolution: str | None
    started_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime

class MaintenanceCreate(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    site_id: int | None = None
    link_id: int | None = None
    description: str | None = None
    starts_at: datetime
    ends_at: datetime
    expected_customer_impact: int = Field(default=0, ge=0)

class MaintenanceOut(MaintenanceCreate, ORMModel):
    id: int
    status: str
    site_name: str | None = None
    link_name: str | None = None
    created_at: datetime
    updated_at: datetime

class TelemetryCreate(BaseModel):
    sampled_at: datetime | None = None
    cpu_percent: float | None = None
    ram_percent: float | None = None
    rx_mbps: float | None = None
    tx_mbps: float | None = None
    latency_ms: float | None = None
    packet_loss_percent: float | None = None
    signal_dbm: float | None = None
    temperature_c: float | None = None
    raw_data: dict = Field(default_factory=dict)

class TelemetryOut(TelemetryCreate, ORMModel):
    id: int
    device_id: int
    sampled_at: datetime

class OperationsSummary(BaseModel):
    open_incidents: int
    critical_incidents: int
    impacted_customers: int
    upcoming_maintenance: int
    offline_sites: int
    degraded_links: int
    unassigned_devices: int
    stale_devices: int


class MonitorCheckOut(ORMModel):
    id: int
    device_id: int
    status: str
    latency_ms: float | None
    error: str | None
    checked_at: datetime

class MonitorEventOut(ORMModel):
    id: int
    device_id: int
    device_name: str | None = None
    site_name: str | None = None
    event_type: str
    from_status: str | None
    to_status: str
    message: str
    acknowledged: bool
    created_at: datetime

class MonitoringSummary(BaseModel):
    enabled_devices: int
    stale_devices: int
    up_devices: int
    down_devices: int
    unknown_devices: int
    average_latency_ms: float | None
    unacknowledged_events: int
    last_check_at: datetime | None
