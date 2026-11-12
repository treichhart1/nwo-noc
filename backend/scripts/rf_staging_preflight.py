"""Non-invasive RF staging configuration checks; no database or network access."""
import json
import os
import sys


def evaluate(env):
    def truthy(name):
        return env.get(name, "").strip().lower() in {"1", "true", "yes", "on"}

    checks = {
        "rf_collection_disabled": not truthy("RF_COLLECTION_ENABLED"),
        "rf_pruning_disabled": not truthy("RF_HISTORY_PRUNING_ENABLED"),
        "uisp_tls_verification": env.get("UISP_VERIFY_TLS", "true").strip().lower()
        not in {"0", "false", "no", "off"},
        "production_env_not_selected": env.get("APP_ENV", "production").strip().lower()
        in {"staging", "test", "testing", "development"},
    }
    return {"ready": all(checks.values()), "checks": checks}


def main():
    result = evaluate(os.environ)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
