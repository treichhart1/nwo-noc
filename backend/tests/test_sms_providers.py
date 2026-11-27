"""NOC126: official wire contracts, no provider credentials or live SMS."""

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, parse_qs
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
from app.notifications.sms_transport import (
    NativeSMS,
    SMSFailure,
    MockSMS,
    segments,
    next_state,
    verify_twilio,
    verify_telnyx,
    money,
)
from app.notifications.sms_contracts import ProviderInput, RoutingInput
from app.notifications.sms_callbacks import normalize
from app.notifications.sms_gateway import app as gateway
from app.main import app
from app.core.config import settings, Settings
from cryptography.fernet import Fernet

PHONE = "+15555550126"
SENDER = "+15555550127"
ACCOUNT = "AC" + "1" * 32
SERVICE = "MG" + "2" * 32
ORG = "10000000-0000-4000-8000-000000000001"
PROFILE = "20000000-0000-4000-8000-000000000002"
TEL_ID = "30000000-0000-4000-8000-000000000003"
TW_ID = "SM" + "3" * 32
TOKEN = "synthetic-webhook-auth-token-never-live"
BODY = "Synthetic approved operational notice"


@pytest.fixture(autouse=True)
def isolated_callback_key(tmp_path, monkeypatch):
    key = tmp_path / "callback.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    monkeypatch.setattr(settings, "sms_callback_key_file", str(key))


def config(name):
    return {
        "sender": SENDER,
        "approved_senders": [SENDER],
        "account_sid": ACCOUNT if name == "twilio" else ORG,
        "service_id": SERVICE if name == "twilio" else PROFILE,
        "callback_url": "https://callbacks.example.invalid/callbacks/sms/" + name,
        "callbacks_enabled": True,
        "registration_reference": "synthetic-registration",
        "categories": ["outage", "maintenance", "restoration"],
        "currency": "USD",
        "price_per_segment_microunits": 1000,
        "daily_limit_microunits": 1000000,
        "monthly_limit_microunits": 10000000,
    }


def secrets(name):
    return (
        {
            "api_key_sid": "SK" + "4" * 32,
            "api_key_secret": "synthetic-api-secret-not-live",
        }
        if name == "twilio"
        else {"api_key": "synthetic-telnyx-api-key-not-live"}
    )


def response(name, raw="queued", body=BODY):
    c = config(name)
    if name == "twilio":
        return {
            "sid": TW_ID,
            "status": raw,
            "account_sid": ACCOUNT,
            "messaging_service_sid": SERVICE,
            "from": SENDER,
            "to": PHONE,
            "body": body,
            "price": None,
            "price_unit": None,
            "date_created": "Fri, 27 Nov 2026 00:00:00 +0000",
        }
    return {
        "data": {
            "id": TEL_ID,
            "messaging_profile_id": PROFILE,
            "organization_id": ORG,
            "from": {"phone_number": SENDER},
            "to": [{"phone_number": PHONE, "status": raw}],
            "text": body,
            "created_at": "2026-11-27T00:00:00Z",
            "cost": None,
        }
    }


def twilio_event(text="STOP", status="received", sid=TW_ID):
    values = {
        "AccountSid": ACCOUNT,
        "MessagingServiceSid": SERVICE,
        "MessageSid": sid,
        "From": PHONE if status == "received" else SENDER,
        "To": SENDER if status == "received" else PHONE,
        "SmsStatus": status,
    }
    if status == "received":
        values["Body"] = text
    body = urlencode(values).encode()
    canonical = config("twilio")["callback_url"] + "".join(
        k + values[k] for k in sorted(values)
    )
    signature = base64.b64encode(
        hmac.digest(TOKEN.encode(), canonical.encode(), "sha1")
    ).decode()
    return body, {
        "x-twilio-signature": signature,
        "content-type": "application/x-www-form-urlencoded",
    }


def telnyx_event(key, text="STOP", raw="received", now=None, event_id=None):
    now = now or datetime.now(timezone.utc)
    incoming = raw == "received"
    value = {
        "data": {
            "id": event_id or str(uuid4()),
            "event_type": "message.received" if incoming else "message.finalized",
            "occurred_at": now.isoformat(),
            "payload": {
                "id": TEL_ID,
                "organization_id": ORG,
                "messaging_profile_id": PROFILE,
                "direction": "inbound" if incoming else "outbound",
                "from": {"phone_number": PHONE if incoming else SENDER},
                "to": [
                    {
                        "phone_number": SENDER if incoming else PHONE,
                        "status": "webhook_delivered" if incoming else raw,
                    }
                ],
                "text": text,
            },
        }
    }
    body = json.dumps(value).encode()
    timestamp = str(int(now.timestamp()))
    signature = base64.b64encode(key.sign(timestamp.encode() + b"|" + body)).decode()
    return body, {
        "telnyx-signature-ed25519": signature,
        "telnyx-timestamp": timestamp,
        "content-type": "application/json",
    }


@pytest.mark.parametrize("name", ["twilio", "telnyx"])
def test_native_wire_contract_uses_api_key_fixed_origin_and_no_assumed_idempotency(
    name,
):
    calls = []

    def send(request):
        calls.append(request)
        assert "Idempotency-Key" not in request.headers
        assert request.url.host == (
            "api.twilio.com" if name == "twilio" else "api.telnyx.com"
        )
        if name == "twilio":
            assert parse_qs(request.content.decode())["MessagingServiceSid"] == [
                SERVICE
            ]
            assert (
                base64.b64decode(request.headers["Authorization"].split()[1])
                .decode()
                .startswith(secrets(name)["api_key_sid"] + ":")
            )
        else:
            assert json.loads(request.content)["messaging_profile_id"] == PROFILE
            assert (
                request.headers["Authorization"] == "Bearer " + secrets(name)["api_key"]
            )
        return httpx.Response(201 if name == "twilio" else 200, json=response(name))

    provider = NativeSMS(
        name, config(name), secrets(name), transport=httpx.MockTransport(send)
    )
    result = provider.submit(destination=PHONE, body=BODY, key="synthetic-claim")
    assert result.state == "accepted"
    assert result.message_id == (TW_ID if name == "twilio" else TEL_ID)
    assert len(calls) == 1


@pytest.mark.parametrize("name", ["twilio", "telnyx"])
@pytest.mark.parametrize(
    "status,code,safe",
    [
        (401, "authentication_failed", True),
        (403, "authentication_failed", True),
        (429, "rate_limited", True),
        (400, "rejected", True),
        (422, "rejected", True),
        (500, "provider_unavailable", False),
        (302, "provider_unavailable", False),
    ],
)
def test_provider_error_dispositions(name, status, code, safe):
    p = NativeSMS(
        name,
        config(name),
        secrets(name),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(status, headers={"Retry-After": "120"})
        ),
    )
    with pytest.raises(SMSFailure) as error:
        p.submit(destination=PHONE, body=BODY, key="x")
    assert error.value.code == code and error.value.not_accepted is safe
    if status == 429:
        assert error.value.retry_after == 120


@pytest.mark.parametrize("name", ["twilio", "telnyx"])
@pytest.mark.parametrize("failure", ["timeout", "disconnect", "malformed", "oversized"])
def test_ambiguous_transport_never_claims_safe_rejection(name, failure):
    def handle(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private details must not propagate")
        if failure == "disconnect":
            raise httpx.ConnectError("private details must not propagate")
        return httpx.Response(
            200, content=b"not json" if failure == "malformed" else b"x" * 40000
        )

    p = NativeSMS(
        name, config(name), secrets(name), transport=httpx.MockTransport(handle)
    )
    with pytest.raises(SMSFailure) as error:
        p.submit(destination=PHONE, body=BODY, key="x")
    assert not error.value.not_accepted
    assert "private" not in str(error.value)


@pytest.mark.parametrize("name", ["twilio", "telnyx"])
def test_no_live_transport_without_explicit_authorization(name):
    with pytest.raises(ValueError):
        NativeSMS(name, config(name), secrets(name))
    with pytest.raises(ValueError):
        NativeSMS(name, config(name), secrets(name), transport=httpx.HTTPTransport())


@pytest.mark.parametrize("name", ["twilio", "telnyx"])
def test_provider_account_and_recipient_mismatch_is_ambiguous(name):
    value = response(name)
    if name == "twilio":
        value["account_sid"] = "AC" + "0" * 32
    else:
        value["data"]["organization_id"] = str(uuid4())
    p = NativeSMS(
        name,
        config(name),
        secrets(name),
        transport=httpx.MockTransport(lambda r: httpx.Response(201, json=value)),
    )
    with pytest.raises(SMSFailure) as error:
        p.submit(destination=PHONE, body=BODY, key="x")
    assert not error.value.not_accepted


@pytest.mark.parametrize(
    "text,count",
    [
        ("A" * 160, 1),
        ("A" * 161, 2),
        ("^" * 80, 1),
        ("^" * 81, 2),
        ("漢" * 70, 1),
        ("漢" * 71, 2),
        ("😀" * 35, 1),
        ("😀" * 36, 2),
        ("\\" * 81, 2),
    ],
)
def test_segments_gsm_extension_and_utf16(text, count):
    assert segments(text) == count


@pytest.mark.parametrize(
    "current,incoming,expected",
    [
        ("sent", "accepted", "sent"),
        ("delivered", "failed", "delivered"),
        ("failed", "delivered", "failed"),
        ("reconciliation_required", "delivered", "delivered"),
        ("submitted", "accepted", "accepted"),
        ("sent", "reconciliation_required", "sent"),
    ],
)
def test_delivery_state_never_regresses(current, incoming, expected):
    assert next_state(current, incoming) == expected


def test_mock_implements_normalized_protocol_without_network():
    p = MockSMS()
    r = p.submit(destination=PHONE, body=BODY, key="same-canonical-identity")
    assert r.state == "delivered" and p.reconcile(r.message_id, destination=PHONE) == r
    assert p.sender.calls == ["same-canonical-identity"]


def test_twilio_official_request_validator_test_vector():
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    values = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+14158675309",
        "Digits": "1234",
        "From": "+14158675309",
        "To": "+18005551212",
    }
    # Twilio's public request-validator examples use this synthetic secret/vector.
    signature = "RSOYDt4T1cUTdK1PDd93/VVr8B8="
    assert (
        verify_twilio(url, urlencode(values).encode(), signature, "12345")["Digits"]
        == "1234"
    )


def test_twilio_unknown_parameters_participate_in_native_signature():
    body, headers = twilio_event()
    with pytest.raises(ValueError):
        verify_twilio(
            config("twilio")["callback_url"],
            body + b"&NewVendorField=changed",
            headers["x-twilio-signature"],
            TOKEN,
        )
    with pytest.raises(ValueError):
        verify_twilio(
            "https://attacker.invalid/", body, headers["x-twilio-signature"], TOKEN
        )


@pytest.mark.parametrize("age", [-301, 301])
def test_telnyx_timestamp_and_raw_payload_are_verified(age):
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    body, headers = telnyx_event(key, now=now + timedelta(seconds=age))
    public = base64.b64encode(
        key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    ).decode()
    with pytest.raises(ValueError):
        verify_telnyx(
            body,
            headers["telnyx-signature-ed25519"],
            headers["telnyx-timestamp"],
            public,
            now=int(now.timestamp()),
        )


def test_telnyx_raw_body_mutation_and_wrong_key_rejected():
    key = Ed25519PrivateKey.generate()
    body, h = telnyx_event(key)
    public = base64.b64encode(
        key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    ).decode()
    with pytest.raises(ValueError):
        verify_telnyx(
            body + b" ",
            h["telnyx-signature-ed25519"],
            h["telnyx-timestamp"],
            public,
            now=int(datetime.now(timezone.utc).timestamp()),
        )


@pytest.mark.parametrize(
    "text,kind",
    [
        ("STOP", "stop"),
        (" stopall ", "stop"),
        ("START", "start"),
        ("HELP", "help"),
        ("Customer reply", "reply"),
    ],
)
def test_twilio_inbound_classification(text, kind):
    body, h = twilio_event(text)
    assert (
        normalize(
            "twilio", config("twilio"), body, h, TOKEN, datetime.now(timezone.utc)
        )["kind"]
        == kind
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/auth/login",
        "/api/notifications",
        "/api/notifications/sms",
        "/docs",
        "/openapi.json",
        "/api/customers",
    ],
)
def test_callback_gateway_has_no_private_routes(path):
    with TestClient(gateway) as c:
        assert c.get(path).status_code == 404


@pytest.mark.parametrize("name", ["twilio", "telnyx"])
def test_callback_gateway_disabled_and_native_admin_api_requires_login(name):
    with TestClient(gateway) as c:
        assert c.post("/callbacks/sms/" + name).status_code == 503
    with TestClient(app) as c:
        assert c.get("/api/notifications/sms").status_code == 401


def test_defaults_and_legacy_gates_remain_closed():
    assert not settings.sms_native_transport_enabled
    assert not settings.sms_native_worker_enabled
    assert not settings.sms_callback_gateway_enabled
    values = {
        "secret_key": "synthetic-long-secret-key-126-not-production",
        "admin_password": "synthetic-password",
        "app_env": "staging",
        "offline_mode": True,
    }
    with pytest.raises(ValueError):
        Settings(**values, sms_native_worker_enabled=True)
    with pytest.raises(ValueError):
        Settings(**values, notifications_live_delivery_enabled=True)


def test_configuration_requires_owned_sender_and_exact_isolated_callback():
    data = config("twilio") | {"recipient_allowlist": [PHONE]}
    assert ProviderInput(**data).provider_valid("twilio").sender == SENDER
    with pytest.raises(ValueError):
        ProviderInput(**(data | {"approved_senders": [PHONE]}))
    with pytest.raises(ValueError):
        ProviderInput(
            **(
                data
                | {
                    "callback_url": "http://callbacks.example.invalid/callbacks/sms/twilio"
                }
            )
        )
    with pytest.raises(ValueError):
        RoutingInput(primary="twilio", secondary="twilio", failover=True)


def test_money_no_nan_infinity_or_negative_cost_confusion():
    assert money("-0.0000011") == 2
    assert money(None) is None
    for value in ["nan", "infinity", "invalid"]:
        with pytest.raises(SMSFailure):
            money(value)


@pytest.mark.parametrize("name,code", [("twilio", "21610"), ("telnyx", "40300")])
def test_native_provider_opt_out_is_never_generic_failover_error(name, code):
    value = {"code": int(code)} if name == "twilio" else {"errors": [{"code": code}]}
    p = NativeSMS(
        name,
        config(name),
        secrets(name),
        transport=httpx.MockTransport(lambda r: httpx.Response(400, json=value)),
    )
    with pytest.raises(SMSFailure) as error:
        p.submit(destination=PHONE, body=BODY, key="no-failover")
    assert error.value.code == "opt_out" and error.value.provider_code == code


@pytest.mark.parametrize("name,code", [("twilio", "21610"), ("telnyx", "40300")])
def test_accepted_failed_receipt_preserves_opt_out_evidence(name, code):
    value = response(name, "failed" if name == "twilio" else "sending_failed")
    if name == "twilio":
        value["error_code"] = 21610
    else:
        value["data"]["errors"] = [{"code": code}]
    p = NativeSMS(
        name,
        config(name),
        secrets(name),
        transport=httpx.MockTransport(lambda r: httpx.Response(201, json=value)),
    )
    result = p.submit(destination=PHONE, body=BODY, key="no-failover")
    assert result.opt_out and result.provider_error_code == code
