"""Offline, read-only deployment environment preflight. Never prints secret values."""
import os
import sys
from pathlib import Path


def parse_env(contents):
    result = {}
    for raw in contents.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def validate(env):
    failures = []
    if env.get("APP_ENV", "").lower() != "production":
        failures.append("APP_ENV must be production")
    if env.get("BIND_ADDRESS") != "127.0.0.1":
        failures.append("BIND_ADDRESS must be 127.0.0.1")
    for name in ("API_DOCS_ENABLED", "RF_COLLECTION_ENABLED", "RF_HISTORY_PRUNING_ENABLED"):
        if env.get(name, "false").lower() not in ("false", "0", "no", "off"):
            failures.append(f"{name} must be disabled for initial deployment")
    if env.get("UISP_VERIFY_TLS", "true").lower() not in ("true", "1", "yes", "on"):
        failures.append("UISP_VERIFY_TLS must be enabled")
    key = env.get("SECRET_KEY", "")
    if len(key) < 32 or key.startswith("replace-"):
        failures.append("SECRET_KEY must be a non-placeholder value of at least 32 characters")
    password = env.get("ADMIN_PASSWORD", "")
    if not password or password in ("ChangeThisPassword123!", "replace-admin-password"):
        failures.append("ADMIN_PASSWORD must not be an example password")
    db_password = env.get("POSTGRES_PASSWORD", "")
    if not db_password or db_password.startswith("replace-"):
        failures.append("POSTGRES_PASSWORD must not be a placeholder")
    if "*" in env.get("CORS_ORIGINS", ""):
        failures.append("CORS_ORIGINS must not contain wildcard")
    return failures


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/validate_deployment_env.py PATH_TO_ENV", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print("ERROR: environment file not found", file=sys.stderr)
        return 1
    failures = validate(parse_env(path.read_text(encoding="utf-8")))
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    print("Deployment environment preflight passed (offline, no secrets printed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
