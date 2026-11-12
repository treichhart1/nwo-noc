"""Validate effective Docker Compose JSON without connecting to Docker."""
import json
import sys


def validate(compose):
    services = compose.get("services", {})
    failures = []
    for name in ("db", "redis", "worker"):
        if name not in services:
            failures.append(f"Required service missing: {name}")
        elif services[name].get("ports"):
            failures.append(f"{name} must not publish host ports")
    for name in ("backend", "frontend"):
        service = services.get(name)
        if not service:
            failures.append(f"Required service missing: {name}")
            continue
        published = service.get("ports", [])
        if not published:
            failures.append(f"{name} requires a localhost-bound published port")
        for port in published:
            if not isinstance(port, dict) or port.get("host_ip") != "127.0.0.1":
                failures.append(f"{name} must bind every published port to 127.0.0.1")
    return failures


def main():
    with open(sys.argv[1], encoding="utf-8") as handle:
        compose = json.load(handle)
    failures = validate(compose)
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        raise SystemExit(1)
    print("Compose exposure validation passed: backend/frontend loopback-only; db/redis/worker unpublished")


if __name__ == "__main__":
    main()
