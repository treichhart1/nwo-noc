from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import Field, field_validator, model_validator
from app.customers.contracts import SafeModel

KINDS = (
    "outage",
    "update",
    "extended",
    "maintenance",
    "rescheduled",
    "cancelled",
    "restoration",
)


class Policy(SafeModel):
    timezone: str = "America/New_York"
    dashboard_base_url: str = Field("", max_length=255)
    minimum_outage_seconds: int = Field(300, ge=60, le=86400)
    quiet_start: int = Field(22, ge=0, le=23)
    quiet_end: int = Field(8, ge=0, le=23)
    critical_quiet_bypass: bool = False
    outage_cooldown_seconds: int = Field(900, ge=60, le=86400)
    flapping_window_seconds: int = Field(300, ge=60, le=3600)
    flapping_transition_limit: int = Field(3, ge=2, le=20)
    slack_critical_channel: str = Field(
        "noc-simulation-critical", pattern=r"^[A-Za-z0-9_-]{1,80}$"
    )
    slack_maintenance_channel: str = Field(
        "noc-simulation-maintenance", pattern=r"^[A-Za-z0-9_-]{1,80}$"
    )
    max_sms_per_plan: int = Field(100, ge=1, le=1000)
    max_sms_per_minute: int = Field(20, ge=1, le=100)
    max_sms_per_hour: int = Field(200, ge=1, le=5000)
    max_sms_per_day: int = Field(500, ge=1, le=10000)
    max_plans_per_hour: int = Field(20, ge=1, le=100)
    max_per_customer_day: int = Field(3, ge=1, le=10)
    max_attempts: int = Field(3, ge=1, le=5)
    sms_price_microunits: int | None = Field(None, ge=0, le=10000000)
    daily_budget_microunits: int = Field(10000000, ge=0, le=1000000000)
    slack_channel: str = Field("noc-simulation", pattern=r"^[A-Za-z0-9_-]{1,80}$")
    slack_min_severity: Literal["info", "warning", "critical"] = "warning"
    escalation_after_seconds: int = Field(3600, ge=300, le=86400)
    escalation_channel: str = Field(
        "noc-simulation-escalation", pattern=r"^[A-Za-z0-9_-]{1,80}$"
    )
    retention_days: int = Field(30, ge=1, le=365)

    @field_validator("dashboard_base_url")
    @classmethod
    def dashboard_origin(cls, v):
        if not v:
            return v
        from app.integrations.read_only import origin

        try:
            return origin(v)
        except Exception:
            raise ValueError(
                "Dashboard URL must be an HTTPS origin without credentials"
            ) from None

    @field_validator("timezone")
    @classmethod
    def zone(cls, v):
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Known IANA timezone required") from None
        return v

    @model_validator(mode="after")
    def hours(self):
        if self.quiet_start == self.quiet_end:
            raise ValueError("Quiet-hour bounds must differ")
        return self


class Evidence(SafeModel):
    reference: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    event_type: Literal[
        "backhaul_outage",
        "severe_degradation",
        "tower_power",
        "ups_warning",
        "tower_outage",
        "ap_outage",
        "sector_outage",
        "fiber_outage",
        "equipment_failure",
        "planned_maintenance",
        "high_utilization",
        "monitoring_failure",
        "acknowledgment",
        "restoration",
    ]
    confirmed: bool = False
    recovery_verified: bool = False
    recovery_samples: int = Field(0, ge=0, le=100)
    verified_at: datetime | None = None
    rssi_dbm: float | None = Field(None, ge=-150, le=0, allow_inf_nan=False)
    snr_db: float | None = Field(None, ge=-20, le=100, allow_inf_nan=False)
    temperature_c: float | None = Field(None, ge=-80, le=150, allow_inf_nan=False)

    @field_validator("verified_at")
    @classmethod
    def aware(cls, v):
        if v and (v.tzinfo is None or v > datetime.now(timezone.utc)):
            raise ValueError("Aware nonfuture verification required")
        return v


class PlanRequest(SafeModel):
    template_key: str | None = Field(None, max_length=40)
    reminder_minutes: int | None = Field(None, ge=1, le=10080)
    incident_id: int | None = Field(None, ge=1)
    maintenance_id: int | None = Field(None, ge=1)
    kind: Literal[
        "outage",
        "update",
        "extended",
        "maintenance",
        "rescheduled",
        "cancelled",
        "restoration",
    ] = "outage"
    evidence: Evidence
    due_at: datetime | None = None
    # One operator-issued revision for updates; repeated requests dedupe.
    update_number: int = Field(0, ge=0, le=100)
    review_unknown_customer_ids: list[int] = Field(default_factory=list, max_length=100)
    critical_quiet_bypass: bool = False

    @model_validator(mode="after")
    def valid(self):
        if (self.incident_id is None) == (self.maintenance_id is None):
            raise ValueError("One source required")
        if self.review_unknown_customer_ids:
            raise ValueError(
                "Resolve unknown inventory mappings in NOC-95 before customer messaging"
            )
        if self.due_at and self.due_at.tzinfo is None:
            raise ValueError("Timezone-aware schedule required")
        return self


class TargetSpec(SafeModel):
    site_id: int | None = Field(None, ge=1)
    device_id: int | None = Field(None, ge=1)
    sector_id: int | None = Field(None, ge=1)
    link_id: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def one(self):
        if sum(v is not None for v in self.model_dump().values()) != 1:
            raise ValueError("One explicit target per entry required")
        return self


class MaintenanceRequest(SafeModel):
    targets: list[TargetSpec] = Field(default_factory=list, max_length=40)
    description: str | None = Field(None, max_length=1000)
    advance_notice_minutes: list[int] = Field(
        default_factory=lambda: [1440, 60], max_length=5
    )

    @field_validator("advance_notice_minutes")
    @classmethod
    def intervals(cls, v):
        if (
            not v
            or len(set(v)) != len(v)
            or any(isinstance(x, bool) or not 1 <= x <= 10080 for x in v)
        ):
            raise ValueError("Unique bounded reminder intervals required")
        return sorted(v, reverse=True)

    title: str = Field(min_length=1, max_length=160)
    site_id: int | None = Field(None, ge=1)
    device_id: int | None = Field(None, ge=1)
    sector_id: int | None = Field(None, ge=1)
    link_id: int | None = Field(None, ge=1)
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def valid(self):
        fields = (self.site_id, self.device_id, self.sector_id, self.link_id)
        if (self.targets and any(v is not None for v in fields)) or (
            not self.targets and sum(v is not None for v in fields) != 1
        ):
            raise ValueError("Use either one target or a reviewed list of targets")
        keys = [
            tuple(sorted(t.model_dump(exclude_none=True).items())) for t in self.targets
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate maintenance targets")
        if (
            self.starts_at.tzinfo is None
            or self.ends_at.tzinfo is None
            or self.ends_at <= self.starts_at
        ):
            raise ValueError("Ordered timezone-aware dates required")
        if (self.ends_at - self.starts_at).total_seconds() > 604800:
            raise ValueError("Maintenance window limited to seven days")
        return self
