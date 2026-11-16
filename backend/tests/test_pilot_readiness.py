"""Offline pilot templates never authorize or activate a real integration."""

import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.pilot_plan import PilotPlan, review

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "name,vendor",
    [
        ("mikrotik-router", "mikrotik"),
        ("wireless-access-point", "ubiquiti"),
        ("microwave-backhaul", "racom"),
    ],
)
def test_pilot_templates_require_independent_approval_and_no_endpoint(name, vendor):
    value = json.loads((ROOT / "deploy/pilot" / (name + ".example.json")).read_text())
    assert value["approved"] is False and value["vendor"] == vendor
    assert value["simulation_only"] is True and value["interval_seconds"] >= 300
    assert value["address"] == "REPLACE_WITH_ONE_APPROVED_RFC1918_IPV4"
    with pytest.raises(ValidationError):
        PilotPlan.model_validate(value)
    # Even a synthetically completed approval assertion remains offline, never activation.
    value.update(
        approved=True,
        approval_record="SYN-NOC114-REVIEW",
        candidate_commit="a" * 40,
        address="10.254.114.1",
        minimum_privilege_reviewed=True,
        private_secret_mount_reviewed=True,
        deny_other_egress_verified=True,
        firmware_mapping_validated=True,
        backup_restore_verified=True,
    )
    result = review(value)
    assert result["execution_enabled"] is False and result["network_calls"] == 0
    value["password"] = "must-not-be-accepted"
    with pytest.raises(ValidationError):
        PilotPlan.model_validate(value)


def test_pilot_context_preserves_existing_inventory_and_no_customer_delivery():
    value = json.loads((ROOT / "deploy/pilot/context.example.json").read_text())
    assert value["activation_enabled"] is False
    assert value["tower"]["existing_site_id"] is None
    assert value["customer_mapping"]["source"] == "synthetic"
    assert value["customer_mapping"]["consent"] == "unknown"
    assert value["customer_mapping"]["notification_delivery_enabled"] is False
    assert not any(
        value[k]
        for k in (
            "live_powercode_enabled",
            "automatic_remediation_enabled",
            "public_publication_enabled",
        )
    )
