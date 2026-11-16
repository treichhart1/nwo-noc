import re
from datetime import datetime
from typing import Literal
from pydantic import Field, field_validator, model_validator
from app.field.contracts import Mutation
from app.customers.contracts import SafeModel

STATUSES = (
    "all_systems_operational",
    "investigating",
    "service_disruption",
    "maintenance_underway",
    "monitoring_recovery",
    "resolved",
)
Status = Literal[
    "all_systems_operational",
    "investigating",
    "service_disruption",
    "maintenance_underway",
    "monitoring_recovery",
    "resolved",
]


def public_text(v):
    if any(ord(c) < 32 and c not in "\n" for c in v) or re.search(
        r"(?<!\d)\+?\d{10,15}(?!\d)|[-+]?\d{1,3}\.\d+\s*[,/ ]\s*[-+]?\d{1,3}\.\d+|[<>@]|https?://|\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[0-9a-fA-F]{1,4}:){2}|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b|[-+]?\d{1,3}\.\d{4,}",
        v,
    ):
        raise ValueError(
            "Plain public prose only; no contacts, URLs, network addresses or coordinates"
        )
    return v.strip()


class PolicyInput(Mutation):
    expected_revision: int = Field(ge=0)
    enabled: bool = False
    baseline_status: Status = "investigating"
    public_baseline_reviewed: bool = False
    stability_seconds: int = Field(300, ge=60, le=3600)
    minimum_samples: int = Field(3, ge=3, le=20)
    maximum_gap_seconds: int = Field(120, ge=30, le=300)

    @model_validator(mode="after")
    def checked(self):
        if self.enabled and not self.public_baseline_reviewed:
            raise ValueError("Explicit public baseline review required")
        if self.baseline_status in {
            "resolved",
            "maintenance_underway",
            "monitoring_recovery",
        }:
            raise ValueError("Use an approved announcement for lifecycle statuses")
        return self


class AreaInput(Mutation):
    label: str = Field(min_length=3, max_length=100)
    publicly_approved: bool
    _label = field_validator("label")(public_text)


class SourceInput(Mutation):
    incident_id: int | None = Field(None, ge=1)
    maintenance_id: int | None = Field(None, ge=1)
    work_order_id: int | None = Field(None, ge=1)
    schedule_id: int | None = Field(None, ge=1)
    service_affecting: bool = False

    @model_validator(mode="after")
    def one(self):
        if (self.incident_id is None) == (self.maintenance_id is None):
            raise ValueError(
                "One existing incident or notification maintenance source required"
            )
        return self


class DraftInput(Mutation):
    expected_version: int = Field(ge=1)
    title: str = Field(min_length=5, max_length=120)
    description: str = Field(min_length=10, max_length=1200)
    status: Status
    area_ids: list[int] = Field(min_length=1, max_length=20)
    estimated_restoration_at: datetime | None = None
    restoration_estimate_approved: bool = False
    privacy_reviewed: bool = False
    recovery_id: int | None = Field(None, ge=1)
    withdraw: bool = False
    _text = field_validator("title", "description")(public_text)

    @model_validator(mode="after")
    def safe(self):
        if not self.privacy_reviewed:
            raise ValueError("Public content/privacy review required")
        if len(set(self.area_ids)) != len(self.area_ids) or any(
            i < 1 for i in self.area_ids
        ):
            raise ValueError("Unique public areas required")
        if self.estimated_restoration_at and (
            not self.estimated_restoration_at.tzinfo
            or not self.restoration_estimate_approved
        ):
            raise ValueError("Aware explicitly approved estimate required")
        return self


class Transition(Mutation):
    expected_version: int = Field(ge=1)
    action: Literal["request_approval", "approve", "reject", "publish"]


class MessageInput(Mutation):
    kind: Literal[
        "outage",
        "update",
        "extended",
        "maintenance",
        "rescheduled",
        "cancelled",
        "restoration",
    ] = "outage"
    event_type: Literal[
        "backhaul_outage",
        "tower_outage",
        "sector_outage",
        "fiber_outage",
        "tower_power",
        "equipment_failure",
        "severe_degradation",
        "planned_maintenance",
        "restoration",
    ] = "tower_outage"
    impact_confirmed: bool = False
    recovery_id: int | None = Field(None, ge=1)
    update_number: int = Field(0, ge=0, le=100)
    due_at: datetime | None = None


class MessageDecision(Mutation):
    action: Literal["approve", "reject", "cancel"]
    fingerprint: str = Field(min_length=64, max_length=64)


class PublicAnnouncement(SafeModel):
    id: str
    title: str
    description: str
    status: Status
    areas: list[str]
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    estimated_restoration_at: datetime | None = None
    updated_at: datetime

    _public_text = field_validator("title", "description")(public_text)

    @field_validator("areas")
    @classmethod
    def public_areas(cls, values):
        return [public_text(v) for v in values]


class PublicFeed(SafeModel):
    api_version: Literal["1"] = "1"
    overall_status: Status
    last_public_update: datetime
    basis: Literal[
        "administrator-approved announcements; not live service verification"
    ] = "administrator-approved announcements; not live service verification"
    incidents: list[PublicAnnouncement]
    maintenance: list[PublicAnnouncement]
    resolved: list[PublicAnnouncement]
    next_history_offset: int | None = None
