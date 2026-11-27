"""Verified callbacks update canonical deliveries and the shared customer opt-out."""

from datetime import datetime, timezone
import hashlib
import json
from sqlalchemy import select, text
from sqlalchemy.orm import load_only
from app.core.config import settings
from app.customers.models import CustomerAccount
from app.customers.contracts import e164
from app.notifications.models import (
    NotificationDelivery as Delivery,
    NotificationAttempt as Attempt,
    NotificationSuppression as Suppression,
    NotificationRecipient as Recipient,
)
from app.notifications.sms_models import (
    SMSProviderConfig as Provider,
    SMSWebhookEvent as Event,
)
from app.notifications.sms_transport import (
    TWILIO_STATES,
    TELNYX_STATES,
    next_state,
    message_id,
    verify_twilio,
    verify_telnyx,
    money,
    provider_error,
)
from app.notifications.service import digest, clock, lock, audit
from app.notifications.sms_identity import contact_hash
from app.notifications.secrets import unseal, seal

STOP = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"}
START = {"START", "UNSTOP"}


def normalize(name, cfg, body, headers, secret, now):
    reported_cost = None
    if name == "twilio":
        if headers.get("x-twilio-webhooks-signature-settings"):
            raise ValueError("Shared-key webhook mode is not configured")
        value = verify_twilio(
            cfg["callback_url"], body, headers.get("x-twilio-signature", ""), secret
        )
        if value.get("AccountSid") != cfg["account_sid"]:
            raise ValueError("Callback ownership mismatch")
        service = value.get("MessagingServiceSid")
        if service is not None and service != cfg["service_id"]:
            raise ValueError("Callback service mismatch")
        identifier = message_id(name, value.get("MessageSid"))
        sender, destination = value.get("From"), value.get("To")
        raw = value.get("MessageStatus") or value.get("SmsStatus")
        inbound = raw in {"received", "receiving"}
        text = value.get("Body", "") if inbound else ""
        opt = value.get("OptOutType", "").upper() if inbound else ""
        event_id = digest(
            [
                identifier,
                "inbound" if inbound else raw,
                None if inbound else value.get("ErrorCode"),
            ]
        )
        canonical = digest(
            [identifier, sender, destination, raw, text, opt, value.get("ErrorCode")]
        )
        occurred = now
    else:
        verify_telnyx(
            body,
            headers.get("telnyx-signature-ed25519", ""),
            headers.get("telnyx-timestamp", ""),
            secret,
            now=int(now.timestamp()),
        )
        try:
            data = json.loads(body)["data"]
            event_type = data["event_type"]
            if event_type not in {
                "message.received",
                "message.sent",
                "message.finalized",
            }:
                raise ValueError()
            value = data["payload"]
            if (
                value.get("organization_id") != cfg["account_sid"]
                or value.get("messaging_profile_id") != cfg["service_id"]
            ):
                raise ValueError()
            event_id = message_id("telnyx", data["id"])
            identifier = message_id(name, value["id"])
            if len(value["to"]) != 1:
                raise ValueError()
            inbound = event_type == "message.received"
            if value.get("direction") != ("inbound" if inbound else "outbound"):
                raise ValueError()
            sender, destination = (
                value["from"]["phone_number"],
                value["to"][0]["phone_number"],
            )
            raw = "received" if inbound else value["to"][0]["status"]
            text, opt = (value.get("text", ""), "") if inbound else ("", "")
            occurred = datetime.fromisoformat(
                data["occurred_at"].replace("Z", "+00:00")
            )
            if occurred.tzinfo is None or occurred.timestamp() > now.timestamp() + 60:
                raise ValueError()
            cost = value.get("cost") or {}
            if cost.get("amount") is not None:
                if cost.get("currency") != cfg["currency"]:
                    raise ValueError()
                reported_cost = money(cost["amount"])
            canonical = digest(data)  # retry meta.attempt is deliberately excluded
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValueError("Invalid callback contract") from None
    if (
        not e164(sender)
        or not e164(destination)
        or not isinstance(text, str)
        or len(text) > 1600
        or not isinstance(raw, str)
        or len(raw) > 40
        or not raw.replace("_", "").isalpha()
    ):
        raise ValueError("Invalid callback contract")
    owned = destination if inbound else sender
    if owned not in cfg["approved_senders"]:
        raise ValueError("Callback sender ownership mismatch")
    if not inbound and owned != cfg["sender"]:
        raise ValueError("Outbound callback sender mismatch")
    keyword = opt or text.strip().upper()
    kind = (
        (
            "stop"
            if keyword in STOP
            else "start"
            if keyword in START
            else "help"
            if keyword in {"HELP", "INFO"}
            else "reply"
        )
        if inbound
        else "status"
    )
    vendor_code, opt_out = provider_error(
        name, {"error_code": value.get("ErrorCode")} if name == "twilio" else value
    )
    return {
        "key": digest([name, event_id]),
        "_opt_out": opt_out and not inbound,
        "provider_error_code": vendor_code,
        "provider": name,
        "message_id": identifier,
        "kind": kind,
        "raw_status": raw,
        "reported_cost_microunits": reported_cost,
        "body_hash": canonical,
        "contact_hash": contact_hash(sender if inbound else destination),
        "text": text,
        "received_at": occurred,
    }


def apply_status(d, attempt, raw, at):
    mapping = TWILIO_STATES if attempt.provider_name == "twilio" else TELNYX_STATES
    normalized = mapping.get(raw, "reconciliation_required")
    state = next_state(d.state, normalized)
    # Keep terminal evidence immutable; accepted/queued cannot regress sent.
    if state == d.state and normalized != d.state:
        return
    d.state, d.provider_status, d.provider_status_at = state, raw, at
    attempt.raw_status, attempt.outcome, attempt.updated_at = raw, state, at
    if state in {"delivered", "undelivered", "failed"}:
        d.completed_at = at


def apply_callback(db, name, body, headers, *, now=None):
    now = now or clock()
    row = db.get(Provider, name)
    if not row or not row.config.get("callbacks_enabled") or not row.webhook_ciphertext:
        raise ValueError("Callback not configured")
    secret = unseal(settings.sms_callback_key_file, row.webhook_ciphertext)
    event = normalize(name, row.config, body, headers, secret, now)
    try:
        lock(db)
    except ValueError:
        raise CallbackBusy("Callback transaction is busy") from None
    prior = db.get(Event, event["key"])
    if prior:
        if prior.body_hash != event["body_hash"]:
            raise ValueError("Conflicting callback identity")
        return {"duplicate": True}
    opted_out = event.pop("_opt_out")
    text = event.pop("text")
    entry = Event(**event)
    if entry.kind != "status":
        entry.ciphertext = (
            seal(
                settings.sms_callback_key_file,
                json.dumps({"body": text}, ensure_ascii=False),
            )
            if text
            else None
        )
    else:
        attempt = db.scalar(
            select(Attempt)
            .options(
                load_only(
                    Attempt.id,
                    Attempt.provider_name,
                    Attempt.provider_message_id,
                    Attempt.destination_hash,
                    Attempt.delivery_id,
                    Attempt.raw_status,
                    Attempt.outcome,
                    Attempt.updated_at,
                    Attempt.reported_cost_microunits,
                    Attempt.provider_error_code,
                )
            )
            .where(
                Attempt.provider_name == name,
                Attempt.provider_message_id == entry.message_id,
            )
            .with_for_update()
        )
        if attempt:
            if attempt.destination_hash != entry.contact_hash:
                raise ValueError("Callback recipient identity mismatch")
            delivery = db.scalar(
                select(Delivery)
                .options(
                    load_only(
                        Delivery.id,
                        Delivery.state,
                        Delivery.provider_status,
                        Delivery.provider_status_at,
                        Delivery.completed_at,
                    )
                )
                .where(Delivery.id == attempt.delivery_id)
            )
            entry.delivery_id = delivery.id
            # A native account signature and exact receipt prove acceptance, not
            # permission to retry a later delivery failure.
            apply_status(delivery, attempt, entry.raw_status, entry.received_at)
            attempt.provider_error_code = entry.provider_error_code
            if entry.reported_cost_microunits is not None:
                attempt.reported_cost_microunits = entry.reported_cost_microunits
    db.add(entry)
    if entry.kind == "stop" or opted_out:
        # Do not require consent or an active/live provider to honor STOP.
        # Immutable contact-HMAC event also protects unknown/future customers.
        phone = None
        if name == "twilio":
            from urllib.parse import parse_qs

            phone = parse_qs(body.decode())["From" if entry.kind != "status" else "To"][
                0
            ]
        else:
            payload = json.loads(body)["data"]["payload"]
            phone = (
                payload["from"]["phone_number"]
                if entry.kind != "status"
                else payload["to"][0]["phone_number"]
            )
        # A one-way SECURITY DEFINER function lets the isolated callback DB
        # role enforce opt-outs without UPDATE access that could restore consent.
        db.execute(
            text("SELECT public.noc_sms_enforce_opt_out(:phone, :at)"),
            {"phone": phone, "at": now},
        )
        if opted_out:
            db.add(
                Event(
                    key=digest(["callback-opt-out", entry.key]),
                    provider=name,
                    message_id=entry.message_id,
                    kind="stop",
                    raw_status="provider_opt_out",
                    provider_error_code=entry.provider_error_code,
                    body_hash=entry.body_hash,
                    contact_hash=entry.contact_hash,
                    received_at=now,
                )
            )
    row.last_callback_at = now
    audit(
        db,
        None,
        "sms_verified_callback",
        provider=name,
        kind=entry.kind,
        delivery_id=entry.delivery_id,
    )
    db.flush()
    return {"duplicate": False}


class CallbackBusy(RuntimeError):
    """Verified callback can retry a busy transaction without losing evidence."""
