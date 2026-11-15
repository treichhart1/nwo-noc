"""Explicit, mockable HTTPS providers. Never accepts an applicant-supplied URL."""

import json, logging, os, stat, ipaddress, socket
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime, timezone
import httpx
from app.core.config import settings

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def isolated():
    return (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    )


def secret(path):
    p = Path(path)
    st = p.lstat()
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_uid != os.getuid()
        or st.st_mode & 0o077
        or st.st_size > 4096
    ):
        raise ValueError("Private provider secret file required")
    return p.read_text().strip()


def turnstile(token, submission_key, client=None):
    mode = settings.signup_turnstile_mode
    if mode == "fixture":
        return bool(
            isolated()
            and settings.signup_public_test_mode
            and token == "synthetic-pass:" + str(submission_key)
        )
    if mode != "turnstile" or settings.offline_mode:
        return False
    body = {
        "secret": secret(settings.signup_turnstile_secret_file),
        "response": token,
        "idempotency_key": str(submission_key),
    }
    own = client is None
    client = client or httpx.Client(
        timeout=3, verify=True, follow_redirects=False, trust_env=False
    )
    try:
        r = client.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify", data=body
        )
        if r.status_code != 200 or len(r.content) > 32000:
            return False
        data = r.json()
        stamp = datetime.fromisoformat(
            str(data.get("challenge_ts", "")).replace("Z", "+00:00")
        )
        return (
            data.get("success") is True
            and data.get("action") == "service_signup"
            and data.get("hostname") == urlsplit(settings.signup_public_origin).hostname
            and stamp.tzinfo is not None
            and 0 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 300
        )
    except (httpx.HTTPError, ValueError, TypeError):
        return False
    finally:
        if own:
            client.close()


def geocode(address, client=None):
    mode = settings.signup_geocoder
    if mode == "disabled":
        return None
    if mode == "fixture":
        if not isolated():
            raise ValueError("Isolated provider fixture required")
        return {
            "latitude": 41.4,
            "longitude": -84.2,
            "source": "Synthetic geocode fixture; not validated address location",
        }
    origin = settings.signup_geocoder_origin.rstrip("/")
    p = urlsplit(origin)
    allowed = {
        s.strip().rstrip("/")
        for s in settings.signup_geocoder_approved_origins.split(",")
        if s.strip()
    }
    if (
        settings.offline_mode
        or not settings.signup_geocoder_admin_approved
        or origin not in allowed
        or p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.path
        or p.query
        or p.fragment
        or p.port not in {None, 443}
    ):
        raise ValueError("Approved HTTPS geocoder origin required")
    # Prevent a configured external provider from resolving to internal infrastructure.
    if client is None:
        addresses = {
            a[4][0]
            for a in socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
        }
        if not addresses or any(
            not ipaddress.ip_address(a).is_global for a in addresses
        ):
            raise ValueError("Public geocoder address required")
    own = client is None
    client = client or httpx.Client(
        timeout=3, verify=True, follow_redirects=False, trust_env=False
    )
    try:
        for attempt in range(2):
            try:
                r = client.get(
                    origin + "/search",
                    params={
                        "q": ", ".join(
                            str(address[k])
                            for k in ["street", "unit", "city", "state", "postal_code"]
                            if address.get(k)
                        ),
                        "format": "jsonv2",
                        "limit": 2,
                    },
                    headers={"User-Agent": "NWO-NOC-Service-Qualification/1"},
                )
                if r.status_code in {401, 403, 429}:
                    return None
                if r.status_code >= 500 and attempt == 0:
                    continue
                if r.status_code != 200 or len(r.content) > 64000:
                    return None
                rows = r.json()
                if not isinstance(rows, list) or len(rows) != 1:
                    return None
                lat, lon = float(rows[0]["lat"]), float(rows[0]["lon"])
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    return None
                return {
                    "latitude": lat,
                    "longitude": lon,
                    "source": "Configured Nominatim-compatible provider; address/rooftop accuracy unverified",
                }
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                if attempt:
                    return None
    finally:
        if own:
            client.close()
