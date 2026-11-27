"""Official Slack contracts with exclusively mocked HTTP and synthetic identities."""

import hashlib
import hmac
import json
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from app.core.config import settings
from app.notifications.slack_contracts import SlackConfig
from app.notifications.slack_transport import (
    NativeSlack,
    SlackFailure,
    signature,
    webhook_url,
)
from app.notifications.slack_sources import payload, metrics
from app.notifications.slack_service import route
from app.notifications.slack_gateway import app as gateway
from app.main import app

TEAM = "T123456789"
APP = "A123456789"
USER = "U123456789"
CHANNEL = "C123456789"
TS = "1795737600.000001"


def config(mode="bot"):
    return SlackConfig(
        mode=mode,
        workspace_id=TEAM,
        app_id=APP,
        bot_user_id=USER,
        approved_channels=[CHANNEL],
        test_channels=[CHANNEL],
        channels={
            x: CHANNEL
            for x in ["default", "critical", "maintenance", "rf", "technician"]
        },
        dashboard_origin="https://noc.example.invalid",
        registration_reference="synthetic-only",
    ).model_dump()


def provider(handler, mode="bot"):
    return NativeSlack(
        config(mode),
        {
            "bot_token": "xoxb-synthetic-not-live",
            "webhooks": {
                CHANNEL: "https://hooks.slack.com/services/"
                + TEAM
                + "/B123456789/0123456789abcdef"
            },
        },
        transport=httpx.MockTransport(handler),
    )


def identity(request):
    assert request.url.host == "slack.com"
    assert request.headers["authorization"] == "Bearer xoxb-synthetic-not-live"
    return httpx.Response(200, json={"ok": True, "team_id": TEAM, "user_id": USER})


def fields():
    return {
        "event": "tower_outage",
        "severity": "critical",
        "incident_id": 1,
        "site_id": 2,
        "detected_at": "2026-11-27T00:00:00+00:00",
        "confidence": "Recorded evidence; physical cause not confirmed",
        "safe_steps": "Inspect private NOC evidence",
        "metrics": {},
        "customer_impact_count": 3,
    }


def test_bot_submission_and_thread():
    seen = []

    def handle(req):
        if req.url.path.endswith("auth.test"):
            return identity(req)
        body = json.loads(req.content)
        seen.append(body)
        assert body["channel"] == CHANNEL and body["thread_ts"] == TS
        assert not body["unfurl_links"] and body["parse"] == "none"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "channel": CHANNEL,
                "ts": TS,
                "message": {"user": USER, "app_id": APP},
            },
        )

    result = provider(handle).send(
        key="unique-not-idempotence-claim",
        destination=CHANNEL,
        payload=payload(fields(), config(), delivery_id=1, thread_ts=TS),
    )
    assert result.timestamp == TS and len(seen) == 1


def test_webhook_has_no_fabricated_receipt():
    def handle(req):
        assert req.url.host == "hooks.slack.com" and "authorization" not in req.headers
        assert "channel" not in json.loads(req.content)
        return httpx.Response(200, text="ok")

    assert (
        provider(handle, "webhook")
        .send(key="one", destination=CHANNEL, payload={"text": "synthetic"})
        .timestamp
        is None
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.slack.com/services/a",
        "https://evil.invalid/services/a",
        "https://hooks.slack.com.evil.invalid/services/a",
        "https://hooks.slack.com:443/services/a",
        "https://hooks.slack.com/services/"
        + TEAM
        + "/B123456789/0123456789abcdef?secret=x",
    ],
)
def test_reject_unapproved_webhook(url):
    with pytest.raises(ValueError):
        webhook_url(url, TEAM)


@pytest.mark.parametrize(
    "status,body,code,rejected",
    [
        (429, {"ok": False}, "rate_limited", True),
        (200, {"ok": False, "error": "invalid_auth"}, "invalid_auth", True),
        (200, {"ok": False, "error": "internal_error"}, "unknown_acceptance", False),
        (200, {"ok": False, "error": "fatal_error"}, "unknown_acceptance", False),
        (500, {}, "unknown_acceptance", False),
        (200, [], "invalid_response", False),
        (200, {"ok": False, "error": "ratelimited"}, "rate_limited", True),
    ],
)
def test_safe_and_ambiguous_provider_errors(status, body, code, rejected):
    def handle(req):
        return httpx.Response(status, json=body, headers={"Retry-After": "999999"})

    with pytest.raises(SlackFailure) as error:
        provider(handle).request("POST", "chat.postMessage", body={})
    assert error.value.code == code and error.value.rejected == rejected
    assert error.value.retry_after <= 3600


def test_timeout_never_proves_nonacceptance():
    def handle(req):
        raise httpx.ReadTimeout("secret provider body")

    with pytest.raises(SlackFailure) as e:
        provider(handle).request("POST", "chat.postMessage")
    assert not e.value.rejected and "secret" not in str(e.value)


def test_channel_denied_before_network():
    def handle(req):
        raise AssertionError("No request permitted")

    with pytest.raises(ValueError):
        provider(handle).send(key="x", destination="C999999999", payload={})


def test_identity_mismatch_before_submission():
    with pytest.raises(SlackFailure) as e:
        provider(
            lambda req: httpx.Response(
                200, json={"ok": True, "team_id": "T999999999", "user_id": USER}
            )
        ).send(key="x", destination=CHANNEL, payload={})
    assert e.value.rejected and e.value.code == "identity_mismatch"


def test_native_disabled_without_network(monkeypatch):
    monkeypatch.setattr(settings, "slack_native_transport_enabled", False)
    with pytest.raises(ValueError):
        NativeSlack(config(), {}, authorized=True)


@pytest.mark.parametrize(
    "change",
    [
        {"interactions_enabled": True, "mode": "webhook"},
        {"dashboard_origin": "http://noc.example.invalid"},
        {"channels": {"default": "C999999999"}},
        {"routing": {"invented_metric": "default"}},
    ],
)
def test_config_fail_closed(change):
    with pytest.raises(ValueError):
        SlackConfig(**(config() | change))


def test_signature_exact_bytes_and_replay_window():
    body = b"payload=%7B%7D"
    now = int(time.time())
    timestamp = str(now)
    secret = "synthetic-secret"
    signed = (
        "v0="
        + hmac.new(
            secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
        ).hexdigest()
    )
    signature(body, timestamp, signed, secret, now)
    for bad, timevalue in [(body + b"x", now), (body, now + 301)]:
        with pytest.raises(ValueError):
            signature(bad, timestamp, signed, secret, timevalue)


def test_payload_allowlist_and_plain_text():
    f = fields() | {
        "customer_name": "private name",
        "phone": "+15555551234",
        "address": "private address",
        "credentials": "private token",
    }
    result = payload(f, config(), delivery_id=12)
    assert all(
        x not in json.dumps(result)
        for x in ["private name", "15555551234", "private address", "private token"]
    )
    assert (
        result["blocks"][0]["text"]["type"] == "plain_text"
        and not result["unfurl_links"]
    )
    assert "unavailable" in result["text"]
    assert metrics(
        {
            "rssi_dbm": -60,
            "password": "private",
            "temperature": float("nan"),
            "cpu_percent": True,
        }
    ) == {"rssi_dbm": -60}


@pytest.mark.parametrize(
    "event",
    [
        "maintenance",
        "overdue_maintenance",
        "rf_degradation",
        "installation",
        "power_failure",
        "recovery",
    ],
)
def test_event_routing(event):
    assert route(config(), fields() | {"event": event}) == CHANNEL


def test_gateway_isolation_and_disabled_default(monkeypatch):
    monkeypatch.setattr(settings, "slack_interaction_gateway_enabled", False)
    with TestClient(gateway) as client:
        assert client.post("/callbacks/slack/interactions").status_code == 503
        for path in [
            "/api/notifications/slack",
            "/api/login",
            "/docs",
            "/openapi.json",
        ]:
            assert client.get(path).status_code == 404


def test_admin_authentication_required():
    with TestClient(app) as client:
        assert client.get("/api/notifications/slack").status_code in {401, 403}
        assert client.post(
            "/api/notifications/slack/authorize",
            json={"confirm": True, "reference": "unauthorized"},
        ).status_code in {401, 403}


def test_reconciliation_exact_identity_no_resend():
    c = config()
    c["history_enabled"] = True
    seen = []

    def handle(req):
        seen.append(req.url.path)
        if req.url.path.endswith("auth.test"):
            return identity(req)
        assert req.method == "GET" and req.url.params["limit"] == "1"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [{"ts": TS, "user": USER, "app_id": APP, "text": "exact"}],
            },
        )

    p = provider(handle)
    p.config = c
    assert p.reconcile(channel=CHANNEL, timestamp=TS, text="exact").timestamp == TS
    assert all("chat.postMessage" not in x for x in seen)


def test_private_installer_https_port_is_supported():
    c = SlackConfig(**(config() | {"dashboard_origin": "https://localhost:18090/"}))
    assert c.dashboard_origin == "https://localhost:18090"
    assert (
        "https://localhost:18090/?incident=1"
        in payload(fields(), c.model_dump(), delivery_id=1)["text"]
    )


@pytest.mark.parametrize(
    "origin",
    [
        "https://localhost:0",
        "https://localhost:65536",
        "https://user:secret@localhost:18090",
        "http://localhost:18090",
    ],
)
def test_private_origin_port_does_not_weaken_url_guards(origin):
    with pytest.raises(ValueError):
        SlackConfig(**(config() | {"dashboard_origin": origin}))
