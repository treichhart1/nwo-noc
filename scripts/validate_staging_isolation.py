"""Offline staging configuration lint; does NOT prove firewall or runtime isolation."""
import argparse
import json
from pathlib import Path

from validate_compose_network import validate as validate_compose
from validate_deployment_env import parse_env


def validate(compose, env):
    failures = list(validate_compose(compose))
    if env.get("APP_ENV", "").strip().lower() != "staging":
        failures.append("APP_ENV must be staging")
    for name in ("RF_COLLECTION_ENABLED", "RF_HISTORY_PRUNING_ENABLED", "API_DOCS_ENABLED"):
        if env.get(name, "false").strip().lower() not in {"false", "0", "no", "off"}:
            failures.append(f"{name} must be disabled")
    if env.get("UISP_TOKEN", "").strip():
        failures.append("UISP_TOKEN must be unset for isolated staging")
    if env.get("BIND_ADDRESS") != "127.0.0.1":
        failures.append("BIND_ADDRESS must be 127.0.0.1")
    if env.get("UISP_VERIFY_TLS", "true").strip().lower() not in {"true", "1", "yes", "on"}:
        failures.append("UISP_VERIFY_TLS must remain enabled")
    if "*" in env.get("CORS_ORIGINS", ""):
        failures.append("CORS_ORIGINS cannot contain wildcard")
    for name in ("DATABASE_URL", "REDIS_URL"):
        value = env.get(name, "").lower()
        if not value:
            failures.append(f"{name} must be explicitly set for staging")
        elif any(marker in value for marker in ("nwohiobb.com", "10.210.", "10.41.", "136.175.")):
            failures.append(f"{name} references a prohibited production network marker")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-json", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args()
    try:
        compose = json.loads(args.compose_json.read_text(encoding="utf-8"))
        env = parse_env(args.env_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        parser.error(f"Invalid input: {type(exc).__name__}")
    failures = validate(compose, env)
    print(json.dumps({
        "static_configuration_passed": not failures,
        "issues": failures,
        "network_isolation_verified": False,
        "note": "Firewall egress restrictions, DNS, routes and live traffic must be verified separately.",
    }, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
