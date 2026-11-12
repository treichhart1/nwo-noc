"""Offline staging evidence gate; never starts services or contacts infrastructure."""
import argparse
import json
from pathlib import Path

REQUIRED = (
    "offline_configuration_review_passed",
    "isolated_staging_environment_confirmed",
    "database_backup_and_restore_verified",
    "staging_migrations_verified",
    "rf_collection_and_pruning_disabled",
    "uisp_integration_isolated_or_mocked",
    "operator_review_completed",
)


def evaluate(evidence):
    checks = {name: evidence.get(name) is True for name in REQUIRED}
    return {
        "staging_gate_passed": all(checks.values()),
        "checks": checks,
        "missing": [name for name, passed in checks.items() if not passed],
        "production_deployment_approved": False,
        "note": "This gate is documentary evidence only, not a production deployment authorization.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_json", type=Path)
    args = parser.parse_args()
    try:
        evidence = json.loads(args.evidence_json.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            raise ValueError("Evidence must be a JSON object")
    except (OSError, ValueError) as exc:
        parser.error(f"Invalid evidence input: {type(exc).__name__}")
    result = evaluate(evidence)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["staging_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
