"""Native email contracts use synthetic recipients and isolated transports only."""

import base64, json, ssl, smtplib
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
import httpx, pytest
from pydantic import ValidationError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from app.notifications.email_contracts import *
from app.notifications.email_transport import *
from app.notifications.email_gateway import app
from app.notifications import email_service as s
from app.core.config import settings

DEST = "synthetic@example.com"
CONFIG = {
    "sender": "cs@example.com",
    "sender_name": "Synthetic NOC",
    "reply_to": "cs@example.com",
    "approved_domains": ["example.com"],
    "verification_reference": "synthetic-domain-review",
    "purposes": ["signup"],
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_tls": "starttls",
}
PAYLOAD = {
    "subject": "Synthetic test",
    "text": "Synthetic content",
    "html": "<p>Synthetic content</p>",
}


def cfg(**kw):
    return EmailConfig(**(CONFIG | kw))


@pytest.mark.parametrize(
    "bad",
    [
        "bad",
        "a\r\nBcc:x@example.com",
        "Person <a@example.com>",
        "a..b@example.com",
        ".a@example.com",
        "a@localhost",
        "a@exa..mple.com",
    ],
)
def test_reject_invalid_mailbox(bad):
    with pytest.raises(ValueError):
        address(bad)


def test_channel_identity_namespaced():
    assert s.identity(DEST) == s.identity("synthetic@EXAMPLE.COM")
    from app.notifications.service import contact_hash

    assert s.identity(DEST) != contact_hash(DEST)


@pytest.mark.parametrize(
    "kw",
    [
        {"sender": "other@elsewhere.com"},
        {"smtp_port": 465},
        {"smtp_tls": "implicit"},
        {"recipient_hashes": ["not-hash"]},
    ],
)
def test_configuration_rejects_insecure_or_unapproved(kw):
    with pytest.raises(ValidationError):
        cfg(**kw)


@pytest.mark.parametrize(
    "text",
    [
        "<script>x</script>",
        "https://tracking.example/x",
        "{name}",
        "{status.__class__}",
        "{reference!r}",
        "{status:>10000}",
    ],
)
def test_template_rejects_active_or_arbitrary_content(text):
    with pytest.raises(ValidationError):
        EmailTemplateRequest(
            key="signup_received",
            purpose="signup",
            subject="Application received",
            text=text + " synthetic padding",
        )


def test_template_escape_and_accessibility():
    t = EmailTemplateRequest(
        key="signup_received",
        purpose="signup",
        subject="Application {reference}",
        text="Status: {status}. Contact support.",
    ).model_dump()
    p = render(t, {"reference": "ref", "status": "<img src=x onerror=alert(1)>"})
    assert (
        "&lt;img" in p["html"] and "<img" not in p["html"] and 'lang="en"' in p["html"]
    )
    assert "<img" in p["text"] and p["subject"] == "Application ref"


def test_sendgrid_contract_no_tracking_or_pii_custom_args():
    def respond(r):
        assert str(r.url) == "https://api.sendgrid.com/v3/mail/send"
        assert r.headers["authorization"] == "Bearer synthetic-key"
        b = json.loads(r.content)
        assert b["personalizations"][0]["custom_args"] == {"noc_attempt": "a" * 64}
        assert b["tracking_settings"]["open_tracking"]["enable"] is False
        assert len(b["content"]) == 2
        return httpx.Response(202, headers={"X-Message-Id": "synthetic-receipt"})

    result = SendGrid(
        cfg().model_dump(),
        {"api_key": "synthetic-key"},
        transport=httpx.MockTransport(respond),
    ).send(key="synthetic", destination=DEST, payload=PAYLOAD, correlation="a" * 64)
    assert result.status == "accepted" and not result.simulated


@pytest.mark.parametrize(
    "code,expected,acceptance",
    [
        (401, "authentication_failed", "not_accepted"),
        (403, "authentication_failed", "not_accepted"),
        (429, "rate_limited", "not_accepted"),
        (400, "sender_rejected", "not_accepted"),
        (500, "invalid_response", "unknown"),
        (302, "invalid_response", "unknown"),
    ],
)
def test_sendgrid_error_evidence(code, expected, acceptance):
    p = SendGrid(
        cfg().model_dump(),
        {"api_key": "secret"},
        transport=httpx.MockTransport(
            lambda r: httpx.Response(code, headers={"Retry-After": "120"})
        ),
    )
    with pytest.raises(EmailFailure) as e:
        p.send(key="synthetic", destination=DEST, payload=PAYLOAD, correlation="a" * 64)
    assert e.value.code == expected and e.value.acceptance == acceptance
    assert "secret" not in str(e.value)


def test_sendgrid_timeout_is_ambiguous():
    def timeout(r):
        raise httpx.ReadTimeout("contains-secret")

    p = SendGrid(
        cfg().model_dump(),
        {"api_key": "secret"},
        transport=httpx.MockTransport(timeout),
    )
    with pytest.raises(EmailFailure) as e:
        p.send(key="synthetic", destination=DEST, payload=PAYLOAD, correlation="a" * 64)
    assert e.value.acceptance == "unknown" and "secret" not in str(e.value)


class SMTPFixture:
    def __init__(self, *, host, port, timeout, context=None):
        self.calls = []
        self.context = context
        self.encrypted = context is not None
        self.closed = False

    def ehlo(self):
        self.calls.append("ehlo")

    def has_extn(self, name):
        return True

    def starttls(self, context):
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        self.encrypted = True
        self.calls.append("tls")

    def login(self, user, password):
        assert self.encrypted
        self.calls.append("login")

    def send_message(self, msg, **kw):
        self.msg = msg
        self.calls.append("data")
        return {}

    def close(self):
        self.closed = True


@pytest.mark.parametrize("mode,port", [("starttls", 587), ("implicit", 465)])
def test_smtp_authenticated_tls_acceptance_not_delivery(mode, port):
    rows = []

    def factory(**kw):
        p = SMTPFixture(**kw)
        rows.append(p)
        return p

    r = SMTP(
        cfg(smtp_tls=mode, smtp_port=port).model_dump(),
        {"username": "synthetic", "password": "secret"},
        factory=factory,
    ).send(key="same", destination=DEST, payload=PAYLOAD, correlation="a" * 64)
    assert r.status == "accepted" and rows[0].closed
    assert rows[0].calls.index("login") < rows[0].calls.index("data")
    assert r.message_id == message_id("same", "example.com")
    assert rows[0].msg.get_content_type() == "multipart/alternative"


def test_smtp_downgrade_does_not_authenticate():
    class NoTLS(SMTPFixture):
        def has_extn(self, name):
            return False

        def login(self, *args):
            pytest.fail("Must not authenticate without TLS")

    with pytest.raises(EmailFailure) as e:
        SMTP(
            cfg().model_dump(), {"username": "u", "password": "p"}, factory=NoTLS
        ).send(key="x", destination=DEST, payload=PAYLOAD, correlation="a" * 64)
    assert e.value.code == "tls_failed" and e.value.acceptance == "not_accepted"


def test_smtp_certificate_rejection():
    class BadTLS(SMTPFixture):
        def starttls(self, context):
            raise ssl.SSLCertVerificationError("synthetic untrusted")

    with pytest.raises(EmailFailure) as e:
        SMTP(
            cfg().model_dump(), {"username": "u", "password": "p"}, factory=BadTLS
        ).send(key="x", destination=DEST, payload=PAYLOAD, correlation="a" * 64)
    assert e.value.code == "tls_failed" and e.value.acceptance == "not_accepted"


def test_smtp_lost_data_response_is_ambiguous():
    class Lost(SMTPFixture):
        def send_message(self, *a, **kw):
            raise smtplib.SMTPServerDisconnected("secret")

    with pytest.raises(EmailFailure) as e:
        SMTP(cfg().model_dump(), {"username": "u", "password": "p"}, factory=Lost).send(
            key="x", destination=DEST, payload=PAYLOAD, correlation="a" * 64
        )
    assert e.value.acceptance == "unknown" and "secret" not in str(e.value)


def event_fixture(kind="delivered", timestamp=None):
    private = ec.generate_private_key(ec.SECP256R1())
    ts = str(
        int(datetime.now(timezone.utc).timestamp()) if timestamp is None else timestamp
    )
    body = json.dumps(
        [
            {
                "event": kind,
                "email": DEST,
                "timestamp": int(ts),
                "sg_event_id": "synthetic-event",
                "noc_attempt": "a" * 64,
                "sg_message_id": "synthetic-id",
            }
        ]
    ).encode()
    signature = private.sign(ts.encode() + body, ec.ECDSA(hashes.SHA256()))
    public = base64.b64encode(
        private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()
    return (
        body,
        {
            "x-twilio-email-event-webhook-timestamp": ts,
            "x-twilio-email-event-webhook-signature": base64.b64encode(
                signature
            ).decode(),
        },
        public,
    )


def test_sendgrid_signed_event_official_contract():
    b, h, k = event_fixture()
    assert (
        verified_events(b, h, k, int(datetime.now(timezone.utc).timestamp()))[0][
            "event"
        ]
        == "delivered"
    )


@pytest.mark.parametrize("alter", ["body", "signature", "timestamp", "key"])
def test_invalid_signature_or_replay(alter):
    b, h, k = event_fixture()
    if alter == "body":
        b += b" "
    if alter == "signature":
        h["x-twilio-email-event-webhook-signature"] = "bad"
    if alter == "timestamp":
        h["x-twilio-email-event-webhook-timestamp"] = "1"
    if alter == "key":
        k = event_fixture()[2]
    with pytest.raises(ValueError):
        verified_events(b, h, k, int(datetime.now(timezone.utc).timestamp()))


def test_gateway_isolation_and_disabled_default(monkeypatch):
    monkeypatch.setattr(settings, "email_callback_gateway_enabled", False)
    c = TestClient(app)
    assert c.get("/api/customers").status_code == 404
    assert c.get("/docs").status_code == 404
    assert c.post("/callbacks/email/sendgrid", json=[]).status_code == 503
    assert c.get("/health").json()["delivery_enabled"] is False


def test_host_activation_fail_closed(monkeypatch):
    monkeypatch.setattr(settings, "email_native_transport_enabled", False)
    with pytest.raises(ValueError):
        s.host_authorized()


def test_no_automatic_failover():
    with pytest.raises(ValidationError):
        EmailRouting(primary="sendgrid", secondary="smtp", failover=True)


def test_migration_extends_canonical_queue():
    from app.models import entities
    from app.core.database import Base

    assert "email_provider" in Base.metadata.tables["notification_attempts"].c
    assert "notification_email_envelopes" in Base.metadata.tables
    assert not any(
        x in Base.metadata.tables for x in ("email_queue", "email_customers")
    )


def test_real_local_smtp_starttls_auth_and_data(tmp_path, monkeypatch):
    import socket, threading
    from datetime import timedelta
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    crt = tmp_path / "fixture.crt"
    priv = tmp_path / "fixture.key"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    priv.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    priv.chmod(0o600)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(crt, priv)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    seen = []
    errors = []

    def serve():
        try:
            conn, _ = listener.accept()
            conn.settimeout(5)
            stream = conn.makefile("rb")
            conn.sendall(b"220 localhost synthetic fixture\r\n")
            encrypted = False
            while True:
                line = stream.readline()
                seen.append(line.split(b" ", 1)[0].strip().upper())
                if not line:
                    break
                command = line.upper()
                if command.startswith(b"EHLO"):
                    conn.sendall(b"250-localhost\r\n250-AUTH PLAIN\r\n250 STARTTLS\r\n")
                elif command.startswith(b"STARTTLS"):
                    conn.sendall(b"220 Ready\r\n")
                    stream.close()
                    conn = server_context.wrap_socket(conn, server_side=True)
                    stream = conn.makefile("rb")
                    encrypted = True
                elif command.startswith(b"AUTH"):
                    assert encrypted
                    conn.sendall(b"235 Authenticated\r\n")
                elif command.startswith((b"MAIL", b"RCPT")):
                    conn.sendall(b"250 Accepted\r\n")
                elif command.startswith(b"DATA"):
                    conn.sendall(b"354 Send content\r\n")
                    body = bytearray()
                    while True:
                        item = stream.readline()
                        if item == b".\r\n":
                            break
                        if not item:
                            raise AssertionError("Incomplete SMTP DATA")
                        body.extend(item)
                    assert (
                        b"multipart/alternative" in body
                        and b"Synthetic content" in body
                    )
                    conn.sendall(b"250 queued synthetic\r\n")
                else:
                    raise AssertionError("Unexpected SMTP command")
            stream.close()
            conn.close()
        except Exception as e:
            errors.append(type(e).__name__)
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    original = ssl.create_default_context
    monkeypatch.setattr(
        ssl, "create_default_context", lambda: original(cafile=str(crt))
    )

    def connect(**kw):
        return smtplib.SMTP("localhost", listener.getsockname()[1], timeout=5)

    result = SMTP(
        cfg().model_dump(),
        {"username": "synthetic", "password": "disposable"},
        factory=connect,
    ).send(key="local-fixture", destination=DEST, payload=PAYLOAD, correlation="a" * 64)
    thread.join(6)
    assert not thread.is_alive() and not errors and result.status == "accepted"
    assert seen.index(b"STARTTLS") < seen.index(b"AUTH") < seen.index(b"DATA")


@pytest.mark.parametrize(
    "exception,expected,acceptance",
    [
        (
            smtplib.SMTPAuthenticationError(535, b"synthetic private error"),
            "authentication_failed",
            "not_accepted",
        ),
        (smtplib.SMTPDataError(451, b"synthetic"), "rate_limited", "not_accepted"),
        (smtplib.SMTPDataError(550, b"synthetic"), "sender_rejected", "not_accepted"),
    ],
)
def test_smtp_verified_rejections(exception, expected, acceptance):
    class Reject(SMTPFixture):
        def send_message(self, *a, **kw):
            raise exception

    with pytest.raises(EmailFailure) as e:
        SMTP(
            cfg().model_dump(), {"username": "u", "password": "secret"}, factory=Reject
        ).send(key="x", destination=DEST, payload=PAYLOAD, correlation="a" * 64)
    assert (
        e.value.code == expected
        and e.value.acceptance == acceptance
        and "private" not in str(e.value)
    )


def test_sendgrid_missing_receipt_never_assumed_rejection():
    r = SendGrid(
        cfg().model_dump(),
        {"api_key": "synthetic"},
        transport=httpx.MockTransport(lambda r: httpx.Response(202)),
    ).send(key="x", destination=DEST, payload=PAYLOAD, correlation="a" * 64)
    assert r.message_id is None and r.status == "accepted"


def test_sendgrid_invalid_receipt_is_ambiguous():
    with pytest.raises(EmailFailure) as e:
        SendGrid(
            cfg().model_dump(),
            {"api_key": "synthetic"},
            transport=httpx.MockTransport(
                lambda r: httpx.Response(202, headers={"X-Message-Id": "unsafe!!!"})
            ),
        ).send(key="x", destination=DEST, payload=PAYLOAD, correlation="a" * 64)
    assert e.value.acceptance == "unknown"


def synthetic_logo(size=32, metadata=False):
    import struct, zlib

    def chunk(kind, value):
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
        )

    raw = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    )
    if metadata:
        raw += chunk(b"tEXt", b"unapproved metadata")
    raw += chunk(
        b"IDAT", zlib.compress((b"\x00" + b"\x11\x22\x33" * size) * size)
    ) + chunk(b"IEND", b"")
    return base64.b64encode(raw).decode()


def test_embedded_logo_no_external_resource():
    logo = synthetic_logo()
    assert logo_png(logo) == logo
    t = EmailTemplateRequest(
        key="signup_received",
        purpose="signup",
        subject="Synthetic application",
        text="Your application {reference} is received.",
        logo_base64=logo,
    ).model_dump()
    p = render(t, {"reference": "SYN", "status": "submitted"})
    assert (
        'src="cid:noc-brand-logo"' in p["html"]
        and "http" not in p["html"]
        and p["logo_base64"] == logo
    )
    called = []

    def respond(r):
        j = json.loads(r.content)
        assert j["attachments"][0]["content_id"] == "noc-brand-logo"
        assert j["attachments"][0]["content"] == logo
        called.append(True)
        return httpx.Response(202)

    SendGrid(
        cfg().model_dump(),
        {"api_key": "synthetic"},
        transport=httpx.MockTransport(respond),
    ).send(key="logo", destination=DEST, payload=p, correlation="a" * 64)
    smtp = []

    def factory(**kw):
        obj = SMTPFixture(**kw)
        smtp.append(obj)
        return obj

    SMTP(
        cfg().model_dump(),
        {"username": "synthetic", "password": "disposable"},
        factory=factory,
    ).send(key="logo", destination=DEST, payload=p, correlation="a" * 64)
    assert called and any(
        part.get("Content-ID") == "<noc-brand-logo>" for part in smtp[0].msg.walk()
    )


@pytest.mark.parametrize(
    "value",
    [
        synthetic_logo(1),
        synthetic_logo(metadata=True),
        base64.b64encode(b"<svg onload=alert(1)>").decode(),
        synthetic_logo()[:-8] + "AAAAAAAA",
    ],
)
def test_logo_rejects_pixels_metadata_active_content_or_corruption(value):
    with pytest.raises(ValueError):
        logo_png(value)


def test_quote_token_body_only_and_never_url():
    t = EmailTemplateRequest(
        key="quote_available",
        purpose="signup",
        subject="Approved quote {reference}",
        text="Use this private token in the existing quote form: {quote_token}",
    )
    token = "s" * 64
    p = render(
        t.model_dump(),
        {"reference": "SYN", "status": "quote_sent", "quote_token": token},
    )
    assert (
        token in p["text"] and token not in p["subject"] and "https://" not in p["text"]
    )
    with pytest.raises(ValidationError):
        EmailTemplateRequest(
            key="quote_available",
            purpose="signup",
            subject="Quote {quote_token}",
            text="Private reviewed quote message.",
        )
    with pytest.raises(ValidationError):
        EmailTemplateRequest(
            key="signup_received",
            purpose="signup",
            subject="Application",
            text="Private token {quote_token}",
        )
