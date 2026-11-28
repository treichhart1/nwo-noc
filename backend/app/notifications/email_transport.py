"""Native email contracts. No I/O on import; no automatic retry after ambiguity."""

import base64
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol
import hashlib
import html
import json
import smtplib
import ssl
import re
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidSignature
from app.notifications.email_contracts import address


@dataclass(frozen=True)
class EmailReceipt:
    message_id: str | None
    status: str = "accepted"
    simulated: bool = False


class EmailSender(Protocol):
    def send(
        self, *, key: str, destination: str, payload: dict, correlation: str
    ) -> EmailReceipt: ...


CAPABILITIES = {
    "mock": {
        "submission": "simulated",
        "confirmed_delivery": False,
        "signed_events": False,
        "provider_costs": False,
    },
    "sendgrid": {
        "submission": "http_202_acceptance",
        "confirmed_delivery": "signed_delivered_event",
        "signed_events": True,
        "provider_costs": False,
    },
    "smtp": {
        "submission": "smtp_server_acceptance",
        "confirmed_delivery": False,
        "signed_events": False,
        "provider_costs": False,
    },
}


class EmailFailure(RuntimeError):
    def __init__(
        self, code="provider_unavailable", *, acceptance="unknown", retry_after=60
    ):
        self.code = (
            code
            if code
            in {
                "authentication_failed",
                "rate_limited",
                "tls_failed",
                "sender_rejected",
                "recipient_rejected",
                "invalid_response",
                "timeout",
                "provider_unavailable",
            }
            else "provider_unavailable"
        )
        self.acceptance = acceptance
        self.retry_after = max(1, min(3600, retry_after))
        super().__init__(self.code)


def message_id(key, domain):
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,63}", domain):
        raise ValueError("Approved domain required")
    return "<noc-" + hashlib.sha256(key.encode()).hexdigest() + "@" + domain + ">"


def render(template, fields):
    safe = {k: str(fields[k]) for k in ("reference", "status")}
    subject = template["subject"].format(**safe)
    text = template["text"].format(**safe)
    if "\n" in subject or "\r" in subject:
        raise ValueError("Header injection rejected")
    # No user supplied HTML/CSS, tracking, script, image or arbitrary resource.
    markup = (
        '<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main style="max-width:640px;margin:auto;font:16px sans-serif"><h1>'
        + html.escape(template["brand"])
        + "</h1><p>"
        + html.escape(text).replace("\n", "<br>")
        + "</p><p>"
        + html.escape(template["support"])
        + "</p></main></body></html>"
    )
    return {
        "subject": subject,
        "text": text + "\n\n" + template["support"],
        "html": markup,
    }


class MockEmail:
    def send(self, *, key, destination, payload, correlation):
        address(destination)
        return EmailReceipt(
            "mock-" + hashlib.sha256(key.encode()).hexdigest()[:40], simulated=True
        )


class SendGrid:
    def __init__(self, config, secret, *, transport=None):
        self.config, self.secret, self.transport = config, secret, transport
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise ValueError("Only offline HTTP fixture injection supported")

    def send(self, *, key, destination, payload, correlation):
        address(destination)
        c = self.config
        body = {
            "personalizations": [
                {
                    "to": [{"email": destination}],
                    "custom_args": {"noc_attempt": correlation},
                }
            ],
            "from": {"email": c["sender"], "name": c["sender_name"]},
            "reply_to": {"email": c["reply_to"]},
            "subject": payload["subject"],
            "content": [
                {"type": "text/plain", "value": payload["text"]},
                {"type": "text/html", "value": payload["html"]},
            ],
            "headers": {"Message-ID": message_id(key, c["sender"].split("@")[1])},
            "tracking_settings": {
                "click_tracking": {"enable": False, "enable_text": False},
                "open_tracking": {"enable": False},
                "subscription_tracking": {"enable": False},
            },
        }
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=httpx.Timeout(10, connect=3),
                verify=True,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST",
                    "https://api.sendgrid.com/v3/mail/send",
                    json=body,
                    headers={"Authorization": "Bearer " + self.secret["api_key"]},
                ) as r:
                    if r.status_code == 429:
                        retry = r.headers.get("Retry-After", "60")
                        raise EmailFailure(
                            "rate_limited",
                            acceptance="not_accepted",
                            retry_after=int(retry) if retry.isdigit() else 60,
                        )
                    if r.status_code in (401, 403):
                        raise EmailFailure(
                            "authentication_failed", acceptance="not_accepted"
                        )
                    if r.status_code in (400, 413, 422):
                        raise EmailFailure("sender_rejected", acceptance="not_accepted")
                    if r.status_code != 202:
                        raise EmailFailure("invalid_response")
                    receipt = r.headers.get("X-Message-Id")
                    if receipt and not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", receipt):
                        raise EmailFailure("invalid_response")
                    return EmailReceipt(receipt)
        except EmailFailure:
            raise
        except httpx.TimeoutException:
            raise EmailFailure("timeout") from None
        except httpx.HTTPError:
            raise EmailFailure() from None


class SMTP:
    def __init__(self, config, secret, *, factory=None):
        self.config, self.secret, self.factory = config, secret, factory

    def send(self, *, key, destination, payload, correlation):
        c = self.config
        address(destination)
        if (c["smtp_port"], c["smtp_tls"]) not in {
            (465, "implicit"),
            (587, "starttls"),
        } or not c.get("smtp_host"):
            raise ValueError("Approved encrypted SMTP required")
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        msg = EmailMessage()
        for header, value in [
            ("From", formataddr((c["sender_name"], c["sender"]))),
            ("To", destination),
            ("Reply-To", c["reply_to"]),
            ("Subject", payload["subject"]),
            ("Message-ID", message_id(key, c["sender"].split("@")[1])),
        ]:
            msg[header] = value
        msg.set_content(payload["text"])
        msg.add_alternative(payload["html"], subtype="html")
        transmitting = False
        client = None
        try:
            factory = self.factory or (
                smtplib.SMTP_SSL if c["smtp_tls"] == "implicit" else smtplib.SMTP
            )
            args = {"host": c["smtp_host"], "port": c["smtp_port"], "timeout": 10}
            if c["smtp_tls"] == "implicit":
                args["context"] = context
            client = factory(**args)
            client.ehlo()
            if c["smtp_tls"] == "starttls":
                if not client.has_extn("starttls"):
                    raise EmailFailure("tls_failed", acceptance="not_accepted")
                client.starttls(context=context)
                client.ehlo()
            client.login(self.secret["username"], self.secret["password"])
            transmitting = True
            refused = client.send_message(
                msg, from_addr=c["sender"], to_addrs=[destination]
            )
            if refused:
                raise EmailFailure("recipient_rejected", acceptance="not_accepted")
            return EmailReceipt(msg["Message-ID"])
        except EmailFailure:
            raise
        except ssl.SSLError:
            raise EmailFailure(
                "tls_failed", acceptance="unknown" if transmitting else "not_accepted"
            ) from None
        except smtplib.SMTPAuthenticationError:
            raise EmailFailure(
                "authentication_failed", acceptance="not_accepted"
            ) from None
        except smtplib.SMTPRecipientsRefused:
            raise EmailFailure(
                "recipient_rejected", acceptance="not_accepted"
            ) from None
        except smtplib.SMTPDataError as e:
            raise EmailFailure(
                "rate_limited" if 400 <= e.smtp_code < 500 else "sender_rejected",
                acceptance="not_accepted",
            ) from None
        except (OSError, smtplib.SMTPException):
            raise EmailFailure(
                "timeout", acceptance="unknown" if transmitting else "not_accepted"
            ) from None
        finally:
            if client:
                try:
                    client.close()
                except OSError:
                    pass


def verified_events(body, headers, public_key, now):
    if len(body) > 65536:
        raise ValueError("Bounded webhook required")
    timestamp = headers.get("x-twilio-email-event-webhook-timestamp", "")
    if not timestamp.isdigit() or abs(now - int(timestamp)) > 300:
        raise ValueError("Stale webhook")
    try:
        key = serialization.load_der_public_key(
            base64.b64decode(public_key, validate=True)
        )
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise ValueError()
        key.verify(
            base64.b64decode(
                headers.get("x-twilio-email-event-webhook-signature", ""), validate=True
            ),
            timestamp.encode() + body,
            ec.ECDSA(hashes.SHA256()),
        )
        events = json.loads(body)
        if not isinstance(events, list) or not 1 <= len(events) <= 100:
            raise ValueError()
        result = []
        for e in events:
            if e.get("event") not in {
                "processed",
                "delivered",
                "deferred",
                "dropped",
                "bounce",
                "spamreport",
                "unsubscribe",
                "group_unsubscribe",
            }:
                continue
            if (
                not isinstance(e.get("sg_event_id"), str)
                or not 1 <= len(e["sg_event_id"]) <= 255
                or not re.fullmatch("[a-f0-9]{64}", e.get("noc_attempt", ""))
            ):
                raise ValueError()
            if not isinstance(e.get("timestamp"), int) or e["timestamp"] > now + 300:
                raise ValueError()
            address(e.get("email"))
            result.append(e)
        return result
    except (InvalidSignature, TypeError, KeyError, ValueError):
        raise ValueError("Email webhook verification failed") from None
