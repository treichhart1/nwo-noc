"""Least-privilege bounded health reads. No Grafana administration or device transport."""

import json, time, re
import httpx
from datetime import timedelta
from sqlalchemy import select
from app.core.config import settings
from . import service as s
from .models import HealthCheck
from .catalog import DASHBOARDS


def fixture_transport():
    def handler(request):
        if request.url.path.endswith("/api/health"):
            return httpx.Response(200, json={"database": "ok", "version": "12.3.3"})
        if "/api/datasources/uid/" in request.url.path:
            return httpx.Response(200, json={"status": "OK"})
        uid = request.url.path.rsplit("/", 1)[-1]
        if uid in DASHBOARDS:
            return httpx.Response(200, json={"dashboard": {"uid": uid}})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def check(db, u, b, transport=None):
    s.admin(u)
    cfg = s.config(db)
    if not cfg["enabled"]:
        raise ValueError("Grafana integration disabled")
    base = s.origin(cfg["server_url"])
    mock = b.mode == "mock"
    if mock:
        if not s.isolated():
            raise ValueError("Mock checks require offline synthetic staging")
        transport = transport or fixture_transport()
        token = "synthetic-mock-token-not-a-real-credential"
    else:
        if (
            settings.offline_mode
            or not settings.grafana_live_health_enabled
            or transport is not None
        ):
            raise ValueError("Live health reads disabled")
        token = s.private_secret(settings.grafana_service_token_file)
    prior = db.scalar(
        select(HealthCheck)
        .where(
            HealthCheck.mode == b.mode, HealthCheck.config_revision == cfg["revision"]
        )
        .order_by(HealthCheck.id.desc())
        .limit(1)
    )
    if prior and s.now() - prior.checked_at < timedelta(seconds=60):
        return s.view(prior)
    result = {
        "connectivity": "unavailable",
        "datasource": "unavailable",
        "approved_dashboards": [],
        "missing_dashboards": list(cfg["dashboards"]),
        "version": None,
        "mode": "synthetic_mock" if mock else "live_read_only",
        "device_polls": 0,
        "credentials_exposed": False,
    }
    deadline = time.monotonic() + 15

    def read(client, path):
        if time.monotonic() > deadline:
            raise ValueError("health_budget_exceeded")
        with client.stream("GET", base + path) as response:
            if response.status_code != 200:
                raise ValueError("grafana_response_unavailable")
            data = b""
            for chunk in response.iter_bytes():
                data += chunk
                if len(data) > 65536:
                    raise ValueError("grafana_response_budget_exceeded")
            try:
                v = json.loads(data)
            except (ValueError, UnicodeError):
                raise ValueError("grafana_invalid_json") from None
            if not isinstance(v, dict):
                raise ValueError("grafana_invalid_response")
            return v

    error = None
    try:
        with httpx.Client(
            transport=transport,
            verify=True,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(2, connect=1, pool=1),
            headers={"Authorization": "Bearer " + token},
        ) as client:
            h = read(client, "/api/health")
            version = h.get("version")
            if (
                h.get("database") != "ok"
                or not isinstance(version, str)
                or not re.fullmatch(r"(?:11|12|13)\.[0-9]{1,3}\.[0-9]{1,3}", version)
            ):
                raise ValueError("grafana_health_contract_unverified")
            result.update(connectivity="responding", version=version)
            d = read(client, "/api/datasources/uid/noc-readonly/health")
            if d.get("status") != "OK":
                raise ValueError("grafana_datasource_unavailable")
            result["datasource"] = "responding"
            for uid in cfg["dashboards"]:
                try:
                    h = read(client, "/api/dashboards/uid/" + uid)
                    if (
                        not isinstance(h.get("dashboard"), dict)
                        or h["dashboard"].get("uid") != uid
                    ):
                        raise ValueError("dashboard_contract_invalid")
                    result["approved_dashboards"].append(uid)
                    result["missing_dashboards"].remove(uid)
                except ValueError:
                    error = "dashboard_provisioning_incomplete"
    except httpx.TimeoutException:
        error = "grafana_timeout"
    except httpx.HTTPError:
        error = "grafana_connection_failed"
    except ValueError as exc:
        error = (
            str(exc)
            if str(exc)
            in {
                "health_budget_exceeded",
                "grafana_response_unavailable",
                "grafana_response_budget_exceeded",
                "grafana_invalid_json",
                "grafana_invalid_response",
                "grafana_health_contract_unverified",
                "grafana_datasource_unavailable",
            }
            else "grafana_validation_failed"
        )
    result["error_code"] = error
    x = HealthCheck(
        mode=b.mode,
        status="error" if error else "ok",
        result=result,
        config_revision=cfg["revision"],
        checked_by=u.id,
    )
    db.add(x)
    db.flush()
    s.event(
        db,
        u,
        "health_check",
        x.id,
        {"mode": b.mode, "status": x.status, "error_code": error},
    )
    return s.view(x)
