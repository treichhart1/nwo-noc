"""Offline deployment readiness audit; never contacts services or reads live credentials."""
import argparse
import json
from pathlib import Path

from validate_compose_network import validate as validate_compose
from validate_deployment_env import parse_env, validate as validate_env


def audit(compose, env):
    checks = {
        "compose_network_exposure": validate_compose(compose),
        "deployment_environment": validate_env(env),
    }
    return {
        "ready_for_configuration_review": all(not issues for issues in checks.values()),
        "checks": {name: {"passed": not issues, "issues": issues} for name, issues in checks.items()},
        "limitations": [
            "Offline configuration checks only; no runtime or connectivity verification",
            "Does not validate secrets against real services or prove production readiness",
            "Staging acceptance and operator review remain required before deployment",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-json", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args()
    try:
        compose = json.loads(args.compose_json.read_text(encoding="utf-8"))
        env = parse_env(args.env_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        parser.error(f"Cannot read input configuration: {type(exc).__name__}")
    result = audit(compose, env)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ready_for_configuration_review"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
