"""Synthetic field browser fixture; refuses live/production configuration."""

import json
from uuid import uuid4
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import User
from app.equipment.models import Asset, Location
from app.equipment.contracts import AssetInput
from app.equipment import service as inventory
from . import service as s
from .contracts import TemplateInput, WorkInput, LabelInput, GrantInput
from .models import WorkOrder


def seed():
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Offline synthetic staging required")
    with SessionLocal() as db:
        if db.scalar(select(WorkOrder.id)):
            raise ValueError("Empty field work-order fixture required")
        admin = db.scalar(select(User).where(User.username == "acceptance"))
        asset = db.scalar(select(Asset).where(Asset.asset_tag == "NWO100-SPARE"))
        if not admin or not asset or asset.state != "installed":
            raise ValueError("Completed NOC100 synthetic replacement fixture required")
        tech = User(
            username="field-acceptance",
            email="field@example.invalid",
            password_hash=hash_password(settings.admin_password),
            role="technician",
            is_active=True,
        )
        db.add(tech)
        db.flush()
        reason = "Synthetic offline field browser acceptance fixture"

        def b(cls, **kw):
            return cls(operation_key=uuid4(), reason=reason, **kw)

        template = s.template(
            db,
            admin,
            b(
                TemplateInput,
                code="standard-tower",
                name="Standard tower inspection",
                items=s.default_items(),
            ),
        )
        site = s.asset_site(db, asset)
        s.grant(db, admin, b(GrantInput, site_id=site, technician_id=tech.id))
        order = s.create_work(
            db,
            admin,
            b(
                WorkInput,
                site_id=site,
                asset_id=asset.id,
                technician_id=tech.id,
                template_id=template["id"],
                description="Synthetic tower structure and power inspection",
            ),
        )
        spare = inventory.create_asset(
            db,
            AssetInput(
                product_id=asset.product_id,
                asset_tag="NWO101-FIELD-SPARE",
                location_id=db.scalar(
                    select(Location.id)
                    .where(Location.kind == "warehouse")
                    .order_by(Location.id)
                ),
                state="available",
                condition="good",
                technician_id=tech.id,
                reason=reason,
            ),
            admin.id,
        )
        replace_order = s.create_work(
            db,
            admin,
            b(
                WorkInput,
                site_id=site,
                asset_id=asset.id,
                technician_id=tech.id,
                description="Synthetic equipment replacement field documentation",
            ),
        )
        tower_qr = s.label(db, admin, b(LabelInput, site_id=site))
        asset_qr = s.label(db, admin, b(LabelInput, asset_id=asset.id))
        db.commit()
        return {
            "site_id": site,
            "asset_id": asset.id,
            "spare_id": spare.id,
            "work_id": order["id"],
            "replacement_work_id": replace_order["id"],
            "tower_qr": tower_qr["identifier"],
            "asset_qr": asset_qr["identifier"],
            "label_id": tower_qr["id"],
            "technician_id": tech.id,
        }


if __name__ == "__main__":
    print(json.dumps(seed()))
