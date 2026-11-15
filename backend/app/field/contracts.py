from datetime import datetime, timezone
from uuid import UUID
from typing import Literal
from pydantic import Field, field_validator, model_validator
from app.equipment.contracts import Reason, Replacement, AttachmentInput, BulkInput
from app.customers.contracts import SafeModel

Status = Literal[
    "open",
    "assigned",
    "scheduled",
    "in_progress",
    "waiting_for_parts",
    "blocked",
    "completed",
    "cancelled",
]


class Mutation(Reason):
    operation_key: UUID


class LabelInput(Mutation):
    site_id: int | None = Field(None, ge=1)
    asset_id: int | None = Field(None, ge=1)
    regenerate: bool = False

    @model_validator(mode="after")
    def target(self):
        if (self.site_id is None) == (self.asset_id is None):
            raise ValueError("Exactly one existing target required")
        return self


class GrantInput(Mutation):
    site_id: int = Field(ge=1)
    technician_id: int = Field(ge=1)
    active: bool = True


class Item(SafeModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,59}$")
    label: str = Field(min_length=3, max_length=200)
    group: Literal["structure", "power", "network", "site"]


class TemplateInput(Mutation):
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,59}$")
    name: str = Field(min_length=3, max_length=160)
    items: list[Item] = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def unique(self):
        if len({i.key for i in self.items}) != len(self.items):
            raise ValueError("Duplicate checklist key")
        return self


class WorkInput(Mutation):
    site_id: int = Field(ge=1)
    asset_id: int | None = Field(None, ge=1)
    incident_id: int | None = Field(None, ge=1)
    finding_id: int | None = Field(None, ge=1)
    template_id: int | None = Field(None, ge=1)
    technician_id: int | None = Field(None, ge=1)
    description: str = Field(min_length=10, max_length=4000)
    priority: Literal["low", "normal", "high", "critical"] = "normal"
    scheduled_at: datetime | None = None
    required_equipment: list[str] = Field(default_factory=list, max_length=30)


class Update(Mutation):
    expected_revision: int = Field(ge=1)
    status: Status
    notes: str = Field(min_length=10, max_length=4000)
    follow_up: str | None = Field(None, max_length=2000)
    technician_id: int | None = Field(None, ge=1)
    scheduled_at: datetime | None = None


class Note(Mutation):
    expected_revision: int = Field(ge=1)
    notes: str = Field(min_length=3, max_length=4000)


class Result(SafeModel):
    key: str = Field(max_length=60)
    outcome: Literal["pass", "fail", "not_applicable"]
    notes: str = Field("", max_length=1000)


class InspectionInput(Mutation):
    expected_revision: int = Field(ge=1)
    template_id: int = Field(ge=1)
    inspected_at: datetime
    results: list[Result] = Field(min_length=1, max_length=80)
    notes: str = Field(min_length=10, max_length=4000)

    @field_validator("inspected_at")
    @classmethod
    def instant(cls, v):
        if v.tzinfo is None or v > datetime.now(timezone.utc):
            raise ValueError("Nonfuture timezone-aware inspection time required")
        return v


class ReplacementInput(Mutation):
    expected_revision: int = Field(ge=1)
    old_asset_id: int = Field(ge=1)
    replacement: Replacement
    installation_notes: str = Field(min_length=10, max_length=2000)
    removal_notes: str = Field(min_length=10, max_length=2000)
    disposition: Literal["repair", "returned", "retired", "scrap", "review"]
    complete: bool = False
    follow_up: str | None = Field(None, max_length=2000)


class Upload(AttachmentInput):
    operation_key: UUID
    inspection_id: int | None = Field(None, ge=1)


class Parts(Mutation):
    expected_revision: int = Field(ge=1)
    movement: BulkInput
