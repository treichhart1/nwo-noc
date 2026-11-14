"""Reproducible offline IPAM browser fixture; no discovery transport."""

import json
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import User
from app.customers.models import CustomerAccount, CustomerService
from .models import VRF
from .contracts import VRFInput, PrefixInput, AssignmentInput, DiscoveryInput
from . import service as s, discovery


def seed():
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Isolated synthetic staging required")
    with SessionLocal() as db:
        if db.scalar(select(VRF.id)):
            raise ValueError("Empty IPAM fixture required")
        actor = db.scalar(select(User).where(User.username == "acceptance"))
        account = db.scalar(
            select(CustomerAccount).where(CustomerAccount.external_id == "NOC95-C0001")
        )
        if not actor or not account:
            raise ValueError("Existing synthetic customer fixture required")
        service = db.scalar(
            select(CustomerService).where(CustomerService.customer_id == account.id)
        )
        v = s.create_vrf(
            db,
            VRFInput(
                name="NOC99 Synthetic Management",
                reason="Synthetic browser acceptance fixture",
            ),
            actor.id,
        )
        p = s.create_prefix(
            db,
            PrefixInput(
                vrf_id=v.id,
                network="10.99.0.0/24",
                site_id=service.site_id,
                gateway="10.99.0.1",
                purpose="Synthetic tower management",
                reason="Synthetic browser acceptance fixture",
            ),
            actor.id,
        )
        a = s.allocate(
            db,
            AssignmentInput(
                prefix_id=p.id,
                address="10.99.0.2",
                owner="Synthetic tower router",
                device_id=service.device_id,
                state="assigned",
                kind="management",
                dns_name="router.example.invalid",
                reason="Synthetic browser acceptance fixture",
            ),
            actor.id,
        )
        discovery.ingest(
            db,
            DiscoveryInput(
                batch_key="SYN99-browser",
                vrf_id=v.id,
                device_id=service.device_id,
                observed_at=s.clock(),
                routeros_version="7.16.2",
                protocol="rest7",
                responses={
                    "/rest/ip/dhcp-server/lease": [
                        {"address": "10.99.0.50", "status": "bound"}
                    ]
                },
                reason="Synthetic recorded DHCP browser fixture",
            ),
            actor.id,
        )
        db.commit()
        return {"vrf_id": v.id, "prefix_id": p.id, "assignment_id": a.id}


if __name__ == "__main__":
    print(json.dumps(seed()))
