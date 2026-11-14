from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
import re
from pydantic import Field, field_validator, model_validator, StrictBool
from app.customers.contracts import SafeModel
from app.core.operator_text import clean_operator_text

State = Literal[
    "ordered",
    "received",
    "available",
    "reserved",
    "installed",
    "in_service",
    "under_maintenance",
    "failed",
    "removed",
    "returned",
    "retired",
]
Condition = Literal["unknown", "new", "good", "refurbished", "damaged", "scrapped"]
Bucket = Literal["available", "reserved", "installed", "repair", "retired"]


def cleaned(value):
    if isinstance(value, str):
        return clean_operator_text(value)
    return value


class Reason(SafeModel):
    reason: str = Field(min_length=10, max_length=1000)

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, v):
        v = cleaned(v)
        if not v or len(v) < 10:
            raise ValueError("Meaningful credential-free reason required")
        return v


class CategoryInput(Reason):
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,49}$")
    name: str = Field(min_length=1, max_length=120)


class ProductInput(Reason):
    manufacturer: str = Field(min_length=1, max_length=120)
    category_id: int = Field(ge=1)
    model: str = Field(min_length=1, max_length=160)
    hardware_revision: str = Field("", max_length=80)
    individually_tracked: StrictBool = True
    unit: str = Field("each", pattern=r"^[a-z][a-z0-9_-]{0,19}$")
    critical: StrictBool = False
    planned_retirement_date: date | None = None
    lifecycle_source: str | None = Field(None, max_length=255)

    @model_validator(mode="after")
    def units(self):
        if self.individually_tracked and self.unit != "each":
            raise ValueError(
                "Individual assets use unit each; bulk materials require explicit units"
            )
        if self.planned_retirement_date and not self.lifecycle_source:
            raise ValueError("Document the source of a retirement planning date")
        return self


class LocationInput(Reason):
    code: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,79}$")
    name: str = Field(min_length=1, max_length=160)
    kind: Literal[
        "warehouse",
        "office",
        "vehicle",
        "technician_stock",
        "tower",
        "customer_premises",
        "repair",
        "returned",
        "retired",
    ]
    site_id: int | None = Field(None, ge=1)
    technician_id: int | None = Field(None, ge=1)
    service_id: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def context(self):
        if self.kind == "tower" and not self.site_id:
            raise ValueError("Tower location must reference the existing NOC Site")
        if self.kind == "customer_premises" and not self.service_id:
            raise ValueError("Customer location requires a reviewed service")
        return self


class AssetInput(Reason):
    product_id: int = Field(ge=1)
    asset_tag: str | None = Field(
        None, min_length=3, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]+$"
    )
    serial_number: str | None = Field(None, max_length=160)
    mac_address: str | None = Field(None, max_length=32)
    device_id: int | None = Field(None, ge=1)
    location_id: int | None = Field(None, ge=1)
    technician_id: int | None = Field(None, ge=1)
    state: State = "received"
    condition: Condition = "unknown"
    documented_operational_status: Literal[
        "unknown", "operational", "degraded", "offline", "failed"
    ] = "unknown"
    recorded_firmware: str | None = Field(None, max_length=120)
    hardware_revision: str | None = Field(None, max_length=80)
    purchase_date: date | None = None
    supplier: str | None = Field(None, max_length=160)
    warranty_end: date | None = None
    warranty_reference: str | None = Field(None, max_length=160)
    installed_at: datetime | None = None

    @field_validator("serial_number")
    @classmethod
    def serial(cls, v):
        if v is None:
            return None
        v = v.strip()
        if not v or v.lower() in {"unknown", "n/a", "none", "not available"}:
            raise ValueError("Leave unavailable serial numbers null")
        if any(ord(c) < 32 for c in v):
            raise ValueError("Serial must be a single printable identifier")
        return cleaned(v)

    @field_validator("asset_tag")
    @classmethod
    def tag(cls, v):
        return v.upper() if v else v

    @field_validator("mac_address")
    @classmethod
    def mac(cls, v):
        if v is None:
            return None
        v = v.strip().replace("-", ":").lower()
        if (
            not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", v)
            or v == "00:00:00:00:00:00"
            or int(v[:2], 16) & 1
        ):
            raise ValueError("Primary physical unicast MAC required")
        return v

    @field_validator("installed_at")
    @classmethod
    def timestamp(cls, v):
        if v and (v.tzinfo is None or v > datetime.now(timezone.utc)):
            raise ValueError("Nonfuture aware installation time required")
        return v.astimezone(timezone.utc) if v else None

    @model_validator(mode="after")
    def dates(self):
        if self.purchase_date and self.purchase_date > date.today():
            raise ValueError("Purchase date cannot be in the future")
        if (
            self.purchase_date
            and self.warranty_end
            and self.warranty_end < self.purchase_date
        ):
            raise ValueError("Warranty predates purchase")
        if (
            self.purchase_date
            and self.installed_at
            and self.installed_at.date() < self.purchase_date
        ):
            raise ValueError("Installation predates purchase")
        return self


class AssetMove(Reason):
    expected_revision: int = Field(ge=1)
    to_state: State
    to_location_id: int | None = Field(None, ge=1)
    device_id: int | None = Field(None, ge=1)
    technician_id: int | None = Field(None, ge=1)
    condition: Condition | None = None
    scrap: StrictBool = False


class Replacement(Reason):
    old_revision: int = Field(ge=1)
    new_asset_id: int = Field(ge=1)
    new_revision: int = Field(ge=1)
    removed_to_location_id: int = Field(ge=1)
    inherit_device: StrictBool = True


class BulkInput(Reason):
    event_key: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    product_id: int = Field(ge=1)
    action: Literal[
        "receive",
        "transfer",
        "install",
        "remove",
        "reserve",
        "release",
        "repair",
        "retire",
        "scrap",
    ]
    quantity: Decimal = Field(
        gt=0,
        le=Decimal("1000000000000"),
        max_digits=16,
        decimal_places=3,
        allow_inf_nan=False,
    )
    from_location_id: int | None = Field(None, ge=1)
    to_location_id: int = Field(ge=1)
    from_state: Bucket | None = None
    to_state: Bucket = "available"
    expected_source_revision: int | None = Field(None, ge=1)


class Part(SafeModel):
    product_id: int = Field(ge=1)
    quantity: Decimal = Field(
        gt=0, le=Decimal("1000000"), decimal_places=3, allow_inf_nan=False
    )
    bulk_movement_id: int | None = Field(None, ge=1)


class MaintenanceInput(Reason):
    inspected_at: datetime
    technician_id: int | None = Field(None, ge=1)
    status: Literal["planned", "in_progress", "awaiting_parts", "completed"] = (
        "completed"
    )
    condition: Condition = "unknown"
    work_performed: str = Field(min_length=10, max_length=4000)
    fault_description: str | None = Field(None, max_length=2000)
    repair_actions: str | None = Field(None, max_length=2000)
    parts: list[Part] = Field(default_factory=list, max_length=30)
    follow_up: str | None = Field(None, max_length=2000)
    tower_finding_id: int | None = Field(None, ge=1)

    @field_validator("inspected_at")
    @classmethod
    def instant(cls, v):
        if v.tzinfo is None or v > datetime.now(timezone.utc):
            raise ValueError("Nonfuture aware inspection time required")
        return v.astimezone(timezone.utc)


class FinancialInput(Reason):
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    purchase_cost: Decimal | None = Field(
        None, ge=0, le=Decimal("1000000000000"), decimal_places=2, allow_inf_nan=False
    )
    replacement_estimate: Decimal | None = Field(
        None, ge=0, le=Decimal("1000000000000"), decimal_places=2, allow_inf_nan=False
    )
    installation_labor_estimate: Decimal | None = Field(
        None, ge=0, le=Decimal("1000000000000"), decimal_places=2, allow_inf_nan=False
    )
    salvage_value: Decimal | None = Field(
        None, ge=0, le=Decimal("1000000000000"), decimal_places=2, allow_inf_nan=False
    )
    depreciation_method: Literal["straight_line_monthly"] | None = None
    useful_life_months: int | None = Field(None, ge=1, le=600)

    @model_validator(mode="after")
    def assumptions(self):
        amounts = [
            self.purchase_cost,
            self.replacement_estimate,
            self.installation_labor_estimate,
            self.salvage_value,
        ]
        if any(v is not None for v in amounts) and not self.currency:
            raise ValueError("Explicit currency required for recorded amounts")
        if (
            self.purchase_cost is not None
            and self.salvage_value is not None
            and self.salvage_value > self.purchase_cost
        ):
            raise ValueError("Salvage exceeds purchase cost")
        return self


class CostInput(Reason):
    location_id: int | None = Field(None, ge=1)
    amount: Decimal = Field(
        ge=0, le=Decimal("1000000000000"), decimal_places=2, allow_inf_nan=False
    )
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    maintenance_id: int | None = Field(None, ge=1)
    bulk_movement_id: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def one(self):
        if (self.maintenance_id is None) == (self.bulk_movement_id is None):
            raise ValueError("One maintenance or bulk receipt reference required")
        return self


class StockInput(Reason):
    product_id: int | None = Field(None, ge=1)
    category_id: int | None = Field(None, ge=1)
    location_id: int | None = Field(None, ge=1)
    unit: str = Field("each", pattern=r"^[a-z][a-z0-9_-]{0,19}$")
    minimum_available: Decimal = Field(
        ge=0, le=Decimal("1000000000000"), decimal_places=3, allow_inf_nan=False
    )
    warranty_warning_days: int = Field(60, ge=1, le=365)
    critical: StrictBool = False

    @model_validator(mode="after")
    def scope(self):
        if (self.product_id is None) == (self.category_id is None):
            raise ValueError("One product or category scope required")
        if self.unit == "each" and self.minimum_available % 1:
            raise ValueError("Individual-unit threshold must be an integer")
        return self


class AttachmentInput(Reason):
    restricted_financial: StrictBool = False
    filename: str = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_. -]+$"
    )
    media_type: Literal["image/png", "image/jpeg", "text/plain"]
    contents_base64: str = Field(min_length=1, max_length=700000)


class ImportInput(Reason):
    csv_text: str = Field(min_length=1, max_length=256000)


class Approval(Reason):
    approved: StrictBool
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class DeviceImport(Reason):
    device_ids: list[int] = Field(min_length=1, max_length=200)
    product_id: int = Field(ge=1)
    location_id: int = Field(ge=1)
    state: Literal["received", "installed", "in_service"] = "in_service"


class MetadataPatch(Reason):
    expected_revision: int = Field(ge=1)
    serial_number: str | None = Field(None, max_length=160)
    mac_address: str | None = Field(None, max_length=32)
    recorded_firmware: str | None = Field(None, max_length=120)
    hardware_revision: str | None = Field(None, max_length=80)
    purchase_date: date | None = None
    supplier: str | None = Field(None, max_length=160)
    warranty_end: date | None = None
    warranty_reference: str | None = Field(None, max_length=160)
    documented_operational_status: (
        Literal["unknown", "operational", "degraded", "offline", "failed"] | None
    ) = None


class ProductPlanning(Reason):
    critical: StrictBool | None = None
    planned_retirement_date: date | None = None
    lifecycle_source: str | None = Field(None, max_length=255)


class MaintenanceStatus(Reason):
    expected_status: Literal["planned", "in_progress", "awaiting_parts", "completed"]
    status: Literal["planned", "in_progress", "awaiting_parts", "completed"]
    follow_up: str | None = Field(None, max_length=2000)
