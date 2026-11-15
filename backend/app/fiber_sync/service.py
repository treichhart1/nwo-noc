"""Versioned approved exports. No network, engineering editor, inventory creation or provisioning."""

import hashlib, json
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, func, or_
from sqlalchemy.exc import IntegrityError
from app.field import service as f
from app.field.models import now, WorkOrder
from app.models.entities import User, Site, Device, AuditLog
from app.equipment.models import Asset, Location
from app.customers.models import CustomerService
from app.maintenance_ops.models import Document
from app.ipam.models import Prefix, Interface
from app.unified import models as m, service as u, contracts as c
from app.core.config import settings
from .models import Source, Binding, Run, Version, Change
from .contracts import Envelope

ENGINE = "noc106-reconcile-v1"
ORDER = {
    "node": 0,
    "port": 1,
    "connection": 2,
    "cable": 3,
    "circuit": 4,
    "assignment": 5,
    "splice": 6,
    "path": 7,
    "reference": 8,
    "route": 9,
    "construction": 10,
    "as_built": 11,
}
TARGET = {
    "node": ("node_id", m.Node),
    "port": ("port_id", m.Port),
    "connection": ("edge_id", m.Edge),
    "cable": ("cable_id", m.Cable),
    "assignment": ("strand_id", m.Strand),
    "splice": ("join_id", m.Join),
    "circuit": ("circuit_id", m.Circuit),
    "path": ("path_id", m.Path),
    "reference": ("association_id", m.Association),
}
REF = {
    "site": ("site_id", Site),
    "asset": ("asset_id", Asset),
    "device": ("device_id", Device),
    "service": ("service_id", CustomerService),
    "document": ("document_id", Document),
    "work_order": ("work_order_id", WorkOrder),
    "ipam_prefix": ("prefix_id", Prefix),
    "ipam_interface": ("interface_id", Interface),
}


class Conflict(ValueError):
    def __init__(self, code, kind=None, external_id=None):
        self.code = code
        self.kind = kind
        self.external_id = external_id
        super().__init__(code)


def digest(data):
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def plain(value):
    from uuid import UUID
    from decimal import Decimal
    import ipaddress

    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(
        value,
        (
            UUID,
            Decimal,
            ipaddress.IPv4Address,
            ipaddress.IPv6Address,
            ipaddress.IPv4Network,
            ipaddress.IPv6Network,
        ),
    ):
        return str(value)
    return value


def view(row):
    return {
        x.name: plain(getattr(row, x.name))
        for x in row.__table__.columns
        if x.name not in {"encrypted_secret", "fixture"}
    }


def get(db, cls, id):
    return f.get(db, cls, id)


def reader(user):
    if user.role not in {"admin", "operator"}:
        raise PermissionError("Private integration operator required")


def event(db, user, action, ident, details=None):
    f.audit(
        db,
        user.id,
        "fiber_sync." + action,
        "fiber_integration",
        ident,
        details or {"documentation_only": True},
    )


def body(cls, reason, **kw):
    return cls(operation_key=uuid4(), reason=reason, **kw)


def isolated():
    return (
        settings.offline_mode
        and settings.synthetic_monitoring_enabled
        and settings.app_env == "staging"
    )


def source_create(db, user, b):
    if db.scalar(select(func.count()).select_from(Source)) >= 100:
        raise Conflict("source_registry_limit")
    r = Source(
        code=b.code,
        name=b.name,
        instance_id=b.instance_id,
        project_id=b.project_id,
        created_by=user.id,
    )
    db.add(r)
    db.flush()
    return view(r)


def configure(db, user, source, b):
    if source.revision != b.expected_revision:
        raise Conflict("source_revision_changed")
    if b.adapter == "mock" and not isolated():
        raise Conflict("mock_requires_offline_synthetic_staging")
    if b.schedule_enabled and (not b.enabled or b.adapter != "mock"):
        raise Conflict(
            "schedule_requires_enabled_mock_adapter_compatibility_unverified"
        )
    if source.recovery_required and b.enabled and not b.acknowledge_recovery:
        raise Conflict("explicit_recovery_acknowledgment_required")
    for key in [
        "enabled",
        "ingest_enabled",
        "schedule_enabled",
        "interval_seconds",
        "adapter",
    ]:
        setattr(source, key, getattr(b, key))
    if b.acknowledge_recovery:
        source.recovery_required = False
    source.next_sync_at = now() if b.schedule_enabled else None
    source.revision += 1
    return view(source)


def target_snapshot(db, b):
    spec = TARGET.get(b.kind)
    if not spec or not getattr(b, spec[0]):
        return {}
    r = get(db, spec[1], getattr(b, spec[0]))
    snap = view(r)
    if b.kind == "splice":
        snap["end_ids"] = list(
            db.scalars(
                select(m.JoinEnd.end_id)
                .where(m.JoinEnd.join_id == r.id)
                .order_by(m.JoinEnd.end_id)
            )
        )
    return snap


def reference_snapshot(row):
    if isinstance(row, Device):
        keys = ["id", "external_id", "wisp_site_id", "serial_number", "mac_address"]
    elif isinstance(row, Site):
        keys = ["id", "latitude", "longitude", "site_type"]
    elif isinstance(row, Asset):
        keys = ["id", "identifier", "device_id", "location_id", "state", "revision"]
    elif isinstance(row, CustomerService):
        keys = [
            "id",
            "external_id",
            "customer_id",
            "site_id",
            "device_id",
            "sector_id",
            "status",
            "source_deleted_at",
        ]
    else:
        return view(row)
    return {k: plain(getattr(row, k)) for k in keys if hasattr(row, k)}


def lock_targets(db, source, extra=()):
    db.scalar(
        select(Source)
        .where(Source.id == source.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    bindings = db.scalars(
        select(Binding)
        .where(Binding.source_id == source.id)
        .order_by(Binding.id)
        .with_for_update()
    ).all()
    for kind, (field, cls) in TARGET.items():
        ids = {getattr(b, field) for b in bindings if getattr(b, field)} | {
            x.target_id for x in extra if x.target_kind == kind and x.target_id
        }
        if ids:
            list(
                db.scalars(
                    select(cls)
                    .where(cls.id.in_(ids))
                    .order_by(cls.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )


def baseline(db, source):
    rows = db.scalars(
        select(Binding)
        .where(Binding.source_id == source.id)
        .order_by(Binding.id)
        .limit(20001)
    ).all()
    if len(rows) > 20000:
        raise Conflict("source_binding_scope_exceeded")
    data = []
    for b in rows:
        refs = {
            key: reference_snapshot(get(db, cls, getattr(b, key)))
            for key, cls in REF.values()
            if getattr(b, key)
        }
        data.append(
            {"binding": view(b), "target": target_snapshot(db, b), "references": refs}
        )
    return digest(
        {
            "engine": ENGINE,
            "revision": source.revision,
            "cursor": source.applied_cursor,
            "recovery_required": source.recovery_required,
            "bindings": data,
        }
    )


def bind(db, user, source, b):
    existing = db.scalar(
        select(Binding).where(
            Binding.source_id == source.id,
            Binding.kind == b.kind,
            Binding.external_id == b.external_id,
        )
    )
    spec = REF.get(b.reference_kind) or TARGET.get(b.reference_kind)
    if not spec:
        raise Conflict("unsupported_reference_kind")
    ref = get(db, spec[1], b.reference_id)
    if b.claim_engineering_fields and b.reference_kind in {
        "connection",
        "cable",
        "port",
        "splice",
        "circuit",
    }:
        if db.scalar(
            select(Binding.id)
            .where(
                Binding.source_id != source.id,
                getattr(Binding, spec[0]) == b.reference_id,
                Binding.claim_engineering_fields.is_(True),
                Binding.active.is_(True),
            )
            .limit(1)
        ):
            raise Conflict("engineering_target_owned_by_other_source")
    if b.kind == "node" and b.reference_kind not in {
        "site",
        "asset",
        "device",
        "service",
        "node",
    }:
        raise Conflict("canonical_node_reference_required")
    if b.kind == "reference" and b.reference_kind not in {
        "document",
        "work_order",
        "ipam_prefix",
        "ipam_interface",
    }:
        raise Conflict("existing_operational_reference_required")
    if b.kind not in {"node", "reference"} and b.reference_kind != b.kind:
        raise Conflict("reference_kind_mismatch")
    if (
        existing
        and existing.current_revision
        and getattr(existing, spec[0]) != b.reference_id
    ):
        raise Conflict("applied_mapping_requires_reviewed_migration_not_remap")
    if existing is None:
        existing = Binding(
            source_id=source.id,
            kind=b.kind,
            external_id=b.external_id,
            created_by=user.id,
        )
        db.add(existing)
    for key, _ in REF.values():
        setattr(existing, key, None)
    if not existing.current_revision:
        for key, _ in TARGET.values():
            setattr(existing, key, None)
    setattr(existing, spec[0], ref.id)
    if b.kind == "node" and b.reference_kind == "node":
        for key in ["site_id", "asset_id", "device_id", "service_id"]:
            if getattr(ref, key):
                setattr(existing, key, getattr(ref, key))
        if ref.sector_id:
            raise Conflict("fiber_node_cannot_alias_sector")
    existing.claim_engineering_fields = b.claim_engineering_fields
    # Explicit rebind of the SAME target acknowledges the operator-reviewed current baseline.
    existing.last_target_digest = digest(target_snapshot(db, existing))
    source.revision += 1
    db.flush()
    return view(existing)


class Context:
    def __init__(self, db, user, source, reason):
        self.db = db
        self.user = user
        self.source = source
        self.reason = reason
        self.changes = []
        self.actions = []
        rows = db.scalars(
            select(Binding).where(Binding.source_id == source.id).limit(20001)
        ).all()
        if len(rows) > 20000:
            raise Conflict("source_binding_scope_exceeded")
        self.bindings = {(r.kind, r.external_id): r for r in rows}

    def binding(self, kind, external_id, required=False):
        b = self.bindings.get((kind, external_id))
        if required and b is None:
            raise Conflict("missing_operator_mapping", kind, external_id)
        if b is None:
            b = Binding(
                source_id=self.source.id,
                kind=kind,
                external_id=external_id,
                created_by=self.user.id,
                claim_engineering_fields=True,
            )
            self.db.add(b)
            self.db.flush()
            self.bindings[(kind, external_id)] = b
        return b

    def ref(self, kind, id):
        b = self.binding(kind, id, True)
        spec = TARGET[kind]
        if not getattr(b, spec[0]) or not b.active:
            raise Conflict("unresolved_or_retired_dependency", kind, id)
        return get(self.db, spec[1], getattr(b, spec[0]))

    def strand(self, cable_ref, number):
        cable = self.ref("cable", cable_ref)
        row = self.db.scalar(
            select(m.Strand).where(
                m.Strand.cable_id == cable.id, m.Strand.number == number
            )
        )
        if not row:
            raise Conflict("strand_outside_cable", "cable", cable_ref)
        return row

    def record(self, r):
        b = self.binding(r.kind, r.external_id, r.kind in {"node", "reference"})
        data = r.model_dump(mode="json")
        sha = digest(data)
        if r.revision == b.current_revision:
            if b.current_digest != sha:
                raise Conflict("same_revision_different_content", r.kind, r.external_id)
            self.actions.append(
                {
                    "kind": r.kind,
                    "external_id": r.external_id,
                    "action": (
                        "unchanged"
                        if b.operational_revision == b.current_revision
                        else "seen_but_compensated_no_reapply"
                    ),
                    "revision": r.revision,
                }
            )
            return
        if r.revision < b.current_revision or r.previous_revision != (
            b.current_revision or None
        ):
            raise Conflict("noncontiguous_record_revision", r.kind, r.external_id)
        before = {"binding": view(b), "target": target_snapshot(self.db, b)}
        if (
            before["target"]
            and b.last_target_digest
            and digest(before["target"]) != b.last_target_digest
        ):
            raise Conflict(
                "operator_target_changed_review_mapping", r.kind, r.external_id
            )
        result, created = self.materialize(r, b)
        if result is not None and r.kind in TARGET:
            field, _ = TARGET[r.kind]
            setattr(b, field, result.id)
        b.current_revision = r.revision
        b.operational_revision = r.revision
        b.current_digest = sha
        b.active = True
        self.db.flush()
        self.changes.append({"binding": b, "before": before, "created": created})
        self.actions.append(
            {
                "kind": r.kind,
                "external_id": r.external_id,
                "action": "create" if created else "reconcile",
                "revision": r.revision,
            }
        )

    def materialize(self, r, b):
        db = self.db
        user = self.user
        reason = self.reason
        old = target_snapshot(db, b)
        existing = None
        if old:
            existing = get(db, TARGET[r.kind][1], old["id"])
        if r.kind == "node":
            if b.asset_id:
                asset = get(db, Asset, b.asset_id)
                location = (
                    get(db, Location, asset.location_id) if asset.location_id else None
                )
                physically_installed = asset.state in {"installed", "in_service"} or (
                    asset.state in {"failed", "under_maintenance"}
                    and location
                    and location.kind in {"tower", "customer_premises"}
                )
                if not physically_installed:
                    raise Conflict(
                        "canonical_asset_not_installed_reconcile_inventory",
                        r.kind,
                        r.external_id,
                    )
            refs = {
                k: getattr(b, k)
                for k in ["site_id", "asset_id", "device_id", "service_id"]
                if getattr(b, k)
            }
            if len(refs) != 1:
                raise Conflict(
                    "canonical_inventory_binding_required", r.kind, r.external_id
                )
            if existing:
                if existing.kind != r.role:
                    raise Conflict("node_role_mismatch", r.kind, r.external_id)
                return existing, False
            # Reuse the existing graph annotation. Never create Site/Asset/Customer records.
            match = db.scalar(
                select(m.Node).where(
                    *[getattr(m.Node, k) == v for k, v in refs.items()]
                )
            )
            if not match and b.asset_id:
                asset = get(db, Asset, b.asset_id)
                if asset.device_id:
                    match = db.scalar(
                        select(m.Node).where(m.Node.device_id == asset.device_id)
                    )
            if not match and b.device_id:
                aid = db.scalar(select(Asset.id).where(Asset.device_id == b.device_id))
                match = (
                    db.scalar(select(m.Node).where(m.Node.asset_id == aid))
                    if aid
                    else None
                )
            if match:
                if match.kind != r.role:
                    raise Conflict("existing_node_role_conflict", r.kind, r.external_id)
                return match, False
            row = u.create_node(
                db,
                user,
                body(c.NodeInput, reason, kind=r.role, state="installed", **refs),
            )
            return get(db, m.Node, row["id"]), True
        if r.kind == "port":
            n = self.ref("node", r.node_ref)
            desired = {
                "node_id": n.id,
                "label": r.label,
                "kind": r.port_kind,
                "split_ratio": r.split_ratio,
                "configured_capacity_mbps": r.capacity_mbps,
                "configured_subscriber_limit": r.subscriber_limit,
            }
            if existing:
                if any(getattr(existing, k) != v for k, v in desired.items()):
                    raise Conflict(
                        "port_engineering_change_requires_migration",
                        r.kind,
                        r.external_id,
                    )
                return existing, False
            if db.scalar(
                select(m.Port.id).where(m.Port.node_id == n.id, m.Port.label == r.label)
            ):
                raise Conflict(
                    "existing_port_requires_explicit_binding", r.kind, r.external_id
                )
            row = u.create_port(
                db,
                user,
                body(
                    c.PortInput,
                    reason,
                    evidence=f"Designer approval {r.approval_ref}; record {r.external_id} revision {r.revision}",
                    **desired,
                ),
            )
            return get(db, m.Port, row["id"]), True
        if r.kind == "connection":
            a = self.ref("node", r.a_ref)
            z = self.ref("node", r.z_ref)
            desired = {
                "a_id": a.id,
                "z_id": z.id,
                "a_port_id": (
                    self.ref("port", r.a_port_ref).id if r.a_port_ref else None
                ),
                "z_port_id": (
                    self.ref("port", r.z_port_ref).id if r.z_port_ref else None
                ),
                "layer": r.layer,
                "relation": r.relation,
                "technology": r.technology,
            }
            if existing:
                if any(getattr(existing, k) != v for k, v in desired.items()):
                    raise Conflict(
                        "connection_rewire_requires_migration", r.kind, r.external_id
                    )
                updates = {
                    "label": r.label,
                    "configured_capacity_mbps": r.configured_capacity_mbps,
                }
                if any(getattr(existing, k) != v for k, v in updates.items()):
                    if not b.claim_engineering_fields:
                        raise Conflict(
                            "operator_owned_fields_not_claimed", r.kind, r.external_id
                        )
                    for k, v in updates.items():
                        setattr(existing, k, v)
                    existing.revision += 1
                return existing, False
            # Physical installed documentation, never observed health. Logical dependencies carry no observed state.
            row = u.create_edge(
                db,
                user,
                body(
                    c.EdgeInput,
                    reason,
                    label=r.label,
                    state="installed",
                    configured_capacity_mbps=r.configured_capacity_mbps,
                    evidence=f"Designer approval {r.approval_ref}; record {r.external_id} revision {r.revision}",
                    **desired,
                ),
            )
            return get(db, m.Edge, row["id"]), True
        if r.kind == "cable":
            edge = self.ref("connection", r.connection_ref)
            if existing:
                if (
                    existing.edge_id != edge.id
                    or existing.strand_count != r.strand_count
                ):
                    raise Conflict(
                        "cable_rewire_or_strand_count_requires_migration",
                        r.kind,
                        r.external_id,
                    )
                if existing.geometry != r.geometry:
                    if not b.claim_engineering_fields:
                        raise Conflict(
                            "operator_owned_geometry_not_claimed", r.kind, r.external_id
                        )
                    existing.geometry = r.geometry
                    existing.revision += 1
                return existing, False
            code = f"fd{self.source.id}:{r.code}"
            if len(code) > 100:
                raise Conflict("namespaced_cable_code_too_long", r.kind, r.external_id)
            row = u.create_cable(
                db,
                user,
                body(
                    c.CableInput,
                    reason,
                    code=code,
                    edge_id=edge.id,
                    strand_count=r.strand_count,
                    geometry=r.geometry,
                ),
            )
            return get(db, m.Cable, row["id"]), True
        if r.kind == "circuit":
            n = self.ref("node", r.service_node_ref) if r.service_node_ref else None
            if n and not n.service_id:
                raise Conflict(
                    "existing_powercode_service_mapping_required", r.kind, r.external_id
                )
            if existing:
                if (
                    existing.service_id != (n.service_id if n else None)
                    or existing.purpose != r.purpose
                ):
                    raise Conflict(
                        "service_ownership_change_requires_migration",
                        r.kind,
                        r.external_id,
                    )
                if existing.bandwidth_mbps != r.bandwidth_mbps:
                    if not b.claim_engineering_fields:
                        raise Conflict(
                            "operator_owned_bandwidth_not_claimed",
                            r.kind,
                            r.external_id,
                        )
                    existing.bandwidth_mbps = r.bandwidth_mbps
                    existing.revision += 1
                return existing, False
            code = f"fd{self.source.id}:{r.code}"
            if len(code) > 100:
                raise Conflict(
                    "namespaced_circuit_code_too_long", r.kind, r.external_id
                )
            row = u.circuit(
                db,
                user,
                body(
                    c.CircuitInput,
                    reason,
                    code=code,
                    service_id=n.service_id if n else None,
                    purpose=r.purpose,
                    bandwidth_mbps=r.bandwidth_mbps,
                    state=(
                        "active"
                        if r.infrastructure_status == "commissioned"
                        else "installed"
                    ),
                    topology_complete=False,
                    completeness_evidence="Imported approved documentation; completeness and functioning paths require NOC operator verification",
                ),
            )
            return get(db, m.Circuit, row["id"]), True
        if r.kind == "assignment":
            strand = self.strand(r.cable_ref, r.strand)
            circuit = self.ref("circuit", r.circuit_ref)
            if existing and existing.id != strand.id:
                raise Conflict("strand_remap_requires_migration", r.kind, r.external_id)
            if strand.state == "assigned" and strand.circuit_id == circuit.id:
                if existing:
                    return existing, False
                raise Conflict(
                    "operator_strand_assignment_requires_binding", r.kind, r.external_id
                )
            if strand.state != "available":
                raise Conflict(
                    "strand_reserved_or_assigned_by_noc", r.kind, r.external_id
                )
            u.reserve(
                db,
                user,
                body(
                    c.StrandInput,
                    reason,
                    strand_ids=[strand.id],
                    expected_revisions={strand.id: strand.revision},
                    action="assign",
                    circuit_id=circuit.id,
                ),
            )
            return strand, existing is None
        if r.kind == "splice":
            node = self.ref("node", r.node_ref)
            ends = []
            for end in r.ends:
                strand = self.strand(end.cable_ref, end.strand)
                e = db.scalar(
                    select(m.End).where(
                        m.End.strand_id == strand.id, m.End.side == end.side
                    )
                )
                ends.append(e.id)
            if existing:
                if (
                    existing.kind != r.splice_kind
                    or existing.node_id != node.id
                    or sorted(old["end_ids"]) != sorted(ends)
                    or not existing.active
                ):
                    raise Conflict(
                        "splice_change_requires_reviewed_migration",
                        r.kind,
                        r.external_id,
                    )
                return existing, False
            row = u.create_join(
                db,
                user,
                body(
                    c.JoinInput,
                    reason,
                    kind=r.splice_kind,
                    node_id=node.id,
                    end_ids=ends,
                    evidence=f"Designer approval {r.approval_ref}; record {r.external_id} revision {r.revision}",
                ),
            )
            return get(db, m.Join, row["id"]), True
        if r.kind == "path":
            circuit = self.ref("circuit", r.circuit_ref)
            source = self.ref("node", r.source_ref)
            destination = self.ref("node", r.destination_ref)
            if existing:
                # Preserve immutable historical hops and invalidate prior functioning proof.
                before = {"binding": view(b), "target": target_snapshot(db, b)}
                existing.state = "out_of_service"
                existing.verified_until = None
                self.changes.append(
                    {
                        "binding": b,
                        "before": before,
                        "created": False,
                        "target_override": ("path", existing.id),
                    }
                )
            hops = []
            for h in r.hops:
                e = self.ref("connection", h.connection_ref)
                strand = self.strand(h.cable_ref, h.strand) if h.cable_ref else None
                if strand and get(db, m.Cable, strand.cable_id).edge_id != e.id:
                    raise Conflict(
                        "path_cable_connection_mismatch", r.kind, r.external_id
                    )
                hops.append(
                    c.HopInput(edge_id=e.id, strand_id=strand.id if strand else None)
                )
            row = u.create_path(
                db,
                user,
                body(
                    c.PathInput,
                    reason,
                    circuit_id=circuit.id,
                    name=f"{r.external_id[:70]}:r{r.revision}",
                    state=(
                        "active"
                        if r.infrastructure_status == "commissioned"
                        else "installed"
                    ),
                    source_id=source.id,
                    destination_id=destination.id,
                    hops=hops,
                    verified_until=None,
                    verification_evidence="Approved as-built documentation only; functioning failover is NOT verified",
                ),
            )
            return get(db, m.Path, row["id"]), True
        if r.kind == "reference":
            component = self.ref(r.component_kind, r.component_ref)
            key = {
                "document": "document_id",
                "work_order": "work_order_id",
                "ipam_prefix": "prefix_id",
                "ipam_interface": "interface_id",
            }[r.category]
            ident = getattr(b, key)
            if not ident:
                raise Conflict(
                    "existing_noc_reference_mapping_required", r.kind, r.external_id
                )
            target = get(db, REF[r.category][1], ident)
            node = (
                component
                if r.component_kind == "node"
                else get(db, m.Node, component.a_id)
            )
            site = u.node_site(db, node)
            device = u.node_device(db, node)
            if (
                r.category == "ipam_prefix"
                and target.site_id
                and target.site_id != site
            ):
                raise Conflict("ipam_site_mismatch", r.kind, r.external_id)
            if r.category == "ipam_interface" and target.device_id != device:
                raise Conflict("ipam_device_mismatch", r.kind, r.external_id)
            if r.category.startswith("ipam_"):
                return None, False
            desired = {
                ("node_id" if r.component_kind == "node" else "edge_id"): component.id,
                key: ident,
            }
            if existing:
                if any(getattr(existing, k) != v for k, v in desired.items()):
                    raise Conflict(
                        "operational_reference_remap_requires_migration",
                        r.kind,
                        r.external_id,
                    )
                return existing, False
            row = u.association(db, user, body(c.AssociationInput, reason, **desired))
            return get(db, m.Association, row["id"]), True
        if r.kind == "route":
            for segment in r.segment_refs:
                self.ref("cable", segment)
            return None, False
        # Completed construction/as-built are immutable provenance summaries, NOT projects, costs, 811 or engineering editors.
        for ident in r.component_refs:
            matches = [
                b
                for (kind, key), b in self.bindings.items()
                if key == ident
                and kind in {"node", "connection", "cable", "circuit", "route"}
                and b.current_revision
            ]
            if len(matches) != 1:
                raise Conflict(
                    "ambiguous_or_missing_completed_component", r.kind, r.external_id
                )
        if r.document_ref:
            rb = self.binding("reference", r.document_ref, True)
            if not rb.document_id:
                raise Conflict(
                    "approved_document_reference_required", r.kind, r.external_id
                )
            d = get(db, Document, rb.document_id)
            if d.deleted_at or not d.approved_revision:
                raise Conflict("document_not_approved", r.kind, r.external_id)
        return None, False

    def apply(self, envelope):
        for r in sorted(envelope.records, key=lambda r: (ORDER[r.kind], r.external_id)):
            self.record(r)
        self.db.flush()
        for b in self.bindings.values():
            if b.current_revision:
                b.last_target_digest = digest(target_snapshot(self.db, b))
        return self.actions


def run_view(run):
    d = view(run)
    d.pop("payload")
    return d


def preview(db, user, source, envelope, reason, origin="manual", new_attempt=False):
    if not source.enabled:
        raise Conflict("integration_disabled")
    if (source.instance_id, source.project_id) != (
        envelope.instance_id,
        envelope.project_id,
    ):
        raise Conflict("source_project_mismatch")
    payload = envelope.model_dump(mode="json")
    sha = digest(payload)
    if len(json.dumps(payload).encode()) > 2097152:
        raise Conflict("export_body_budget_exceeded")
    old = db.scalar(
        select(Run)
        .where(Run.source_id == source.id, Run.envelope_id == str(envelope.envelope_id))
        .order_by(Run.attempt.desc())
        .limit(1)
    )
    if old:
        if old.envelope_digest != sha:
            raise Conflict("envelope_id_reused_for_different_content")
        if not new_attempt or old.status in {"applied", "rolled_back"}:
            return run_view(old)
        old.status = "superseded"
    base = baseline(db, source)
    report = {
        "changes": [],
        "conflicts": [],
        "dry_run": True,
        "external_calls": 0,
        "installed_records_created": False,
    }
    status = "preview"
    try:
        if source.recovery_required:
            raise Conflict("recovery_review_required")
        if envelope.from_cursor != source.applied_cursor:
            raise Conflict("source_cursor_mismatch")
        savepoint = db.begin_nested()
        try:
            context = Context(db, user, source, reason)
            report["changes"] = context.apply(envelope)
        finally:
            savepoint.rollback()
    except (Conflict, ValueError, LookupError, IntegrityError) as exc:
        status = "conflict"
        report["conflicts"] = [
            {
                "code": (
                    exc.code
                    if isinstance(exc, Conflict)
                    else "topology_or_reference_validation_failed"
                ),
                "kind": getattr(exc, "kind", None),
                "external_id": getattr(exc, "external_id", None),
            }
        ]
    # Do not put rolled-back sequence IDs into the approval token or advertised target mappings.
    token = digest({"engine": ENGINE, "manifest": sha, "baseline": base})
    run = Run(
        source_id=source.id,
        envelope_id=str(envelope.envelope_id),
        attempt=old.attempt + 1 if old else 1,
        envelope_digest=sha,
        payload=payload,
        baseline_digest=base,
        plan_digest=token,
        report=report,
        status=status,
        actor_id=user.id,
        origin=origin,
    )
    db.add(run)
    source.last_attempt_at = now()
    source.last_preview_at = now()
    source.last_error_code = (
        report["conflicts"][0]["code"] if report["conflicts"] else None
    )
    db.flush()
    event(
        db,
        user,
        "preview_" + status,
        run.id,
        {
            "source_id": source.id,
            "origin": origin,
            "record_count": len(envelope.records),
        },
    )
    return run_view(run)


def approve(db, user, run, b):
    f.admin(user)
    if run.status == "applied":
        return run_view(run)
    source = get(db, Source, run.source_id)
    lock_targets(db, source)
    if not source.enabled or source.recovery_required or run.status != "preview":
        raise Conflict("preview_not_approvable")
    if run.plan_digest != b.plan_digest or baseline(db, source) != run.baseline_digest:
        raise Conflict("stale_preview_repreview_required")
    envelope = Envelope.model_validate(run.payload)
    if source.applied_cursor != envelope.from_cursor:
        raise Conflict("source_cursor_mismatch")
    # A savepoint isolates ALL topology writes while retaining a reviewable failed run.
    savepoint = db.begin_nested()
    try:
        context = Context(db, user, source, b.reason)
        context.apply(envelope)
        for record in envelope.records:
            if db.scalar(
                select(Version.id).where(
                    Version.source_id == source.id,
                    Version.kind == record.kind,
                    Version.external_id == record.external_id,
                    Version.revision == record.revision,
                )
            ):
                continue
            db.add(
                Version(
                    source_id=source.id,
                    run_id=run.id,
                    kind=record.kind,
                    external_id=record.external_id,
                    revision=record.revision,
                    digest=digest(record.model_dump(mode="json")),
                    payload=record.model_dump(mode="json"),
                )
            )
        for change in context.changes:
            bind = change["binding"]
            kind, id = change.get("target_override") or (
                bind.kind,
                getattr(bind, TARGET[bind.kind][0]) if bind.kind in TARGET else None,
            )
            if change.get("target_override"):
                row = get(db, TARGET[kind][1], id)
                after = view(row)
            else:
                after = target_snapshot(db, bind)
            db.add(
                Change(
                    run_id=run.id,
                    binding_id=bind.id,
                    target_kind=kind,
                    target_id=id,
                    before={**change["before"], "created": change["created"]},
                    after={"binding": view(bind), "target": after},
                )
            )
        db.flush()
        savepoint.commit()
    except (Conflict, ValueError, LookupError, IntegrityError) as exc:
        savepoint.rollback()
        run.status = "failed"
        run.report = {
            **run.report,
            "failure_code": (
                exc.code if isinstance(exc, Conflict) else "atomic_apply_failed"
            ),
        }
        source.last_error_code = run.report["failure_code"]
        event(db, user, "apply_failed", run.id, {"code": source.last_error_code})
        return run_view(run)
    source.applied_cursor = envelope.to_cursor
    source.last_success_at = now()
    source.last_error_code = None
    run.status = "applied"
    run.applied_at = now()
    run.approved_by = user.id
    event(
        db,
        user,
        "approved_applied",
        run.id,
        {
            "source_id": source.id,
            "cursor": source.applied_cursor,
            "no_provisioning": True,
        },
    )
    return run_view(run)


def change_snapshot(db, change):
    if change.target_id is None or change.target_kind not in TARGET:
        return {}
    cls = TARGET[change.target_kind][1]
    r = get(db, cls, change.target_id)
    result = view(r)
    if change.target_kind == "splice":
        result["end_ids"] = list(
            db.scalars(
                select(m.JoinEnd.end_id)
                .where(m.JoinEnd.join_id == r.id)
                .order_by(m.JoinEnd.end_id)
            )
        )
    return result


def rollback_preview(db, user, run):
    if run.status != "applied":
        raise Conflict("only_applied_run_can_be_compensated")
    source = get(db, Source, run.source_id)
    envelope = Envelope.model_validate(run.payload)
    if source.applied_cursor != envelope.to_cursor:
        raise Conflict("rollback_latest_applied_cursor_only")
    changes = db.scalars(
        select(Change).where(Change.run_id == run.id).order_by(Change.id)
    ).all()
    if not changes:
        raise Conflict("no_materialized_changes")
    own_edges = {x.target_id for x in changes if x.target_kind == "connection"}
    own_paths = {x.target_id for x in changes if x.target_kind == "path"}
    own_nodes = {
        x.target_id
        for x in changes
        if x.target_kind == "node" and x.before.get("created")
    }
    own_circuits = {
        x.target_id
        for x in changes
        if x.target_kind == "circuit" and x.before.get("created")
    }
    own_strands = {x.target_id for x in changes if x.target_kind == "assignment"}
    conflicts = []
    for change in changes:
        if change_snapshot(db, change) != change.after["target"]:
            conflicts.append(
                {"code": "target_changed_since_apply", "change_id": change.id}
            )
    # Never deactivate infrastructure subsequently used outside this approved import.
    if own_nodes and db.scalar(
        select(m.Edge.id)
        .where(
            or_(m.Edge.a_id.in_(own_nodes), m.Edge.z_id.in_(own_nodes)),
            m.Edge.id.not_in(own_edges),
            m.Edge.state != "out_of_service",
        )
        .limit(1)
    ):
        conflicts.append({"code": "node_used_by_other_operational_links"})
    if own_nodes and db.scalar(
        select(Binding.id)
        .where(
            Binding.source_id != source.id,
            Binding.node_id.in_(own_nodes),
            Binding.active.is_(True),
        )
        .limit(1)
    ):
        conflicts.append({"code": "node_reused_by_other_source"})
    if own_edges and db.scalar(
        select(m.Hop.path_id)
        .join(m.Path, m.Path.id == m.Hop.path_id)
        .where(
            m.Hop.edge_id.in_(own_edges),
            m.Path.id.not_in(own_paths),
            m.Path.state == "active",
        )
        .limit(1)
    ):
        conflicts.append({"code": "connection_used_by_other_active_path"})
    if own_circuits and db.scalar(
        select(m.Strand.id)
        .where(m.Strand.circuit_id.in_(own_circuits), m.Strand.id.not_in(own_strands))
        .limit(1)
    ):
        conflicts.append({"code": "circuit_reused_by_other_assignments"})
    if own_circuits and db.scalar(
        select(m.Path.id)
        .where(
            m.Path.circuit_id.in_(own_circuits),
            m.Path.id.not_in(own_paths),
            m.Path.state == "active",
        )
        .limit(1)
    ):
        conflicts.append({"code": "circuit_reused_by_other_active_paths"})
    # Bound snapshots also fence local strand reservations and optical terminations.
    cable_ids = [
        x.target_id
        for x in changes
        if x.target_kind == "cable" and x.before.get("created")
    ]
    if cable_ids and db.scalar(
        select(m.End.id)
        .join(m.Strand, m.Strand.id == m.End.strand_id)
        .where(m.Strand.cable_id.in_(cable_ids), m.End.port_id.is_not(None))
        .limit(1)
    ):
        conflicts.append({"code": "operator_optical_terminations_block_compensation"})
    assigned = (
        db.scalars(
            select(m.Strand).where(
                m.Strand.cable_id.in_(cable_ids), m.Strand.state != "available"
            )
        ).all()
        if cable_ids
        else []
    )
    if any(s.id not in own_strands for s in assigned):
        conflicts.append({"code": "new_cable_contains_operator_allocations"})
    token = digest(
        {
            "run": run.id,
            "baseline": baseline(db, source),
            "changes": [
                {"id": x.id, "current": change_snapshot(db, x)} for x in changes
            ],
        }
    )
    return {
        "id": run.id,
        "plan_digest": token,
        "conflicts": conflicts,
        "compensation_only": True,
        "records_deleted": False,
        "cursor_will_not_rewind": True,
    }


def rollback(db, user, run, b):
    f.admin(user)
    if run.status == "rolled_back":
        return run_view(run)
    lock_targets(
        db,
        get(db, Source, run.source_id),
        db.scalars(select(Change).where(Change.run_id == run.id)).all(),
    )
    plan = rollback_preview(db, user, run)
    if plan["plan_digest"] != b.plan_digest or plan["conflicts"]:
        raise Conflict("rollback_stale_or_reused_infrastructure_requires_review")
    changes = db.scalars(
        select(Change).where(Change.run_id == run.id).order_by(Change.id)
    ).all()
    # Stop recorded paths before touching assignments/joins. No equipment operations.
    for x in changes:
        if x.target_kind == "path" and x.target_id:
            p = get(db, m.Path, x.target_id)
            p.state = "out_of_service"
            p.verified_until = None
    db.flush()
    for x in reversed(changes):
        if x.target_id is None or x.target_kind not in TARGET:
            continue
        obj = get(db, TARGET[x.target_kind][1], x.target_id)
        if x.before.get("created"):
            if x.target_kind in {"node", "connection", "circuit", "path"}:
                obj.state = "out_of_service"
                if hasattr(obj, "revision"):
                    obj.revision += 1
            elif x.target_kind == "assignment":
                u.reserve(
                    db,
                    user,
                    body(
                        c.StrandInput,
                        b.reason,
                        strand_ids=[obj.id],
                        expected_revisions={obj.id: obj.revision},
                        action="release",
                    ),
                )
            elif x.target_kind == "splice" and obj.active:
                u.state_change(
                    db,
                    user,
                    body(
                        c.StateInput, b.reason, target="join", id=obj.id, active=False
                    ),
                )
            elif x.target_kind == "cable":
                strands = db.scalars(
                    select(m.Strand)
                    .where(m.Strand.cable_id == obj.id)
                    .order_by(m.Strand.id)
                    .with_for_update()
                ).all()
                for strand in strands:
                    if strand.state not in {"available", "out_of_service"}:
                        raise Conflict("operator_allocation_blocks_compensation")
                    strand.state = "out_of_service"
                    strand.revision += 1
            # Ports and relationship references remain as nondeleted historical documentation.
        else:
            before = x.before["target"]
            if x.target_kind == "connection":
                keys = ["label", "configured_capacity_mbps"]
            elif x.target_kind == "cable":
                keys = ["geometry"]
            elif x.target_kind == "circuit":
                keys = ["bandwidth_mbps"]
            elif x.target_kind == "path":
                keys = ["state"]
            else:
                keys = []
            for k in keys:
                setattr(obj, k, before[k])
            if keys and hasattr(obj, "revision"):
                obj.revision += 1
            if x.target_kind == "path":
                obj.verified_until = (
                    None  # A rollback can NEVER revive old functioning proof.
                )
    # Restore the previous path target when superseding revisions; do not rewind source versions/cursor.
    for x in reversed(changes):
        bind = get(db, Binding, x.binding_id)
        for key, _ in TARGET.values():
            previous = x.before["binding"].get(key)
            if previous is not None:
                setattr(bind, key, previous)
        bind.operational_revision = x.before["binding"].get("operational_revision", 0)
        bind.active = bool(
            bind.operational_revision and x.before["binding"].get("active", False)
        )
    db.flush()
    source = get(db, Source, run.source_id)
    source.recovery_required = True
    source.schedule_enabled = False
    source.revision += 1
    source.last_error_code = "review_required_after_compensation"
    for bind in db.scalars(select(Binding).where(Binding.source_id == source.id)):
        if bind.current_revision:
            bind.last_target_digest = digest(target_snapshot(db, bind))
    run.status = "rolled_back"
    run.rolled_back_at = now()
    event(
        db,
        user,
        "reviewed_compensation",
        run.id,
        {
            "source_cursor_retained": source.applied_cursor,
            "no_deletions": True,
            "no_provisioning": True,
        },
    )
    return run_view(run)


def sources(db, user):
    reader(user)
    result = []
    for source in db.scalars(select(Source).order_by(Source.id).limit(101)):
        r = view(source)
        r["credential_configured"] = bool(source.encrypted_secret)
        r["live_adapter_available"] = False
        r["counts"] = dict(
            db.execute(
                select(Binding.kind, func.count())
                .where(
                    Binding.source_id == source.id,
                    Binding.operational_revision > 0,
                    Binding.active.is_(True),
                )
                .group_by(Binding.kind)
            ).all()
        )
        r["pending_previews"] = db.scalar(
            select(func.count())
            .select_from(Run)
            .where(Run.source_id == source.id, Run.status == "preview")
        )
        result.append(r)
    return {
        "items": result[:100],
        "truncated": len(result) > 100,
        "schema_version": "nwo.fiber-operational.v1",
        "role": user.role,
        "external_delivery": False,
        "automatic_apply": False,
    }


def provenance(db, user, node_id=None, edge_id=None):
    reader(user)
    clause = (Binding.node_id == node_id) if node_id else (Binding.edge_id == edge_id)
    rows = db.scalars(
        select(Binding)
        .where(clause, Binding.operational_revision > 0, Binding.active.is_(True))
        .limit(101)
    ).all()
    return {
        "items": [
            {
                "source_id": b.source_id,
                "kind": b.kind,
                "external_id": b.external_id,
                "approved_revision": b.operational_revision,
                "last_consumed_source_revision": b.current_revision,
                "data_owner": "AI Fiber Designer: approved engineering; NOC: observed health and operator state",
            }
            for b in rows[:100]
        ],
        "truncated": len(rows) > 100,
        "physical_fault_not_inferred": True,
    }
