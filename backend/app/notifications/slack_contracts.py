from typing import Literal
import re
from urllib.parse import urlsplit
from pydantic import Field, SecretStr, model_validator
from app.customers.contracts import SafeModel
from app.notifications.slack_transport import CHANNEL

EVENTS = {
    "core_outage",
    "tower_outage",
    "backhaul_outage",
    "sector_outage",
    "fiber_outage",
    "rf_degradation",
    "suspected_interference",
    "congestion",
    "temperature",
    "power_failure",
    "low_battery",
    "ospf_failure",
    "collector_failure",
    "recovery",
    "maintenance",
    "overdue_maintenance",
    "technician_assignment",
    "installation",
    "signup",
    "application_review",
    "quote_approval",
    "installation_scheduled",
    "installation_overdue",
    "installation_completed",
    "application_health",
    "backup_failure",
    "recovery_failure",
    "incident_update",
    "incident_closed",
}
ROLES = {"default", "critical", "maintenance", "rf", "technician"}


class SlackConfig(SafeModel):
    enabled: bool = False
    mode: Literal["webhook", "bot"] = "webhook"
    workspace_id: str = Field(pattern=r"^T[A-Z0-9]{8,31}$")
    app_id: str = Field(pattern=r"^A[A-Z0-9]{8,31}$")
    bot_user_id: str = Field("", max_length=40)
    approved_channels: list[str] = Field(default_factory=list, max_length=30)
    test_channels: list[str] = Field(default_factory=list, max_length=30)
    channels: dict[str, str] = Field(default_factory=dict, max_length=5)
    routing: dict[str, str] = Field(default_factory=dict, max_length=24)
    dashboard_origin: str = Field(max_length=255)
    registration_reference: str = Field(
        min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.:/ -]+$"
    )
    minimum_severity: Literal["info", "warning", "critical"] = "warning"
    cooldown_seconds: int = Field(300, ge=60, le=86400)
    max_per_minute: int = Field(20, ge=1, le=60)
    max_per_hour: int = Field(200, ge=1, le=2000)
    interactions_enabled: bool = False
    history_enabled: bool = False

    @model_validator(mode="after")
    def valid(self):
        u = urlsplit(self.dashboard_origin)
        if (
            u.scheme != "https"
            or not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
            or u.path not in {"", "/"}
            or (u.port is not None and not 1 <= u.port <= 65535)
        ):
            raise ValueError("Private HTTPS dashboard origin required")
        self.dashboard_origin = self.dashboard_origin.rstrip("/")
        if self.mode == "bot" and not re.fullmatch(
            r"U[A-Z0-9]{8,31}", self.bot_user_id
        ):
            raise ValueError("Approved bot identity required")
        if self.interactions_enabled and self.mode != "bot":
            raise ValueError("Interactions require verified bot receipts")
        if (
            any(
                not re.fullmatch(CHANNEL, x)
                for x in self.approved_channels
                + self.test_channels
                + list(self.channels.values())
            )
            or not set(self.test_channels) <= set(self.approved_channels)
            or set(self.channels) != ROLES
            or not set(self.channels.values()) <= set(self.approved_channels)
            or not set(self.routing) <= EVENTS
            or not set(self.routing.values()) <= ROLES
        ):
            raise ValueError("Explicit approved channel IDs and event routing required")
        return self


class SlackCredentials(SafeModel):
    value: SecretStr = Field(max_length=4096)


class SlackSourceRequest(SafeModel):
    kind: Literal[
        "incident",
        "monitoring",
        "tower",
        "work_order",
        "maintenance",
        "notification_maintenance",
        "signup",
    "application_review",
    "quote_approval",
    "installation_scheduled",
    "installation_overdue",
    "installation_completed",
        "worker",
        "audit",
        "plan",
    ]
    identifier: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")


class SlackApproval(SafeModel):
    confirm: bool
    reference: str = Field(
        min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.:/ -]+$"
    )
    fingerprint: str | None = Field(None, pattern=r"^[a-f0-9]{64}$")


class SlackReconcile(SlackApproval):
    timestamp: str = Field(pattern=r"^[0-9]{10,16}\.[0-9]{6}$")


class SlackMapping(SafeModel):
    user_id: int = Field(ge=1)
    channels: list[str] = Field(max_length=30)
    active: bool = False
