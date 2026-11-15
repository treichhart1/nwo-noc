from datetime import datetime, timezone
from typing import Literal, Annotated, Union
from uuid import UUID
from pydantic import Field, SecretStr, model_validator, field_validator
from app.customers.contracts import SafeModel
from app.field.contracts import Mutation
from app.unified.contracts import CableInput

ID = Annotated[
    str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
]
Kind = Literal[
    "node",
    "route",
    "port",
    "connection",
    "cable",
    "splice",
    "circuit",
    "assignment",
    "path",
    "reference",
    "construction",
    "as_built",
]


class Approved(SafeModel):
    infrastructure_status: Literal["installed", "commissioned", "completed"]
    external_id: ID
    revision: int = Field(ge=1, le=2147483647)
    previous_revision: int | None = Field(None, ge=1, le=2147483647)
    publication: Literal["approved", "published"]
    approval_ref: ID
    approved_at: datetime

    @field_validator("approved_at")
    @classmethod
    def aware(cls, v):
        if v.tzinfo is None or v > datetime.now(timezone.utc):
            raise ValueError("Nonfuture timezone-aware approval timestamp required")
        return v

    @model_validator(mode="after")
    def revisions(self):
        if (
            self.previous_revision is not None
            and self.previous_revision >= self.revision
        ):
            raise ValueError("Revision must advance")
        return self


class NodeRecord(Approved):
    kind: Literal["node"]
    role: Literal[
        "core",
        "headend",
        "tower",
        "pop",
        "cabinet",
        "splice_enclosure",
        "patch_panel",
        "odf",
        "olt",
        "ont",
        "splitter",
        "customer",
        "hybrid",
        "upstream",
        "equipment",
    ]
    label: str = Field(min_length=1, max_length=160)
    point: list[float] | None = Field(None, min_length=2, max_length=2)

    @field_validator("point")
    @classmethod
    def coords(cls, v):
        import math

        if v and (
            not all(math.isfinite(x) for x in v)
            or not (-180 <= v[0] <= 180 and -90 <= v[1] <= 90)
        ):
            raise ValueError("WGS84 longitude/latitude required")
        return v


class PortRecord(Approved):
    kind: Literal["port"]
    node_ref: ID
    label: str = Field(min_length=1, max_length=80)
    port_kind: Literal[
        "ethernet", "fiber", "pon", "splitter_input", "splitter_output", "ont"
    ]
    split_ratio: Literal[2, 4, 8, 16, 32, 64, 128] | None = None
    capacity_mbps: float | None = Field(None, ge=0, le=1e8, allow_inf_nan=False)
    subscriber_limit: int | None = Field(None, ge=1, le=4096)


class ConnectionRecord(Approved):
    kind: Literal["connection"]
    a_ref: ID
    z_ref: ID
    a_port_ref: ID | None = None
    z_port_ref: ID | None = None
    label: str = Field(min_length=3, max_length=160)
    layer: Literal["physical", "logical"] = "physical"
    technology: Literal["fiber", "gpon"] = "fiber"
    relation: Literal[
        "transport", "access", "pon_splitter", "splitter_ont", "uplink", "depends_on"
    ] = "transport"
    configured_capacity_mbps: float | None = Field(
        None, ge=0, le=1e8, allow_inf_nan=False
    )


class CableRecord(Approved):
    kind: Literal["cable"]
    connection_ref: ID
    code: str = Field(min_length=3, max_length=80)
    strand_count: int = Field(ge=1, le=1728)
    geometry: list[list[float]] = Field(min_length=2, max_length=2000)

    @field_validator("geometry")
    @classmethod
    def geometry_valid(cls, v):
        return CableInput.geometry_valid(v)


class EndRef(SafeModel):
    cable_ref: ID
    strand: int = Field(ge=1, le=1728)
    side: Literal["a", "z"]


class SpliceRecord(Approved):
    kind: Literal["splice"]
    node_ref: ID
    splice_kind: Literal["splice", "patch"]
    ends: list[EndRef] = Field(min_length=2, max_length=2)


class CircuitRecord(Approved):
    kind: Literal["circuit"]
    code: str = Field(min_length=3, max_length=80)
    service_node_ref: ID | None = None
    purpose: Literal[
        "residential", "business", "transport", "dark_fiber", "infrastructure"
    ]
    bandwidth_mbps: float | None = Field(None, ge=0, le=1e8, allow_inf_nan=False)


class AssignmentRecord(Approved):
    kind: Literal["assignment"]
    cable_ref: ID
    strand: int = Field(ge=1, le=1728)
    circuit_ref: ID


class HopRef(SafeModel):
    connection_ref: ID
    cable_ref: ID | None = None
    strand: int | None = Field(None, ge=1, le=1728)

    @model_validator(mode="after")
    def pair(self):
        if (self.cable_ref is None) != (self.strand is None):
            raise ValueError("Paired cable and strand required")
        return self


class PathRecord(Approved):
    kind: Literal["path"]
    circuit_ref: ID
    source_ref: ID
    destination_ref: ID
    hops: list[HopRef] = Field(min_length=1, max_length=128)


class ReferenceRecord(Approved):
    kind: Literal["reference"]
    component_kind: Literal["node", "connection"]
    component_ref: ID
    category: Literal["document", "work_order", "ipam_prefix", "ipam_interface"]


class RouteRecord(Approved):
    kind: Literal["route"]
    name: str = Field(min_length=1, max_length=160)
    geometry: list[list[float]] = Field(min_length=2, max_length=2000)
    segment_refs: list[ID] = Field(min_length=1, max_length=256)

    @field_validator("geometry")
    @classmethod
    def coords(cls, v):
        return CableInput.geometry_valid(v)


class ConstructionRecord(Approved):
    kind: Literal["construction"]
    state: Literal["completed"]
    completed_at: datetime
    component_refs: list[ID] = Field(min_length=1, max_length=256)
    document_ref: ID | None = None

    @field_validator("completed_at")
    @classmethod
    def time(cls, v):
        return Approved.aware(v)


class AsBuiltRecord(Approved):
    kind: Literal["as_built"]
    component_refs: list[ID] = Field(min_length=1, max_length=256)
    document_ref: ID | None = None


Record = Annotated[
    Union[
        NodeRecord,
        PortRecord,
        ConnectionRecord,
        CableRecord,
        SpliceRecord,
        CircuitRecord,
        AssignmentRecord,
        PathRecord,
        ReferenceRecord,
        RouteRecord,
        ConstructionRecord,
        AsBuiltRecord,
    ],
    Field(discriminator="kind"),
]


class Envelope(SafeModel):
    schema_version: Literal["nwo.fiber-operational.v1"]
    envelope_id: UUID
    instance_id: ID
    project_id: ID
    from_cursor: int = Field(ge=0, le=2147483647)
    to_cursor: int = Field(ge=1, le=2147483647)
    generated_at: datetime
    records: list[Record] = Field(min_length=1, max_length=2048)

    @field_validator("generated_at")
    @classmethod
    def time(cls, v):
        return Approved.aware(v)

    @model_validator(mode="after")
    def bounded(self):
        if self.to_cursor <= self.from_cursor:
            raise ValueError("Cursor must advance")
        keys = [(r.kind, r.external_id) for r in self.records]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate record identifiers in envelope")
        if sum(r.strand_count for r in self.records if r.kind == "cable") > 10000:
            raise ValueError(
                "Strand materialization budget exceeded; partition approved exports"
            )
        return self


class SourceInput(Mutation):
    code: str = Field(min_length=3, max_length=60, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=3, max_length=120)
    instance_id: ID
    project_id: ID


class ConfigureInput(Mutation):
    expected_revision: int = Field(ge=1)
    enabled: bool
    ingest_enabled: bool = False
    schedule_enabled: bool = False
    interval_seconds: int = Field(3600, ge=60, le=86400)
    adapter: Literal["manual", "mock"] = "manual"
    acknowledge_recovery: bool = False


class BindingInput(Mutation):
    kind: Kind
    external_id: ID
    reference_kind: Literal[
        "site",
        "asset",
        "device",
        "service",
        "node",
        "port",
        "connection",
        "cable",
        "splice",
        "circuit",
        "document",
        "work_order",
        "ipam_prefix",
        "ipam_interface",
        "assignment",
    ]
    reference_id: int = Field(ge=1)
    claim_engineering_fields: bool = False


class PreviewInput(Mutation):
    envelope: Envelope


class ApprovalInput(Mutation):
    plan_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    approve_documentation_only: Literal[True]


class CredentialInput(Mutation):
    signing_secret: SecretStr = Field(min_length=32, max_length=256)


class FixtureInput(Mutation):
    envelope: Envelope
