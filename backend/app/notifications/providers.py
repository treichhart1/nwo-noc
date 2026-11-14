"""Interchangeable provider protocol; NOC-96 exposes no live network transport."""

from typing import Protocol
import hashlib, hmac, json
import httpx


class DeliveryFailure(RuntimeError):
    def __init__(self, code="provider_unavailable", permanent=False):
        self.code = (
            code
            if code
            in {
                "provider_unavailable",
                "rate_limited",
                "authentication_failed",
                "invalid_response",
                "timeout",
            }
            else "provider_unavailable"
        )
        self.permanent = permanent
        super().__init__(self.code)


class Sender(Protocol):
    def send(self, *, key: str, destination: str, payload: dict) -> str: ...


class MockSender:
    def __init__(self, failures=0, permanent=False):
        self.failures = failures
        self.permanent = permanent
        self.calls = []

    def send(self, *, key, destination, payload):
        # No phone/payload is retained by the mock provider or logs.
        self.calls.append(key)
        if self.failures:
            self.failures -= 1
            raise DeliveryFailure(permanent=self.permanent)
        return "mock-" + hashlib.sha256(key.encode()).hexdigest()[:40]


class MockSlack:
    """Slack-shaped webhook/chat.postMessage payload contracts, mock transport only."""

    def __init__(self, transport, mode="webhook"):
        if not isinstance(transport, httpx.MockTransport) or mode not in {
            "webhook",
            "api",
        }:
            raise ValueError("Mock Slack transport required; live delivery unavailable")
        self.transport, self.mode = transport, mode

    def send(self, *, key, destination, payload):
        try:
            with httpx.Client(
                transport=self.transport,
                verify=True,
                follow_redirects=False,
                trust_env=False,
                timeout=5,
            ) as client:
                r = client.post(
                    "https://slack-fixture.invalid/"
                    + ("webhook" if self.mode == "webhook" else "api/chat.postMessage"),
                    json=payload | {"channel": destination},
                    headers={"Idempotency-Key": key},
                )
                if r.status_code == 429:
                    raise DeliveryFailure("rate_limited")
                if r.status_code in (401, 403):
                    raise DeliveryFailure("authentication_failed", True)
                if r.status_code != 200:
                    raise DeliveryFailure()
                if self.mode == "api" and r.json().get("ok") is not True:
                    raise DeliveryFailure("invalid_response", True)
                return "mock-slack-" + hashlib.sha256(key.encode()).hexdigest()[:32]
        except DeliveryFailure:
            raise
        except httpx.TimeoutException:
            raise DeliveryFailure("timeout") from None
        except Exception:
            raise DeliveryFailure("invalid_response", True) from None


def verified_callback(
    secret: bytes, body: bytes, signature: str, timestamp: int, now: int
) -> bool:
    if abs(now - timestamp) > 300 or len(body) > 8192:
        return False
    expected = hmac.new(
        secret, str(timestamp).encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


class TwilioMockAdapter(MockSender):
    """Interchangeable SMS wire-contract adapter, mock HTTP transport ONLY.

    Prepares Twilio's Messages resource form/Basic-auth shape against a synthetic
    origin. This is not live Twilio delivery or verification of account settings.
    """

    def __init__(self, transport, *, account_sid, token, from_number):
        import re
        from app.customers.contracts import e164

        if (
            not isinstance(transport, httpx.MockTransport)
            or not re.fullmatch(r"AC[a-fA-F0-9]{32}", account_sid)
            or not e164(from_number)
        ):
            raise ValueError("Validated mock SMS configuration required")
        token = (
            token.get_secret_value() if hasattr(token, "get_secret_value") else token
        )
        if not isinstance(token, str) or not 1 <= len(token) <= 512:
            raise ValueError("Bounded private SMS token required")
        super().__init__()
        self.transport = transport
        self.account = account_sid
        self._token = token
        self.sender = from_number

    def send(self, *, key, destination, payload):
        from app.customers.contracts import e164
        import re

        if (
            not e164(destination)
            or not isinstance(payload.get("body"), str)
            or len(payload["body"]) > 640
        ):
            raise DeliveryFailure("invalid_response", True)
        self.calls.append(key)
        try:
            with httpx.Client(
                transport=self.transport,
                verify=True,
                follow_redirects=False,
                trust_env=False,
                timeout=5,
                auth=(self.account, self._token),
            ) as client:
                response = client.post(
                    f"https://sms-fixture.invalid/2010-04-01/Accounts/{self.account}/Messages.json",
                    data={
                        "To": destination,
                        "From": self.sender,
                        "Body": payload["body"],
                    },
                    headers={"Idempotency-Key": key},
                )
                if response.status_code == 429:
                    raise DeliveryFailure("rate_limited")
                if response.status_code in (401, 403):
                    raise DeliveryFailure("authentication_failed", True)
                if response.status_code >= 500:
                    raise DeliveryFailure()
                if response.status_code != 201:
                    raise DeliveryFailure("invalid_response", True)
                value = response.json()
                if not re.fullmatch(
                    r"SM[a-fA-F0-9]{32}", str(value.get("sid", ""))
                ) or value.get("status") not in {
                    "queued",
                    "accepted",
                    "sent",
                    "delivered",
                }:
                    raise DeliveryFailure("invalid_response", True)
                return "mock-sms-" + hashlib.sha256(key.encode()).hexdigest()[:32]
        except DeliveryFailure:
            raise
        except httpx.TimeoutException:
            raise DeliveryFailure("timeout") from None
        except Exception:
            raise DeliveryFailure("invalid_response", True) from None
