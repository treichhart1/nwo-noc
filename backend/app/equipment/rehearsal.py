"""Offline asset/device/IPAM browser acceptance fixture; no equipment access."""

import json
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import User, Device
from app.ipam.models import Assignment
from .models import Asset
from .contracts import (
    CategoryInput,
    ProductInput,
    LocationInput,
    AssetInput,
    StockInput,
    FinancialInput,
)
from . import service as s


def seed():
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Explicit isolated synthetic staging required")
    with SessionLocal() as db:
        if db.scalar(select(Asset.id)):
            raise ValueError("Empty asset fixture required")
        actor = db.scalar(select(User).where(User.username == "acceptance"))
        ip = db.scalar(select(Assignment).where(Assignment.address == "10.99.0.2"))
        if not actor or not ip or not ip.device_id:
            raise ValueError("NOC99 synthetic IPAM fixture required")
        device = db.get(Device, ip.device_id)
        reason = "Synthetic browser asset acceptance fixture"
        category = s.create_category(
            db,
            CategoryInput(
                code="synthetic-radio", name="Synthetic RF equipment", reason=reason
            ),
            actor.id,
        )
        product = s.create_product(
            db,
            ProductInput(
                manufacturer=device.vendor or "Synthetic maker",
                model=device.model or device.model_name or "Synthetic radio",
                category_id=category.id,
                reason=reason,
            ),
            actor.id,
        )
        warehouse = s.create_location(
            db,
            LocationInput(
                code="NOC100-WH",
                name="NOC100 Synthetic warehouse",
                kind="warehouse",
                reason=reason,
            ),
            actor.id,
        )
        tower = s.create_location(
            db,
            LocationInput(
                code="NOC100-TOWER",
                name="NOC100 Synthetic tower location",
                kind="tower",
                site_id=device.wisp_site_id,
                reason=reason,
            ),
            actor.id,
        )
        repair = s.create_location(
            db,
            LocationInput(
                code="NOC100-REPAIR",
                name="NOC100 Synthetic repair stock",
                kind="repair",
                reason=reason,
            ),
            actor.id,
        )
        old = s.create_asset(
            db,
            AssetInput(
                product_id=product.id,
                asset_tag="NWO100-INSTALLED",
                serial_number="SYN100-INSTALLED",
                device_id=device.id,
                location_id=tower.id,
                state="in_service",
                condition="unknown",
                reason=reason,
            ),
            actor.id,
        )
        spare = s.create_asset(
            db,
            AssetInput(
                product_id=product.id,
                asset_tag="NWO100-SPARE",
                serial_number="SYN100-SPARE",
                location_id=warehouse.id,
                state="available",
                condition="good",
                reason=reason,
            ),
            actor.id,
        )
        s.set_stock_policy(
            db,
            StockInput(product_id=product.id, minimum_available="2", reason=reason),
            actor.id,
        )
        s.set_financial(
            db,
            old,
            FinancialInput(currency="USD", purchase_cost="123.45", reason=reason),
            actor.id,
        )
        db.commit()
        return {
            "asset_id": old.id,
            "asset_uuid": str(old.public_id),
            "spare_id": spare.id,
            "product_id": product.id,
            "warehouse_id": warehouse.id,
            "repair_location_id": repair.id,
            "site_id": device.wisp_site_id,
        }


if __name__ == "__main__":
    print(json.dumps(seed()))
