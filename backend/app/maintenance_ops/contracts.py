from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Annotated
from pydantic import Field, model_validator, StringConstraints
from app.field.contracts import Mutation
from app.equipment.contracts import MaintenanceInput

Text = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class ScheduleInput(Mutation):
    site_id: int = Field(ge=1)
    asset_id: int | None = Field(None, ge=1)
    template_id: int | None = Field(None, ge=1)
    technician_id: int | None = Field(None, ge=1)
    name: str = Field(min_length=3, max_length=160)
    maintenance_type: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,59}$")
    recurrence: Literal[
        "weekly", "monthly", "quarterly", "semiannual", "annual", "custom_days"
    ]
    custom_days: int | None = Field(None, ge=1, le=3650)
    starts_at: datetime
    lead_days: int = Field(7, ge=0, le=365)
    due_soon_days: int = Field(7, ge=0, le=365)
    overdue_days: int = Field(0, ge=0, le=365)
    priority: Literal["low", "normal", "high", "critical"] = "normal"
    estimated_minutes: int | None = Field(None, ge=1, le=14400)
    required_equipment: list[Text] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def valid(self):
        if self.starts_at.tzinfo is None:
            raise ValueError("Aware UTC anchor required")
        self.starts_at = self.starts_at.astimezone(timezone.utc)
        if (self.recurrence == "custom_days") != (self.custom_days is not None):
            raise ValueError("Custom days required only for custom recurrence")
        return self


class ScheduleUpdate(Mutation):
    expected_revision: int = Field(ge=1)
    active: bool
    technician_id: int | None = Field(None, ge=1)


class ServiceInput(Mutation):
    work_order_id: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    maintenance: MaintenanceInput
    diagnostics: str = Field(min_length=10, max_length=4000)
    labor_minutes: int | None = Field(None, ge=0, le=14400)
    warranty_claim: str | None = Field(None, max_length=500)
    repair_cost: Decimal | None = Field(
        None, ge=0, le=Decimal("1000000000"), decimal_places=2, allow_inf_nan=False
    )
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")


class CategoryInput(Mutation):
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,59}$")
    name: str = Field(min_length=3, max_length=160)


class DocumentInput(Mutation):
    site_id: int = Field(ge=1)
    asset_id: int | None = Field(None, ge=1)
    category_id: int = Field(ge=1)
    title: str = Field(min_length=3, max_length=200)
    visibility: Literal["technician", "operator", "admin"] = "technician"
    retention_days: int = Field(2555, ge=1, le=36500)


class VersionInput(Mutation):
    expected_revision: int = Field(ge=1)
    filename: str = Field(
        min_length=3, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_. -]+$"
    )
    media_type: Literal["application/pdf", "image/png", "image/jpeg", "text/plain"]
    contents_base64: str = Field(min_length=1, max_length=14000000)
    change_description: str = Field(min_length=10, max_length=1000)


class DocumentControl(Mutation):
    expected_revision: int = Field(ge=1)
    action: Literal["approve", "delete", "restore", "hold", "release_hold", "rescan"]
    version_id: int | None = Field(None, ge=1)


class AvailabilityInput(Mutation):
    technician_id: int = Field(ge=1)
    starts_at: datetime
    ends_at: datetime
    state: Literal["available", "unavailable", "leave"]
    notes: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def valid(self):
        if (
            self.starts_at.tzinfo is None
            or self.ends_at.tzinfo is None
            or self.ends_at <= self.starts_at
        ):
            raise ValueError("Valid aware interval required")
        return self
