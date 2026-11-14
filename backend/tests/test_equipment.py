"""Synthetic physical inventory, lifecycle, stock, privacy and recovery acceptance."""

import base64, json
from decimal import Decimal
from datetime import timedelta, date
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
from app.equipment import service as s
from app.equipment.models import (
    Category,
    Product,
    Manufacturer,
    Location,
    Asset,
    DeviceBinding,
    Movement,
    BulkBalance,
    BulkMovement,
    Maintenance,
    Attachment,
    Financial,
    FinancialChange,
    StockPolicy,
    ImportBatch,
    RequestBudget,
)
from app.equipment.contracts import (
    CategoryInput,
    ProductInput,
    LocationInput,
    AssetInput,
    AssetMove,
    Replacement,
    BulkInput,
    MaintenanceInput,
    AttachmentInput,
    FinancialInput,
    CostInput,
    StockInput,
    ImportInput,
    Approval,
    DeviceImport,
    MetadataPatch,
)
from app.models.entities import Device, User, AuditLog
from app.tower_health.models import TowerCurrent
from test_notifications import network, api
from test_powercode_customers import inventory, imported
from test_rf_postgres_integration import postgres_db

REASON = "Synthetic operator reviewed physical inventory documentation"


def setup(x):
    x.now = s.clock()
    x.category = s.create_category(
        x.db,
        CategoryInput(
            code="network-equipment", name="Network equipment", reason=REASON
        ),
        x.admin.id,
    )
    x.product = s.create_product(
        x.db,
        ProductInput(
            manufacturer=x.ap.vendor or "Ubiquiti",
            model=x.ap.model or x.ap.model_name or "Synthetic radio",
            category_id=x.category.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    x.material = s.create_product(
        x.db,
        ProductInput(
            manufacturer="Synthetic materials",
            model="Fiber reel measured metres",
            category_id=x.category.id,
            individually_tracked=False,
            unit="metre",
            reason=REASON,
        ),
        x.admin.id,
    )
    x.warehouse = s.create_location(
        x.db,
        LocationInput(
            code="SYN-WH", name="Synthetic warehouse", kind="warehouse", reason=REASON
        ),
        x.admin.id,
    )
    x.tower = s.create_location(
        x.db,
        LocationInput(
            code="SYN-TOWER",
            name="Synthetic tower equipment",
            kind="tower",
            site_id=x.a.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    x.repair = s.create_location(
        x.db,
        LocationInput(
            code="SYN-REPAIR",
            name="Synthetic repair shop",
            kind="repair",
            reason=REASON,
        ),
        x.admin.id,
    )
    return x


@pytest.fixture
def stock(network):
    return setup(network)


def asset(x, **kwargs):
    return s.create_asset(
        x.db,
        AssetInput(
            **(
                {
                    "product_id": x.product.id,
                    "location_id": x.warehouse.id,
                    "state": "available",
                    "condition": "good",
                    "reason": REASON,
                }
                | kwargs
            )
        ),
        x.admin.id,
    )


def move(x, a, state, **kwargs):
    return s.move_asset(
        x.db,
        a,
        AssetMove(
            **(
                {"expected_revision": a.revision, "to_state": state, "reason": REASON}
                | kwargs
            )
        ),
        x.admin.id,
    )


def bulk(x, event="SYN-BULK-1", **kwargs):
    return s.bulk_move(
        x.db,
        BulkInput(
            **(
                {
                    "event_key": event,
                    "product_id": x.material.id,
                    "action": "receive",
                    "quantity": "100.500",
                    "to_location_id": x.warehouse.id,
                    "to_state": "available",
                    "reason": REASON,
                }
                | kwargs
            )
        ),
        x.admin.id,
    )


def test_asset_identifiers_multiple_same_model_and_unknown_data(stock):
    x = stock
    a = asset(x)
    b = asset(x)
    assert a.id != b.id and a.public_id != b.public_id and a.asset_tag != b.asset_tag
    assert a.serial_number is None and a.purchase_date is None
    d = s.detail(x.db, a)
    assert (
        d["age_days"] is None
        and d["warranty_status"] == "unknown"
        and d["availability"]["state"] == "unknown"
    )
    assert d["qr_requires_authentication"] and d["qr_url"].startswith("/?asset=")
    assert len(list(x.db.scalars(s.asset_query(q="nwo-")))) == 2
    assert len(d["history"]) == 1 and d["history"][0]["actor_id"] == x.admin.id


@pytest.mark.parametrize(
    "field,first,second",
    [
        ("serial_number", "SERIAL-ONE", "serial-one"),
        ("mac_address", "02:00:00:00:00:01", "02-00-00-00-00-01"),
        ("asset_tag", "NWO-TEST-1", "nwo-test-1"),
    ],
)
def test_duplicate_physical_identifiers(stock, field, first, second):
    x = stock
    asset(x, **{field: first})
    with pytest.raises(ValueError):
        asset(x, **{field: second})


@pytest.mark.parametrize(
    "field,val",
    [
        ("serial_number", "unknown"),
        ("mac_address", "00:00:00:00:00:00"),
        ("mac_address", "ff:ff:ff:ff:ff:ff"),
        ("mac_address", "bad"),
        ("state", "healthy"),
        ("purchase_date", "2999-01-01"),
    ],
)
def test_invalid_asset_contract(field, val):
    with pytest.raises(ValidationError):
        AssetInput(product_id=1, reason=REASON, **{field: val})


def test_existing_device_cannot_be_imported_twice_or_silently_duplicated(stock):
    x = stock
    x.ap.serial_number = "SYN100-EXISTING"
    x.db.flush()
    with pytest.raises(ValueError):
        asset(x, serial_number="SYN100-EXISTING")
    a = asset(
        x,
        state="in_service",
        condition="unknown",
        location_id=x.tower.id,
        device_id=x.ap.id,
        serial_number="SYN100-EXISTING",
    )
    with pytest.raises(ValueError):
        asset(x, state="installed", location_id=x.tower.id, device_id=x.ap.id)
    assert a.device_id == x.ap.id and x.db.scalar(select(func.count(Asset.id))) == 1


def test_existing_noc_device_preview_and_approval(stock):
    x = stock
    b = s.device_preview(
        x.db,
        DeviceImport(
            device_ids=[x.ap.id],
            product_id=x.product.id,
            location_id=x.tower.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    assert not b["errors"] and not x.db.scalar(select(Asset.id))
    result = s.apply_import(
        x.db,
        b["id"],
        Approval(approved=True, digest=b["digest"], reason=REASON),
        x.admin.id,
    )
    assert (
        result["created_assets"] == 1 and result["equipment_configuration_changes"] == 0
    )
    again = s.device_preview(
        x.db,
        DeviceImport(
            device_ids=[x.ap.id],
            product_id=x.product.id,
            location_id=x.tower.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    assert again["errors"] and x.db.scalar(select(func.count(Asset.id))) == 1


def test_warehouse_transfer_install_remove_and_retire(stock):
    x = stock
    a = asset(x)
    uid = a.public_id
    move(x, a, "reserved")
    move(x, a, "installed", to_location_id=x.tower.id, device_id=x.ap.id)
    move(x, a, "in_service")
    assert a.installed_at and a.device_id == x.ap.id
    with pytest.raises(ValueError):
        move(x, a, "in_service", to_location_id=x.warehouse.id)
    move(x, a, "removed", to_location_id=x.repair.id)
    assert a.device_id is None and x.ap.wisp_site_id == x.a.id
    move(x, a, "under_maintenance")
    move(x, a, "available", condition="refurbished")
    move(x, a, "retired", scrap=True)
    assert a.retired_at and a.condition == "scrapped" and a.public_id == uid
    with pytest.raises(ValueError):
        move(x, a, "available")
    assert (
        x.db.scalar(select(func.count(Movement.id)).where(Movement.asset_id == a.id))
        == 8
    )
    assert x.db.scalar(
        select(DeviceBinding).where(DeviceBinding.asset_id == a.id)
    ).ended_at


@pytest.mark.parametrize(
    "source,target",
    [
        ("ordered", "in_service"),
        ("available", "received"),
        ("installed", "available"),
        ("received", "in_service"),
        ("returned", "in_service"),
    ],
)
def test_invalid_lifecycle_transitions(stock, source, target):
    x = stock
    a = asset(
        x,
        state=source,
        location_id=x.tower.id if source == "installed" else x.warehouse.id,
    )
    with pytest.raises(ValueError):
        move(x, a, target)


def test_stale_revision_rolls_back_transfer(stock):
    x = stock
    a = asset(x)
    revision = a.revision
    move(x, a, "reserved")
    with pytest.raises(ValueError):
        s.move_asset(
            x.db,
            a,
            AssetMove(
                expected_revision=revision,
                to_state="installed",
                to_location_id=x.tower.id,
                reason=REASON,
            ),
            x.admin.id,
        )
    assert a.state == "reserved" and a.location_id == x.warehouse.id


def test_failed_equipment_repair_and_verified_spare(stock):
    x = stock
    a = asset(x)
    move(x, a, "failed")
    assert a.documented_operational_status == "failed"
    move(x, a, "under_maintenance")
    move(x, a, "available", condition="refurbished")
    assert a.documented_operational_status == "unknown"
    with pytest.raises(ValueError):
        asset(x, state="available", condition="unknown")


def test_replacement_preserves_history_and_existing_ipam(stock):
    x = stock
    old = asset(
        x,
        state="in_service",
        location_id=x.tower.id,
        device_id=x.ap.id,
        serial_number="OLD-100",
    )
    new = asset(x, serial_number="NEW-100")
    from app.ipam.service import create_vrf, create_prefix, allocate
    from app.ipam.contracts import VRFInput, PrefixInput, AssignmentInput

    vrf = create_vrf(
        x.db, VRFInput(name="NOC100 management", reason=REASON), x.admin.id
    )
    p = create_prefix(
        x.db,
        PrefixInput(
            vrf_id=vrf.id, network="192.0.2.0/24", site_id=x.a.id, reason=REASON
        ),
        x.admin.id,
    )
    ip = allocate(
        x.db,
        AssignmentInput(
            prefix_id=p.id,
            address="192.0.2.10",
            owner="Synthetic router",
            device_id=x.ap.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    result = s.replace_asset(
        x.db,
        old,
        Replacement(
            old_revision=old.revision,
            new_asset_id=new.id,
            new_revision=new.revision,
            removed_to_location_id=x.repair.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    assert old.state == "removed" and old.device_id is None and new.device_id == x.ap.id
    assert new.replacement_of_id == old.id and result["ipam_changes"] == 0
    assert (
        ip.device_id == x.ap.id
        and s.integrated_view(x.db, old)["ipam_assignments"] == []
    )
    assert s.integrated_view(x.db, new)["ipam_assignments"][0]["id"] == ip.id
    bindings = list(x.db.scalars(select(DeviceBinding).order_by(DeviceBinding.id)))
    assert len(bindings) == 2 and bindings[0].ended_at and bindings[1].ended_at is None


def test_tower_assignment_must_match_existing_device(stock):
    x = stock
    other = s.create_location(
        x.db,
        LocationInput(
            code="SYN-OTHER",
            name="Other tower",
            kind="tower",
            site_id=x.b.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    with pytest.raises(ValueError):
        asset(x, state="in_service", location_id=other.id, device_id=x.ap.id)


def test_missing_stale_and_pre_binding_telemetry_never_healthy(stock):
    x = stock
    a = asset(
        x,
        state="in_service",
        condition="unknown",
        location_id=x.tower.id,
        device_id=x.ap.id,
    )
    binding = x.db.scalar(select(DeviceBinding).where(DeviceBinding.asset_id == a.id))
    x.ap.authorized = True
    x.ap.monitor_enabled = True
    x.ap.monitor_status = "up"
    x.ap.monitor_last_checked_at = binding.started_at - timedelta(seconds=1)
    report = {
        "equipment": [
            {
                "device_id": x.ap.id,
                "metric_states": {
                    "cpu_percent": {
                        "value": 20,
                        "state": "available",
                        "observed_at": (
                            binding.started_at - timedelta(seconds=1)
                        ).isoformat(),
                    },
                    "temperature_c": {
                        "value": None,
                        "state": "unsupported",
                        "observed_at": None,
                    },
                },
            }
        ]
    }
    row = TowerCurrent(site_id=x.a.id, assessed_at=s.clock(), report=report)
    x.db.add(row)
    x.db.flush()
    observed = s.integrated_view(x.db, a)
    assert (
        observed["availability"]["state"] == "unknown"
        and observed["telemetry"]["metrics"]["cpu_percent"]["state"] == "pre_binding"
    )
    report["equipment"][0]["metric_states"]["cpu_percent"][
        "observed_at"
    ] = s.clock().isoformat()
    row.report = json.loads(json.dumps(report))
    x.ap.monitor_last_checked_at = s.clock()
    x.db.flush()
    assert s.integrated_view(x.db, a)["availability"]["state"] == "up"
    assert (
        s.integrated_view(x.db, a)["telemetry"]["metrics"]["cpu_percent"]["value"] == 20
    )


def test_bulk_receipt_transfer_install_remove_and_replay(stock):
    x = stock
    r = bulk(x)
    same = bulk(x)
    assert same["duplicate"] and same["movement"]["id"] == r["movement"]["id"]
    source = x.db.scalar(select(BulkBalance))
    assert source.quantity == Decimal("100.500")
    transfer = bulk(
        x,
        event="SYN-BULK-INSTALL",
        action="install",
        from_location_id=x.warehouse.id,
        from_state="available",
        to_location_id=x.tower.id,
        to_state="installed",
        quantity="25.250",
        expected_source_revision=source.revision,
    )
    assert not transfer["duplicate"] and source.quantity == Decimal("75.250")
    target = x.db.scalar(
        select(BulkBalance).where(BulkBalance.location_id == x.tower.id)
    )
    bulk(
        x,
        event="SYN-BULK-REMOVE",
        action="remove",
        from_location_id=x.tower.id,
        from_state="installed",
        to_location_id=x.repair.id,
        to_state="repair",
        quantity="5.000",
        expected_source_revision=target.revision,
    )
    assert sum(x.db.scalars(select(BulkBalance.quantity))) == Decimal("100.500")
    with pytest.raises(ValueError):
        bulk(x, quantity="2")


def test_bulk_insufficient_quantity_is_atomic(stock):
    x = stock
    bulk(x)
    source = x.db.scalar(select(BulkBalance))
    with pytest.raises(ValueError):
        bulk(
            x,
            event="SYN-TOO-MUCH",
            action="transfer",
            quantity="101",
            from_location_id=x.warehouse.id,
            from_state="available",
            to_location_id=x.repair.id,
            expected_source_revision=source.revision,
        )
    assert (
        source.quantity == Decimal("100.500")
        and x.db.scalar(select(func.count(BulkMovement.id))) == 1
    )
    with pytest.raises(ValueError):
        asset(x, product_id=x.material.id)
    with pytest.raises(ValueError):
        bulk(x, event="SYN-INVALID", product_id=x.product.id)


@pytest.mark.parametrize("quantity", ["-1", "0", "0.0001", "NaN", "Infinity"])
def test_invalid_bulk_quantities(quantity):
    with pytest.raises(ValidationError):
        BulkInput(
            event_key="SYN",
            product_id=1,
            quantity=quantity,
            action="receive",
            to_location_id=1,
            reason=REASON,
        )


def test_maintenance_parts_and_download_only_attachments(stock):
    x = stock
    a = asset(x)
    m = s.add_maintenance(
        x.db,
        a,
        MaintenanceInput(
            inspected_at=s.clock(),
            work_performed="Inspected cabinet and replaced synthetic patch lead",
            parts=[{"product_id": x.material.id, "quantity": "1.5"}],
            condition="good",
            reason=REASON,
        ),
        x.admin.id,
    )
    b = AttachmentInput(
        filename="inspection.txt",
        media_type="text/plain",
        contents_base64=base64.b64encode(
            b"Synthetic cabinet inspection complete"
        ).decode(),
        reason=REASON,
    )
    result = s.add_attachment(x.db, m, b, x.admin.id)
    assert result["size"] > 0 and result["download_only"] and "data" not in result
    assert a.condition == "good" and m.technician_id == x.admin.id


@pytest.mark.parametrize(
    "media,name,data",
    [
        ("image/png", "evil.png", b"<html>bad"),
        ("text/plain", "notes.txt", b"password=do-not-store"),
        ("text/plain", "notes.txt", b"\x00bad"),
        ("image/jpeg", "file.jpg", b"not jpeg"),
        ("text/plain", "notes.txt", b"x" * 524289),
    ],
)
def test_attachment_validation(media, name, data):
    with pytest.raises(ValueError):
        s.verify_file(
            AttachmentInput(
                filename=name,
                media_type=media,
                contents_base64=base64.b64encode(data).decode(),
                reason=REASON,
            )
        )


def test_warranty_and_financial_unknowns_and_explicit_depreciation(stock):
    x = stock
    a = asset(x, purchase_date=date(2020, 1, 15), warranty_end=date(2027, 1, 15))
    assert s.financial_view(x.db, a)["book_value"] is None
    s.set_financial(
        x.db,
        a,
        FinancialInput(
            currency="USD",
            purchase_cost="1200.00",
            salvage_value="0.00",
            depreciation_method="straight_line_monthly",
            useful_life_months=60,
            reason=REASON,
        ),
        x.admin.id,
    )
    assert s.financial_view(x.db, a, date(2022, 7, 15))["book_value"] == "600.00"
    assert s.financial_view(x.db, a, date(2020, 1, 14))["book_value"] is None
    assert s.financial_view(x.db, a, date(2030, 1, 1))["book_value"] == "0.00"
    assert s.detail(x.db, a)["age_basis"] == "purchase_date"
    assert "financial" not in s.detail(x.db, a)
    assert x.db.scalar(select(func.count(FinancialChange.id))) == 1


@pytest.mark.parametrize(
    "data",
    [
        {"purchase_cost": "10.00"},
        {"currency": "USD", "purchase_cost": "-1"},
        {"currency": "USD", "purchase_cost": "10", "salvage_value": "11"},
        {"currency": "USD", "purchase_cost": "NaN"},
        {"currency": "USD", "purchase_cost": "0.001"},
    ],
)
def test_financial_contract_requires_known_units_and_values(data):
    with pytest.raises(ValidationError):
        FinancialInput(reason=REASON, **data)


def test_actual_repair_costs_and_currencies_not_estimates(stock):
    x = stock
    a = asset(x)
    m = s.add_maintenance(
        x.db,
        a,
        MaintenanceInput(
            inspected_at=s.clock(),
            work_performed="Synthetic equipment repair investigation",
            reason=REASON,
        ),
        x.admin.id,
    )
    s.add_cost(
        x.db,
        CostInput(maintenance_id=m.id, amount="25.50", currency="USD", reason=REASON),
        x.admin.id,
    )
    s.set_financial(
        x.db,
        a,
        FinancialInput(
            currency="USD",
            purchase_cost="100.00",
            replacement_estimate="200.00",
            installation_labor_estimate="30.00",
            reason=REASON,
        ),
        x.admin.id,
    )
    report = s.cost_report(x.db)
    assert (
        report["actual_event_costs"][0]["amount"] == "25.50"
        and report["estimated_costs_are_not_actuals"]
    )
    with pytest.raises(ValueError):
        s.add_cost(
            x.db,
            CostInput(
                maintenance_id=m.id, amount="25.50", currency="USD", reason=REASON
            ),
            x.admin.id,
        )
    assert s.financial_view(x.db, a)["missing_depreciation_inputs"]


def test_low_stock_critical_no_spare_warranty_and_failure_evidence(stock):
    x = stock
    a = asset(x, warranty_end=s.clock().date() + timedelta(days=10))
    x.product.critical = True
    x.db.flush()
    p = s.set_stock_policy(
        x.db,
        StockInput(
            product_id=x.product.id, minimum_available="2", critical=True, reason=REASON
        ),
        x.admin.id,
    )
    assert any(r["rule"] == "low_stock" for r in s.planning(x.db)["items"])
    for i in range(3):
        move(x, a, "failed")
        move(x, a, "under_maintenance")
        move(x, a, "available", condition="refurbished")
    rules = {r["rule"] for r in s.planning(x.db)["items"]}
    assert "repeated_documented_failures" in rules and "warranty_review" in rules
    move(x, a, "reserved")
    assert "critical_model_without_spare" in {
        r["rule"] for r in s.planning(x.db)["items"]
    }


def test_csv_preview_no_overwrite_and_failed_import_rollback(stock):
    x = stock
    data = f"product_id,asset_tag,location_id,state,condition,serial_number\n{x.product.id},CSV100-A,{x.warehouse.id},available,good,SYN100-A\n{x.product.id},CSV100-B,{x.warehouse.id},available,good,SYN100-B"
    b = s.csv_preview(x.db, ImportInput(csv_text=data, reason=REASON), x.admin.id)
    assert not b["errors"] and not x.db.scalar(select(Asset.id))
    asset(x, serial_number="SYN100-B")
    with pytest.raises(ValueError):
        with x.db.begin_nested():
            s.apply_import(
                x.db,
                b["id"],
                Approval(approved=True, digest=b["digest"], reason=REASON),
                x.admin.id,
            )
    assert not x.db.scalar(select(Asset.id).where(Asset.asset_tag == "CSV100-A"))
    assert x.db.get(ImportBatch, b["id"]).state == "preview"


def test_csv_success_duplicate_and_financial_boundaries(stock):
    x = stock
    data = f"product_id,asset_tag,location_id,state,condition\n{x.product.id},CSV100-ONE,{x.warehouse.id},available,good"
    b = s.csv_preview(x.db, ImportInput(csv_text=data, reason=REASON), x.admin.id)
    r = s.apply_import(
        x.db,
        b["id"],
        Approval(approved=True, digest=b["digest"], reason=REASON),
        x.admin.id,
    )
    assert r["created_assets"] == 1
    assert s.csv_preview(x.db, ImportInput(csv_text=data, reason=REASON), x.admin.id)[
        "errors"
    ]
    with pytest.raises(ValueError):
        s.apply_import(
            x.db,
            b["id"],
            Approval(approved=True, digest=b["digest"], reason=REASON),
            x.admin.id,
        )
    with pytest.raises(PermissionError):
        s.csv_preview(
            x.db,
            ImportInput(
                csv_text=data.replace(
                    "condition", "condition,purchase_cost,currency"
                ).replace("available,good", "available,good,100,USD"),
                reason=REASON,
            ),
            x.admin.id,
            False,
        )


@pytest.mark.parametrize(
    "data",
    [
        "unknown\nvalue",
        "product_id,product_id\n1,1",
        "product_id,location_id\n1,1",
        "product_id,asset_tag\n1,=formula",
    ],
)
def test_bad_import_validation(stock, data):
    with pytest.raises(ValueError):
        s.csv_preview(
            stock.db, ImportInput(csv_text=data, reason=REASON), stock.admin.id
        )


def test_metadata_updates_are_explicit_and_audited(stock):
    x = stock
    a = asset(x)
    old = a.revision
    s.patch_metadata(
        x.db,
        a,
        MetadataPatch(
            expected_revision=old,
            serial_number="KNOWN-LATER",
            recorded_firmware="synthetic-1",
            warranty_end=date(2028, 1, 1),
            reason=REASON,
        ),
        x.admin.id,
    )
    assert a.serial_number == "KNOWN-LATER" and a.revision == old + 1
    assert x.db.scalar(select(Movement).where(Movement.action == "metadata_updated"))
    with pytest.raises(ValueError):
        s.patch_metadata(
            x.db,
            a,
            MetadataPatch(
                expected_revision=old, supplier="Other supplier", reason=REASON
            ),
            x.admin.id,
        )


def test_native_database_constraints_protect_asset_and_bulk_context(stock):
    x = stock
    a = asset(x, serial_number="SYN100-UNIQUE")
    with pytest.raises(IntegrityError):
        with x.db.begin_nested():
            x.db.add(
                Asset(
                    product_id=x.material.id,
                    manufacturer_id=x.material.manufacturer_id,
                    asset_tag="BAD-BULK-ASSET",
                    location_id=x.warehouse.id,
                )
            )
            x.db.flush()
    with pytest.raises(IntegrityError):
        with x.db.begin_nested():
            x.db.execute(
                text("UPDATE equipment_assets SET state='in_service' WHERE id=:id"),
                {"id": a.id},
            )
    with pytest.raises(IntegrityError):
        with x.db.begin_nested():
            x.db.add(
                BulkBalance(
                    product_id=x.material.id,
                    location_id=x.warehouse.id,
                    state="available",
                    quantity=Decimal("-1"),
                )
            )
            x.db.flush()


def test_api_privacy_qr_financials_and_attachments(api):
    x, c, h = api
    setup(x)
    a = asset(x)
    assert c.get(f"/api/equipment/assets/{a.public_id}").status_code == 401
    assert c.get(f"/api/equipment/assets/{a.public_id}/qr").status_code == 401
    fin = {"currency": "USD", "purchase_cost": "9876.54", "reason": REASON}
    assert (
        c.put(
            f"/api/equipment/assets/{a.id}/financial", headers=h, json=fin
        ).status_code
        == 200
    )
    maintenance = {
        "inspected_at": s.clock().isoformat(),
        "work_performed": "Synthetic detailed equipment inspection",
        "reason": REASON,
    }
    r = c.post(f"/api/equipment/assets/{a.id}/maintenance", headers=h, json=maintenance)
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    upload = {
        "filename": "invoice.txt",
        "media_type": "text/plain",
        "contents_base64": base64.b64encode(
            b"USD9876.54 synthetic financial attachment"
        ).decode(),
        "restricted_financial": True,
        "reason": REASON,
    }
    r = c.post(f"/api/equipment/maintenance/{mid}/attachments", headers=h, json=upload)
    assert r.status_code == 200, r.text
    file_id = r.json()["id"]
    x.admin.role = "operator"
    x.db.commit()
    for path in ["", f"/assets/{a.id}", f"/assets/{a.public_id}/qr", "/export.csv"]:
        r = c.get("/api/equipment" + path, headers=h)
        assert r.status_code == 200, r.text
        assert "9876.54" not in r.text and "purchase_cost" not in r.text
    assert (
        c.put(
            f"/api/equipment/assets/{a.id}/financial", headers=h, json=fin
        ).status_code
        == 403
    )
    assert c.get(f"/api/equipment/attachments/{file_id}", headers=h).status_code == 403
    assert (
        c.get(f"/api/equipment/maintenance/{mid}/attachments", headers=h).json() == []
    )
    assert (
        c.get("/api/equipment/export.csv?financial=true", headers=h).status_code == 403
    )
    x.admin.role = "viewer"
    x.db.commit()
    assert c.get("/api/equipment", headers=h).status_code == 403
    assert (
        c.post(
            "/api/equipment/assets",
            headers=h,
            json={"product_id": x.product.id, "reason": REASON},
        ).status_code
        == 403
    )


def test_inventory_request_budget(api):
    x, c, h = api
    x.db.add(
        RequestBudget(
            user_id=x.admin.id,
            minute=s.clock().replace(second=0, microsecond=0),
            count=120,
        )
    )
    x.db.commit()
    assert c.get("/api/equipment", headers=h).status_code == 429


def test_concurrent_inventory_transfers_are_revision_protected(postgres_db):
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4
    from sqlalchemy.orm import Session
    from sqlalchemy import delete

    name = "noc100-race-" + uuid4().hex
    engine = postgres_db.bind.engine
    actor = None
    product_ids = []
    locations = []
    asset_id = None
    maker = None
    category = None
    try:
        with Session(engine) as db:
            user = User(
                username=name,
                email=name + "@example.invalid",
                password_hash="synthetic-unusable-hash",
                role="operator",
                is_active=True,
            )
            db.add(user)
            db.flush()
            actor = user.id
            cat = s.create_category(
                db,
                CategoryInput(
                    code="race-" + uuid4().hex,
                    name="Synthetic race category",
                    reason=REASON,
                ),
                actor,
            )
            category = cat.id
            p = s.create_product(
                db,
                ProductInput(
                    manufacturer=name,
                    model="Individual race asset",
                    category_id=cat.id,
                    reason=REASON,
                ),
                actor,
            )
            material = s.create_product(
                db,
                ProductInput(
                    manufacturer=name,
                    model="Bulk race material",
                    category_id=cat.id,
                    individually_tracked=False,
                    unit="metre",
                    reason=REASON,
                ),
                actor,
            )
            product_ids = [p.id, material.id]
            maker = p.manufacturer_id
            for i in range(2):
                locations.append(
                    s.create_location(
                        db,
                        LocationInput(
                            code=name + str(i),
                            name=name + str(i),
                            kind="warehouse",
                            reason=REASON,
                        ),
                        actor,
                    ).id
                )
            a = s.create_asset(
                db,
                AssetInput(
                    product_id=p.id,
                    location_id=locations[0],
                    state="available",
                    condition="good",
                    reason=REASON,
                ),
                actor,
            )
            asset_id = a.id
            asset_revision = a.revision
            s.bulk_move(
                db,
                BulkInput(
                    event_key=name + "-receive",
                    product_id=material.id,
                    action="receive",
                    to_location_id=locations[0],
                    quantity="100",
                    reason=REASON,
                ),
                actor,
            )
            source = db.scalar(
                select(BulkBalance).where(BulkBalance.product_id == material.id)
            )
            source_revision = source.revision
            db.commit()

        def transfer(i):
            with Session(engine) as db:
                try:
                    s.move_asset(
                        db,
                        s.resolve(db, asset_id),
                        AssetMove(
                            expected_revision=asset_revision,
                            to_state="available",
                            to_location_id=locations[1],
                            reason=REASON,
                        ),
                        actor,
                    )
                    db.commit()
                    return "moved"
                except ValueError:
                    db.rollback()
                    return "conflict"

        with ThreadPoolExecutor(max_workers=4) as pool:
            r = list(pool.map(transfer, range(4)))
        assert r.count("moved") == 1 and r.count("conflict") == 3

        def material_transfer(i):
            with Session(engine) as db:
                try:
                    s.bulk_move(
                        db,
                        BulkInput(
                            event_key=name + "-move-" + str(i),
                            product_id=product_ids[1],
                            action="transfer",
                            from_location_id=locations[0],
                            from_state="available",
                            to_location_id=locations[1],
                            to_state="available",
                            quantity="80",
                            expected_source_revision=source_revision,
                            reason=REASON,
                        ),
                        actor,
                    )
                    db.commit()
                    return "moved"
                except ValueError:
                    db.rollback()
                    return "conflict"

        with ThreadPoolExecutor(max_workers=4) as pool:
            r = list(pool.map(material_transfer, range(4)))
        assert r.count("moved") == 1 and r.count("conflict") == 3
        with Session(engine) as db:
            assert sum(
                db.scalars(
                    select(BulkBalance.quantity).where(
                        BulkBalance.product_id == product_ids[1]
                    )
                )
            ) == Decimal("100")
    finally:
        if actor:
            with Session(engine) as db:
                db.execute(delete(Movement).where(Movement.actor_id == actor))
                db.execute(delete(BulkMovement).where(BulkMovement.actor_id == actor))
                if asset_id:
                    db.execute(delete(Asset).where(Asset.id == asset_id))
                if product_ids:
                    db.execute(
                        delete(BulkBalance).where(
                            BulkBalance.product_id.in_(product_ids)
                        )
                    )
                    db.execute(delete(Product).where(Product.id.in_(product_ids)))
                if locations:
                    db.execute(delete(Location).where(Location.id.in_(locations)))
                if maker:
                    db.execute(delete(Manufacturer).where(Manufacturer.id == maker))
                if category:
                    db.execute(delete(Category).where(Category.id == category))
                db.execute(delete(AuditLog).where(AuditLog.user_id == actor))
                db.execute(delete(User).where(User.id == actor))
                db.commit()


def test_maintenance_status_updates_preserve_work_history(stock):
    from app.equipment.contracts import MaintenanceStatus

    x = stock
    a = asset(x)
    m = s.add_maintenance(
        x.db,
        a,
        MaintenanceInput(
            inspected_at=s.clock(),
            work_performed="Synthetic initial diagnostic inspection",
            status="awaiting_parts",
            reason=REASON,
        ),
        x.admin.id,
    )
    s.maintenance_status(
        x.db,
        m,
        MaintenanceStatus(
            expected_status="awaiting_parts",
            status="completed",
            follow_up="Monitor documented repair observations",
            reason=REASON,
        ),
        x.admin.id,
    )
    assert m.status == "completed" and x.db.scalar(
        select(Movement.id).where(Movement.action == "maintenance_status_changed")
    )
    with pytest.raises(ValueError):
        s.maintenance_status(
            x.db,
            m,
            MaintenanceStatus(
                expected_status="completed", status="planned", reason=REASON
            ),
            x.admin.id,
        )


def test_same_serial_different_manufacturer_is_not_false_duplicate(stock):
    x = stock
    asset(x, serial_number="VENDOR-REUSED-100")
    p = s.create_product(
        x.db,
        ProductInput(
            manufacturer="Different synthetic manufacturer",
            model="Same serial scheme",
            category_id=x.category.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    other = asset(x, product_id=p.id, serial_number="VENDOR-REUSED-100")
    assert other.manufacturer_id != x.product.manufacturer_id


def test_valid_png_is_stored_opaque_with_hash():
    # Standard one-pixel RGBA PNG, built with actual CRCs rather than trusting filename.
    import zlib, struct

    def chunk(kind, data):
        return (
            struct.pack("!I", len(data))
            + kind
            + data
            + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff"))
        + chunk(b"IEND", b"")
    )
    assert (
        s.verify_file(
            AttachmentInput(
                filename="photo.png",
                media_type="image/png",
                contents_base64=base64.b64encode(raw).decode(),
                reason=REASON,
            )
        )
        == raw
    )


def test_backdated_bindings_cannot_overlap_physical_history(stock):
    x = stock
    old = asset(x, state="in_service", location_id=x.tower.id, device_id=x.ap.id)
    started = x.db.scalar(
        select(DeviceBinding.started_at).where(DeviceBinding.asset_id == old.id)
    )
    move(x, old, "removed", to_location_id=x.repair.id)
    with pytest.raises(ValueError):
        with x.db.begin_nested():
            asset(
                x,
                state="in_service",
                location_id=x.tower.id,
                device_id=x.ap.id,
                installed_at=started - timedelta(hours=1),
            )
    with pytest.raises(IntegrityError):
        with x.db.begin_nested():
            x.db.add(
                DeviceBinding(
                    asset_id=old.id,
                    device_id=x.ap.id,
                    started_at=started - timedelta(minutes=5),
                    ended_at=started + timedelta(seconds=1),
                )
            )
            x.db.flush()


def test_historical_device_import_is_reviewed_and_missing_identifiers_not_invented(
    stock,
):
    x = stock
    x.ap.serial_number = "unknown"
    x.ap.mac_address = "not-physical"
    b = s.device_preview(
        x.db,
        DeviceImport(
            device_ids=[x.ap.id],
            product_id=x.product.id,
            location_id=x.tower.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    assert not b["errors"]
    r = s.apply_import(
        x.db,
        b["id"],
        Approval(approved=True, digest=b["digest"], reason=REASON),
        x.admin.id,
    )
    a = x.db.get(Asset, r["asset_ids"][0])
    assert a.serial_number is None and a.mac_address is None
    move(x, a, "removed", to_location_id=x.repair.id)
    again = s.device_preview(
        x.db,
        DeviceImport(
            device_ids=[x.ap.id],
            product_id=x.product.id,
            location_id=x.tower.id,
            reason=REASON,
        ),
        x.admin.id,
    )
    assert again["errors"] and x.db.scalar(select(func.count(Asset.id))) == 1
