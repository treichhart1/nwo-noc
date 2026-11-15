from sqlalchemy import (
    Column as C,
    Integer as I,
    String as S,
    Boolean as B,
    DateTime as DT,
    Float as F,
    JSON,
    ForeignKey as FK,
    UniqueConstraint as U,
    Index,
)
from app.core.database import Base
from app.field.models import now


class IntegrationConfig(Base):
    __tablename__ = "analytics_config"
    id = C(I, primary_key=True)
    enabled = C(B, nullable=False, default=False)
    server_url = C(S(300), nullable=False, default="")
    auth_mode = C(S(20), nullable=False, default="session")
    embed_enabled = C(B, nullable=False, default=False)
    dashboards = C(JSON, nullable=False, default=list)
    export_device_ids = C(JSON, nullable=False, default=list)
    export_metrics = C(JSON, nullable=False, default=list)
    retention_days = C(I, nullable=False, default=90)
    stale_seconds = C(I, nullable=False, default=900)
    revision = C(I, nullable=False, default=1)
    updated_by = C(I, FK("users.id"), nullable=False)
    updated_at = C(DT(timezone=True), nullable=False, default=now)


class HealthCheck(Base):
    __tablename__ = "analytics_health_checks"
    id = C(I, primary_key=True)
    mode = C(S(20), nullable=False)
    status = C(S(30), nullable=False)
    result = C(JSON, nullable=False)
    config_revision = C(I, nullable=False)
    checked_by = C(I, FK("users.id"), nullable=False)
    checked_at = C(DT(timezone=True), nullable=False, default=now, index=True)


class Provision(Base):
    __tablename__ = "analytics_provisions"
    id = C(I, primary_key=True)
    digest = C(S(64), nullable=False)
    config_revision = C(I, nullable=False)
    dashboard_uids = C(JSON, nullable=False)
    state = C(S(30), nullable=False, default="exported_only")
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(DT(timezone=True), nullable=False, default=now)


class HourlyBin(Base):
    __tablename__ = "analytics_hourly_bins"
    bucket = C(DT(timezone=True), primary_key=True)
    device_id = C(I, FK("devices.id", ondelete="CASCADE"), primary_key=True)
    metric = C(S(50), primary_key=True)
    component = C(S(32), primary_key=True)
    family = C(S(20), nullable=False)
    site_id = C(I, FK("sites.id", ondelete="SET NULL"), index=True)
    sector_id = C(I, FK("sectors.id", ondelete="SET NULL"))
    link_id = C(I, FK("network_links.id", ondelete="SET NULL"))
    mapping_basis = C(S(30), nullable=False)
    valid_count = C(I, nullable=False)
    missing_count = C(I, nullable=False)
    value_sum = C(F)
    minimum = C(F)
    maximum = C(F)
    last_observed = C(DT(timezone=True), nullable=False)
    projected_at = C(DT(timezone=True), nullable=False, default=now)
    __table_args__ = (Index("ix_analytics_hourly_metric_time", "metric", "bucket"),)


class ProjectionState(Base):
    __tablename__ = "analytics_projection_state"
    id = C(I, primary_key=True)
    last_success = C(DT(timezone=True))
    last_bucket = C(DT(timezone=True))
    last_error = C(S(80))
    rows = C(I, nullable=False, default=0)


class ReportSnapshot(Base):
    __tablename__ = "analytics_report_snapshots"
    id = C(I, primary_key=True)
    digest = C(S(64), nullable=False, unique=True)
    result = C(JSON, nullable=False)
    created_by = C(I, FK("users.id"), nullable=False)
    created_at = C(DT(timezone=True), nullable=False, default=now)


class ScrapeBudget(Base):
    __tablename__ = "analytics_scrape_budget"
    id = C(I, primary_key=True)
    minute = C(DT(timezone=True), nullable=False)
    count = C(I, nullable=False)


class HourCoverage(Base):
    __tablename__ = "analytics_hour_coverage"
    bucket = C(DT(timezone=True), primary_key=True)
    complete = C(B, nullable=False, default=False)
    source_points = C(I, nullable=False, default=0)
    refreshed_at = C(DT(timezone=True), nullable=False, default=now)
