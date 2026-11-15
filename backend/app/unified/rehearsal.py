"""Explicit synthetic CI fixture; no discovery, device IO or customer provisioning."""

import json
from uuid import uuid4
from datetime import timedelta
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import User, Site, Device
from app.customers.models import CustomerService
from app.field.models import now
from app.field.contracts import Mutation
from .models import Node, Strand, End
from .contracts import *
from . import service as s

R = "NOC104 synthetic isolated operator reviewed topology"


def seed():
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Offline synthetic staging required")
    with SessionLocal() as db:
        if db.scalar(select(Node.id)):
            raise ValueError("Fresh unified fixture required")
        u = User(
            username="unified-acceptance",
            email="unified-acceptance@example.invalid",
            password_hash=hash_password(settings.admin_password),
            role="admin",
            is_active=True,
        )
        db.add(u)
        db.flush()
        sites = db.scalars(select(Site).order_by(Site.id).limit(2)).all()
        while len(sites) < 2:
            site = Site(
                name="NOC104 Synthetic POP " + str(len(sites)),
                latitude=41.4 + len(sites) * 0.01,
                longitude=-84.2,
            )
            db.add(site)
            db.flush()
            sites.append(site)

        def b(cls, **kw):
            return cls(operation_key=uuid4(), reason=R, **kw)

        core = s.create_node(db, u, b(NodeInput, kind="core", site_id=sites[0].id))[
            "id"
        ]
        s.import_existing(db, u, b(Mutation))
        tower = db.scalar(select(Node).where(Node.site_id == sites[1].id)).id
        service = db.scalar(
            select(CustomerService)
            .where(
                CustomerService.status == "active",
                CustomerService.source_deleted_at.is_(None),
            )
            .order_by(CustomerService.id)
        )
        if not service:
            raise ValueError("Existing synthetic Powercode service fixture required")
        customer = s.create_node(
            db, u, b(NodeInput, kind="customer", service_id=service.id)
        )["id"]

        def e(a, z, tech, label):
            return s.create_edge(
                db,
                u,
                b(
                    EdgeInput,
                    label=label,
                    a_id=a,
                    z_id=z,
                    layer="physical",
                    relation="transport",
                    technology=tech,
                    state="installed",
                    evidence=R,
                ),
            )["id"]

        fiber = e(core, tower, "fiber", "NOC104 Synthetic fiber feed")
        access = e(tower, customer, "wireless", "NOC104 Synthetic wireless access")
        backup = e(
            core, customer, "microwave", "NOC104 Synthetic verified microwave backup"
        )
        cable = s.create_cable(
            db,
            u,
            b(
                CableInput,
                code="NOC104-SYN-CABLE",
                edge_id=fiber,
                strand_count=12,
                geometry=[[-84.21, 41.4], [-84.2, 41.4]],
            ),
        )
        circuit = s.circuit(
            db,
            u,
            b(
                CircuitInput,
                code="NOC104-SYN-SERVICE",
                service_id=service.id,
                purpose="business",
                state="active",
                topology_complete=True,
                completeness_evidence=R,
            ),
        )
        strand = db.scalar(
            select(Strand).where(Strand.cable_id == cable["id"]).order_by(Strand.number)
        )
        s.reserve(
            db,
            u,
            b(
                StrandInput,
                strand_ids=[strand.id],
                expected_revisions={strand.id: strand.revision},
                action="assign",
                circuit_id=circuit["id"],
            ),
        )
        for name, hops in [
            (
                "primary",
                [
                    HopInput(edge_id=fiber, strand_id=strand.id),
                    HopInput(edge_id=access),
                ],
            ),
            ("backup", [HopInput(edge_id=backup)]),
        ]:
            s.create_path(
                db,
                u,
                b(
                    PathInput,
                    circuit_id=circuit["id"],
                    name=name,
                    state="active",
                    source_id=core,
                    destination_id=customer,
                    hops=hops,
                    verified_until=now() + timedelta(minutes=15),
                    verification_evidence=R,
                ),
            )
        db.commit()
        return {
            "admin_username": u.username,
            "core_id": core,
            "tower_id": tower,
            "customer_node_id": customer,
            "service_id": service.id,
            "fiber_edge_id": fiber,
            "backup_edge_id": backup,
            "cable_id": cable["id"],
            "strand_id": strand.id,
            "circuit_id": circuit["id"],
        }


if __name__ == "__main__":
    print(json.dumps(seed()), flush=True)
