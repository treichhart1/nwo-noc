"""Offline staging egress manifest regression tests."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate_staging_egress_policy.py"
spec = importlib.util.spec_from_file_location("validate_staging_egress_policy", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def safe():
    return {
        "default_action": "deny",
        "ipv6_default_action": "deny",
        "allow_rules": [],
        "deny_cidrs": ["0.0.0.0/0", "::/0"],
    }


def test_safe_policy_passes():
    assert module.validate(safe()) == []


def test_default_manifest_passes():
    import json
    path = ROOT / "docs" / "noc-75x-staging-egress-policy.json"
    assert module.validate(json.loads(path.read_text(encoding="utf-8"))) == []


def test_default_allow_fails():
    policy = safe()
    policy["default_action"] = "allow"
    assert module.validate(policy)


def test_ipv6_allow_fails():
    policy = safe()
    policy["ipv6_default_action"] = "allow"
    assert module.validate(policy)


def test_allow_rule_fails():
    policy = safe()
    policy["allow_rules"] = [{"destination": "10.210.0.20"}]
    assert module.validate(policy)


def test_missing_private_range_fails():
    policy = safe()
    policy["deny_cidrs"] = ["10.0.0.0/8", "192.168.0.0/16"]
    assert module.validate(policy)


def test_malformed_cidr_fails():
    policy = safe()
    policy["deny_cidrs"] = ["not-a-network"]
    assert module.validate(policy)
