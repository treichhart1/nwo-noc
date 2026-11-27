#!/usr/bin/env python3
"""Read-only public gateway deployment guard. Does not launch or publish services."""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs


def validate(environment):
    image = environment.get("NOC_STATUS_BACKEND_IMAGE", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[a-f0-9]{64}", image):
        raise ValueError("Reviewed immutable backend image digest required")
    proxy = environment.get("NOC_STATUS_PROXY_IMAGE", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[a-f0-9]{64}", proxy):
        raise ValueError("Reviewed immutable public proxy image required")
    for name in [
        "NOC_STATUS_DB_URL_FILE",
        "NOC_STATUS_RATE_KEY_FILE",
        "NOC_STATUS_PG_CA_FILE",
    ]:
        path = Path(environment.get(name, ""))
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise ValueError("Absolute regular gateway-only secret/CA files required")
        if name != "NOC_STATUS_PG_CA_FILE" and path.stat().st_mode & 0o077:
            raise ValueError("Gateway-only secrets require0600 permissions")
    u = urlsplit(Path(environment["NOC_STATUS_DB_URL_FILE"]).read_text().strip())
    if (
        u.scheme != "postgresql+psycopg"
        or u.username != "noc_status_public"
        or not u.password
        or not u.hostname
        or u.path != "/nwo_noc"
        or parse_qs(u.query).get("sslmode") != ["verify-full"]
    ):
        raise ValueError(
            "Restricted public role and verify-full PostgreSQL TLS required"
        )
    return {
        "status": "PASS",
        "scope": "configuration only; deployment/website authorization not granted",
        "image": image,
        "private_router": "excluded",
        "tls_database": "verify-full",
    }


if __name__ == "__main__":
    try:
        print(json.dumps(validate(os.environ)))
    except (ValueError, OSError):
        raise SystemExit(
            "Gateway preflight FAILED: require approved immutable image, gateway-only0600 secrets and restricted PostgreSQL verify-full TLS. Credentials are not logged."
        ) from None
