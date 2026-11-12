"""Validate a declarative staging egress policy; does not configure a firewall."""
import argparse
import ipaddress
import json
from pathlib import Path

REQUIRED_DENY = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def validate(policy):
    errors = []
    if policy.get("default_action") != "deny":
        errors.append("Default outbound action must be deny")
    if policy.get("allow_rules"):
        errors.append("Initial staging policy must not allow outbound destinations")
    if policy.get("ipv6_default_action") != "deny":
        errors.append("IPv6 outbound action must be deny")
    networks = []
    for raw in policy.get("deny_cidrs", []):
        try:
            networks.append(ipaddress.ip_network(raw, strict=True))
        except (ValueError, TypeError):
            errors.append("Invalid deny CIDR")
    for required in REQUIRED_DENY:
        if not any(required.subnet_of(network) for network in networks if network.version == 4):
            errors.append(f"Missing deny coverage for {required}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy_json", type=Path)
    args = parser.parse_args()
    try:
        policy = json.loads(args.policy_json.read_text(encoding="utf-8"))
        if not isinstance(policy, dict):
            raise ValueError("Policy must be an object")
    except (OSError, ValueError) as exc:
        parser.error(f"Invalid policy input: {type(exc).__name__}")
    errors = validate(policy)
    print(json.dumps({
        "static_policy_passed": not errors,
        "errors": errors,
        "firewall_enforcement_verified": False,
        "note": "Manifest checks are not host firewall, DNS, route, or runtime isolation verification.",
    }, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
