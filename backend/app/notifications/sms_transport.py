"""Native SMS wire contracts. No retry, implicit idempotency or fallback at HTTP level.

Endpoints are fixed vendor origins. Production use requires independent host and
administrator authorization; all automated acceptance uses MockTransport.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import base64
import hashlib
import hmac
import re
from typing import Protocol
from urllib.parse import parse_qs
from uuid import UUID

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from app.customers.contracts import e164

STATES = {
    "queued",
    "submitted",
    "accepted",
    "sent",
    "delivered",
    "undelivered",
    "failed",
    "suppressed",
    "reconciliation_required",
}
TWILIO_STATES = {
    "queued": "accepted",
    "accepted": "accepted",
    "scheduled": "accepted",
    "sending": "accepted",
    "sent": "sent",
    "delivered": "delivered",
    "undelivered": "undelivered",
    "failed": "failed",
    "canceled": "failed",
}
TELNYX_STATES = {
    "queued": "accepted",
    "sending": "accepted",
    "sent": "sent",
    "delivered": "delivered",
    "sending_failed": "failed",
    "delivery_failed": "undelivered",
    "delivery_unconfirmed": "reconciliation_required",
}
TERMINAL = {"delivered", "undelivered", "failed", "suppressed"}


@dataclass(frozen=True)
class Submission:
    message_id: str
    state: str
    raw_status: str
    reported_cost_microunits: int | None = None
    currency: str | None = None
    provider_error_code: str | None = None
    opt_out: bool = False


class SMSFailure(RuntimeError):
    """Only explicitly rejected requests are safe to retry/fail over."""

    def __init__(self, code, *, not_accepted=False, retry_after=60, provider_code=None):
        self.code = (
            code
            if code
            in {
                "authentication_failed",
                "rate_limited",
                "rejected",
                "opt_out",
                "timeout",
                "invalid_response",
                "provider_unavailable",
            }
            else "provider_unavailable"
        )
        self.provider_code = (
            provider_code
            if isinstance(provider_code, str)
            and re.fullmatch(r"[0-9]{3,12}", provider_code)
            else None
        )
        self.not_accepted = bool(not_accepted)
        self.retry_after = min(3600, max(5, retry_after))
        super().__init__(self.code)


class SMSProvider(Protocol):
    name: str

    def submit(self, *, destination: str, body: str, key: str) -> Submission: ...
    def reconcile(self, message_id: str, *, destination: str) -> Submission: ...


def segments(body):
    # GSM 03.38 extension characters consume two septets. Astral Unicode
    # consumes two UTF-16 code units; predictions are not billed segment counts.
    if not isinstance(body, str) or not 1 <= len(body) <= 640:
        raise ValueError("Bounded SMS content required")
    basic = "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
    extended = "^{}\\[~]|€\f"
    gsm = all(c in basic or c in extended for c in body)
    units = (
        sum(2 if c in extended else 1 for c in body)
        if gsm
        else len(body.encode("utf-16-be")) // 2
    )
    single, multi = (160, 153) if gsm else (70, 67)
    return 1 if units <= single else (units + multi - 1) // multi


def money(value):
    if value is None:
        return None
    try:
        d = abs(Decimal(str(value)))
        if not d.is_finite() or d > 100000:
            raise ValueError()
        return int((d * 1000000).to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError, TypeError):
        raise SMSFailure("invalid_response") from None


def message_id(provider, value):
    if provider == "twilio":
        if not isinstance(value, str) or not re.fullmatch(
            r"(?:SM|MM)[a-fA-F0-9]{32}", value
        ):
            raise SMSFailure("invalid_response")
    else:
        try:
            if str(UUID(value)) != value:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise SMSFailure("invalid_response") from None
    return value


def next_state(current, incoming):
    if incoming not in STATES:
        raise ValueError("Unknown normalized SMS state")
    if current in TERMINAL:
        return current
    rank = {
        "queued": 0,
        "submitted": 1,
        "reconciliation_required": 1,
        "accepted": 2,
        "sent": 3,
    }
    if incoming == "reconciliation_required" and current in {"accepted", "sent"}:
        return current
    if incoming in TERMINAL or incoming == "reconciliation_required":
        return incoming
    return incoming if rank.get(incoming, -1) >= rank.get(current, -1) else current


def provider_error(name, value):
    raw = (
        [value.get("error_code"), value.get("code")]
        if name == "twilio"
        else [
            x.get("code") for x in value.get("errors", [])[:20] if isinstance(x, dict)
        ]
    )
    codes = [
        str(x) for x in raw if x is not None and re.fullmatch(r"[0-9]{3,12}", str(x))
    ]
    opt_out = "21610" if name == "twilio" else "40300"
    code = opt_out if opt_out in codes else codes[0] if codes else None
    return code, code == opt_out


class NativeSMS:
    def __init__(self, name, config, credentials, *, transport=None, authorized=False):
        if name not in {"twilio", "telnyx"}:
            raise ValueError("Unsupported native SMS provider")
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise ValueError("Only an explicit mock transport can be injected")
        if transport is None:
            from app.core.config import settings

            if (
                not authorized
                or not settings.sms_native_transport_enabled
                or not settings.sms_egress_approved
                or settings.offline_mode
            ):
                raise ValueError("Live transport is not independently host-authorized")
        self.name, self.config, self._credentials = name, config, credentials
        self._transport = transport
        self.last_message_body = None
        self.last_created_at = None
        self.base = (
            "https://api.twilio.com" if name == "twilio" else "https://api.telnyx.com"
        )

    def request(self, method, path, **kwargs):
        auth = (
            (self._credentials["api_key_sid"], self._credentials["api_key_secret"])
            if self.name == "twilio"
            else None
        )
        headers = (
            {"Authorization": "Bearer " + self._credentials["api_key"]}
            if self.name == "telnyx"
            else {}
        )
        try:
            with httpx.Client(
                transport=self._transport,
                verify=True,
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(5, connect=3),
                auth=auth,
            ) as client:
                # No HTTP retry and no unsupported Idempotency-Key claim.
                with client.stream(
                    method, self.base + path, headers=headers, **kwargs
                ) as response:
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 32768:
                            raise SMSFailure("invalid_response")
                    import json

                    try:
                        error_value = (
                            json.loads(data)
                            if 400 <= response.status_code < 500
                            else {}
                        )
                        error_value = (
                            error_value if isinstance(error_value, dict) else {}
                        )
                        vendor_code, opted_out = provider_error(self.name, error_value)
                    except (ValueError, TypeError, AttributeError):
                        vendor_code, opted_out = None, False
                    if opted_out:
                        raise SMSFailure(
                            "opt_out",
                            not_accepted=method == "POST",
                            provider_code=vendor_code,
                        )
                    if response.status_code in {401, 403}:
                        raise SMSFailure(
                            "authentication_failed",
                            not_accepted=True,
                            provider_code=vendor_code,
                        )
                    if response.status_code == 429:
                        retry = response.headers.get("Retry-After", "60")
                        raise SMSFailure(
                            "rate_limited",
                            not_accepted=True,
                            retry_after=int(retry) if retry.isdigit() else 60,
                            provider_code=vendor_code,
                        )
                    # Only documented request rejection responses prove no acceptance.
                    if response.status_code in {400, 404, 422}:
                        raise SMSFailure(
                            "rejected",
                            not_accepted=method == "POST",
                            provider_code=vendor_code,
                        )
                    if response.status_code not in {200, 201, 202}:
                        raise SMSFailure("provider_unavailable")
                    import json

                    value = json.loads(data)
                    if not isinstance(value, dict):
                        raise ValueError()
                    return value
        except SMSFailure:
            raise
        except httpx.TimeoutException:
            raise SMSFailure("timeout") from None
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise SMSFailure("invalid_response") from None

    def parse(self, value, destination):
        try:
            if self.name == "twilio":
                if (
                    value.get("account_sid") != self.config["account_sid"]
                    or value.get("to") != destination
                    or value.get("from") != self.config["sender"]
                ):
                    raise ValueError()
                if value.get("messaging_service_sid") != self.config["service_id"]:
                    raise ValueError()
                raw = value["status"]
                state = TWILIO_STATES.get(raw, "reconciliation_required")
                price, currency = money(value.get("price")), value.get("price_unit")
            else:
                value = value["data"]
                if (
                    value.get("messaging_profile_id") != self.config["service_id"]
                    or value.get("organization_id") != self.config["account_sid"]
                ):
                    raise ValueError()
                if (
                    value.get("from", {}).get("phone_number") != self.config["sender"]
                    or len(value.get("to", [])) != 1
                    or value["to"][0].get("phone_number") != destination
                ):
                    raise ValueError()
                raw = value["to"][0]["status"]
                state = TELNYX_STATES.get(raw, "reconciliation_required")
                cost = value.get("cost") or {}
                price, currency = money(cost.get("amount")), cost.get("currency")
            self.last_created_at = (
                value.get("date_created")
                if self.name == "twilio"
                else value.get("created_at")
            )
            self.last_message_body = (
                value.get("body") if self.name == "twilio" else value.get("text")
            )
            identifier = message_id(
                self.name, value["sid"] if self.name == "twilio" else value["id"]
            )
            if (
                not isinstance(raw, str)
                or not 1 <= len(raw) <= 40
                or not re.fullmatch(r"[a-z_]+", raw)
            ):
                raise ValueError()
            if currency is not None and currency.upper() != self.config["currency"]:
                raise ValueError()
            code, opted_out = provider_error(self.name, value)
            return Submission(
                identifier,
                state,
                raw,
                price,
                currency.upper() if currency else None,
                provider_error_code=code,
                opt_out=opted_out,
            )
        except SMSFailure:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise SMSFailure("invalid_response") from None

    def submit(self, *, destination, body, key):
        if not e164(destination):
            raise ValueError("E164 destination required")
        segments(body)
        if self.name == "twilio":
            path = f"/2010-04-01/Accounts/{self.config['account_sid']}/Messages.json"
            value = self.request(
                "POST",
                path,
                data={
                    "To": destination,
                    "From": self.config["sender"],
                    "Body": body,
                    "MessagingServiceSid": self.config["service_id"],
                    "StatusCallback": self.config["callback_url"],
                },
            )
        else:
            value = self.request(
                "POST",
                "/v2/messages",
                json={
                    "to": destination,
                    "from": self.config["sender"],
                    "text": body,
                    "messaging_profile_id": self.config["service_id"],
                    "webhook_url": self.config["callback_url"],
                    "type": "SMS",
                },
            )
        return self.parse(value, destination)

    def reconcile(self, identifier, *, destination):
        identifier = message_id(self.name, identifier)
        path = (
            f"/2010-04-01/Accounts/{self.config['account_sid']}/Messages/{identifier}.json"
            if self.name == "twilio"
            else f"/v2/messages/{identifier}"
        )
        return self.parse(self.request("GET", path), destination)


def verify_twilio(url, body, signature, token):
    """Documented Auth Token HMAC-SHA1 form contract; all parameters participate."""
    if not isinstance(signature, str) or len(signature) > 100 or len(body) > 16384:
        raise ValueError("Invalid callback")
    try:
        params = parse_qs(
            body.decode("utf-8", errors="strict"),
            keep_blank_values=True,
            max_num_fields=80,
        )
        canonical = url + "".join(
            k + v for k in sorted(params) for v in sorted(set(params[k]))
        )
        # Required native protocol, not password hashing or an algorithm fallback.
        expected = base64.b64encode(
            hmac.digest(token.encode(), canonical.encode(), "sha1")
        ).decode()
        if not hmac.compare_digest(expected, signature):
            raise ValueError()
        if any(
            len(v) != 1
            for k, v in params.items()
            if k
            in {
                "AccountSid",
                "MessageSid",
                "From",
                "To",
                "Body",
                "MessageStatus",
                "SmsStatus",
                "MessagingServiceSid",
                "OptOutType",
                "ErrorCode",
            }
        ):
            raise ValueError()
        return {k: v[0] for k, v in params.items()}
    except (ValueError, UnicodeError, TypeError):
        raise ValueError("Invalid callback") from None


def verify_telnyx(body, signature, timestamp, public_key, *, now):
    try:
        if (
            len(body) > 16384
            or not isinstance(timestamp, str)
            or not timestamp.isdigit()
            or len(timestamp) > 12
            or abs(now - int(timestamp)) > 300
        ):
            raise ValueError()
        key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key, validate=True)
        )
        key.verify(
            base64.b64decode(signature, validate=True), timestamp.encode() + b"|" + body
        )
    except (ValueError, TypeError, InvalidSignature):
        raise ValueError("Invalid callback") from None


class MockSMS:
    """Normalized facade around the existing no-network mock sender."""

    name = "mock"

    def __init__(self, sender=None):
        from app.notifications.providers import MockSender

        self.sender = sender or MockSender()
        self.receipts = {}

    def submit(self, *, destination, body, key):
        receipt = self.sender.send(
            key=key,
            destination=destination,
            payload={"body": body, "simulation_only": True},
        )
        value = Submission(receipt, "delivered", "mock_delivered")
        self.receipts[receipt] = value
        return value

    def reconcile(self, message_id, *, destination):
        if message_id not in self.receipts:
            raise SMSFailure("invalid_response")
        return self.receipts[message_id]


PROVIDER_CAPABILITIES = {
    "mock": {
        "network": False,
        "callbacks": "administrator-only mock",
        "automatic_reply": False,
    },
    "twilio": {
        "network": True,
        "callbacks": "Auth Token HMAC-SHA1 form",
        "automatic_reply": False,
        "submission_idempotency_assumed": False,
    },
    "telnyx": {
        "network": True,
        "callbacks": "Ed25519 raw JSON and signed timestamp",
        "automatic_reply": False,
        "submission_idempotency_assumed": False,
    },
}
