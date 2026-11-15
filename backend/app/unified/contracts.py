from datetime import datetime, timezone
from typing import Literal, Annotated
from pydantic import Field, model_validator, field_validator
from app.field.contracts import Mutation
from app.customers.contracts import SafeModel

State = Literal["planned", "installed", "reserved", "active", "out_of_service"]
Technology = Literal[
    "wireless", "microwave", "fiber", "ethernet", "hybrid", "gpon", "upstream"
]
Number = Annotated[float, Field(ge=0, le=100000000, allow_inf_nan=False)]


class NodeInput(Mutation):
    kind: Literal[
        "core",
        "headend",
        "tower",
        "pop",
        "sector",
        "radio",
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
    site_id: int | None = Field(None, ge=1)
    asset_id: int | None = Field(None, ge=1)
    device_id: int | None = Field(None, ge=1)
    sector_id: int | None = Field(None, ge=1)
    service_id: int | None = Field(None, ge=1)
    state: State = "installed"

    @model_validator(mode="after")
    def identity(self):
        if (
            sum(
                v is not None
                for v in [
                    self.site_id,
                    self.asset_id,
                    self.device_id,
                    self.sector_id,
                    self.service_id,
                ]
            )
            != 1
        ):
            raise ValueError("Exactly one existing inventory identity required")
        if self.kind == "sector" and not self.sector_id:
            raise ValueError("Sector identity required")
        if self.kind == "customer" and not self.service_id:
            raise ValueError("Customer service identity required")
        if (
            self.kind in {"tower", "pop", "core", "headend", "upstream"}
            and not self.site_id
        ):
            raise ValueError("Existing site identity required")
        if self.kind in {
            "cabinet",
            "splice_enclosure",
            "patch_panel",
            "odf",
            "olt",
            "ont",
            "splitter",
            "radio",
            "equipment",
        } and not (self.asset_id or self.device_id):
            raise ValueError("Existing asset or device required")
        return self


class PortInput(Mutation):
    node_id: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=80)
    kind: Literal[
        "ethernet",
        "rf",
        "fiber",
        "pon",
        "splitter_input",
        "splitter_output",
        "ont",
        "patch",
    ]
    split_ratio: Literal[2, 4, 8, 16, 32, 64, 128] | None = None
    configured_capacity_mbps: Number | None = None
    configured_subscriber_limit: int | None = Field(None, ge=1, le=4096)
    evidence: str = Field(min_length=10, max_length=1000)


class EdgeInput(Mutation):
    label: str = Field(min_length=3, max_length=160)
    a_id: int = Field(ge=1)
    z_id: int = Field(ge=1)
    layer: Literal["physical", "logical"]
    relation: Literal[
        "transport", "access", "pon_splitter", "splitter_ont", "uplink", "depends_on"
    ]
    technology: Technology
    network_link_id: int | None = Field(None, ge=1)
    a_port_id: int | None = Field(None, ge=1)
    z_port_id: int | None = Field(None, ge=1)
    state: State = "planned"
    configured_capacity_mbps: Number | None = None
    estimated_capacity_mbps: Number | None = None
    evidence: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def different(self):
        if self.a_id == self.z_id:
            raise ValueError("Distinct endpoints required")
        return self


class CableInput(Mutation):
    code: str = Field(min_length=3, max_length=100)
    edge_id: int = Field(ge=1)
    strand_count: int = Field(ge=1, le=1728)
    geometry: list[list[float]] = Field(default_factory=list, max_length=2000)

    @field_validator("geometry")
    @classmethod
    def geometry_valid(cls, v):
        if v and len(v) < 2:
            raise ValueError("At least two route coordinates required")
        if any(
            len(p) != 2 or not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90) for p in v
        ):
            raise ValueError("Finite WGS84 lon/lat coordinates required")
        return v


class JoinInput(Mutation):
    kind: Literal["splice", "patch"]
    node_id: int = Field(ge=1)
    end_ids: list[int] = Field(min_length=2, max_length=2)
    evidence: str = Field(min_length=10, max_length=1000)
    optical_test_document_id: int | None = Field(None, ge=1)
    optical_tested_at: datetime | None = None

    @model_validator(mode="after")
    def test_evidence(self):
        if self.end_ids[0] == self.end_ids[1]:
            raise ValueError("Distinct strand ends required")
        if bool(self.optical_test_document_id) != bool(self.optical_tested_at):
            raise ValueError("Paired test document and timestamp required")
        if self.optical_tested_at and (
            self.optical_tested_at.tzinfo is None
            or self.optical_tested_at > datetime.now(timezone.utc)
        ):
            raise ValueError("Nonfuture UTC-aware test time required")
        return self


class PairInput(Mutation):
    code: str = Field(min_length=3, max_length=100)
    strand_ids: list[int] = Field(min_length=2, max_length=2)


class CircuitInput(Mutation):
    code: str = Field(min_length=3, max_length=100)
    service_id: int | None = Field(None, ge=1)
    purpose: Literal[
        "residential", "business", "transport", "dark_fiber", "infrastructure"
    ]
    bandwidth_mbps: Number | None = None
    state: State = "planned"
    topology_complete: bool = False
    completeness_evidence: str = Field(min_length=10, max_length=1000)


class StrandInput(Mutation):
    strand_ids: list[int] = Field(min_length=1, max_length=1728)
    expected_revisions: dict[int, int]
    action: Literal["reserve", "assign", "release", "out_of_service"]
    circuit_id: int | None = Field(None, ge=1)
    reserved_until: datetime | None = None

    @model_validator(mode="after")
    def valid(self):
        if len(set(self.strand_ids)) != len(self.strand_ids) or set(
            self.expected_revisions
        ) != set(self.strand_ids):
            raise ValueError("Unique strands and exact revision map required")
        if self.action in {"reserve", "assign"} and not self.circuit_id:
            raise ValueError("Documented circuit required")
        if self.reserved_until and (
            self.reserved_until.tzinfo is None
            or self.reserved_until <= datetime.now(timezone.utc)
        ):
            raise ValueError("Future aware reservation time required")
        return self


class HopInput(SafeModel):
    edge_id: int = Field(ge=1)
    strand_id: int | None = Field(None, ge=1)


class PathInput(Mutation):
    circuit_id: int = Field(ge=1)
    name: str = Field(min_length=3, max_length=100)
    state: State = "planned"
    source_id: int = Field(ge=1)
    destination_id: int = Field(ge=1)
    hops: list[HopInput] = Field(min_length=1, max_length=128)
    verified_until: datetime | None = None
    verification_evidence: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def validity(self):
        if self.source_id == self.destination_id or len(
            {h.edge_id for h in self.hops}
        ) != len(self.hops):
            raise ValueError("Simple nonzero path required")
        if self.verified_until and (
            self.verified_until.tzinfo is None
            or self.verified_until <= datetime.now(timezone.utc)
            or self.verified_until
            > datetime.now(timezone.utc) + __import__("datetime").timedelta(days=1)
        ):
            raise ValueError("Verification must be aware and valid at most24h")
        return self


class StateInput(Mutation):
    target: Literal["node", "edge", "circuit", "path", "join"]
    id: int = Field(ge=1)
    state: State | None = None
    expected_revision: int | None = Field(None, ge=1)
    active: bool | None = None


class AssociationInput(Mutation):
    node_id: int | None = Field(None, ge=1)
    edge_id: int | None = Field(None, ge=1)
    document_id: int | None = Field(None, ge=1)
    work_order_id: int | None = Field(None, ge=1)
    incident_id: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def targets(self):
        if (
            sum(x is not None for x in [self.node_id, self.edge_id]) != 1
            or sum(
                x is not None
                for x in [self.document_id, self.work_order_id, self.incident_id]
            )
            != 1
        ):
            raise ValueError("One component and one existing reference required")
        return self


class Metrics(SafeModel):
    status: Literal["up", "down", "unknown"] = "unknown"
    throughput_mbps: Number | None = None
    observed_capacity_mbps: Number | None = None
    rx_dbm: Annotated[float, Field(ge=-100, le=20, allow_inf_nan=False)] | None = None
    tx_dbm: Annotated[float, Field(ge=-100, le=20, allow_inf_nan=False)] | None = None
    subscribers: int | None = Field(None, ge=0, le=4096)
    packet_loss_pct: (
        Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None
    ) = None
    latency_ms: Number | None = None


class ObservationInput(Mutation):
    port_id: int = Field(ge=1)
    observed_at: datetime
    source: Literal["noc-gpon-fixture-v1", "operator-recorded-read-only"]
    metrics: Metrics

    @field_validator("observed_at")
    @classmethod
    def time(cls, v):
        if v.tzinfo is None or v > datetime.now(timezone.utc):
            raise ValueError("Nonfuture aware observation required")
        return v


class ImpactInput(SafeModel):
    failed_node_ids: list[int] = Field(default_factory=list, max_length=100)
    failed_edge_ids: list[int] = Field(default_factory=list, max_length=100)


class QualificationInput(SafeModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    technology: Literal["wireless", "fiber", "hybrid"]
    application_id: int | None = Field(None, ge=1)


class CircuitReview(Mutation):
    expected_revision: int = Field(ge=1)
    topology_complete: bool
    completeness_evidence: str = Field(min_length=10, max_length=1000)


class EndMapping(Mutation):
    end_id: int = Field(ge=1)
    port_id: int | None = Field(None, ge=1)
    expected_strand_revision: int = Field(ge=1)
