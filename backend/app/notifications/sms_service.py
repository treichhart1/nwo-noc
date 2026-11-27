"""Native transports consume the canonical NOC-96 queue; no second queue/consent DB.

Every external submission has a committed claim BEFORE I/O. A worker crash,
malformed response or timeout quarantines the claim; none is automatically resent.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from uuid import uuid4
from sqlalchemy import select, func, or_, update, case, text
from app.core.config import settings
from app.models.entities import User
from app.customers.models import CustomerAccount
from app.customers.contracts import e164
from app.notifications.models import (
    NotificationDelivery as Delivery,
    NotificationAttempt as Attempt,
    NotificationPlan as Plan,
    NotificationRecipient as Recipient,
    NotificationCredential as Credential,
    NotificationSuppression as Suppression,
)
from app.notifications.sms_identity import contact_hash as native_contact_hash
from app.notifications.sms_models import (
    SMSProviderConfig as Provider,
    SMSRouting as Routing,
    SMSWebhookEvent as Event,
)
from app.notifications.service import (
    lock,
    clock,
    utc,
    policy,
    snapshot,
    digest,
    contact_hash,
    quiet_due,
    audit,
)
from app.notifications.contracts import PlanRequest
from app.notifications.secrets import seal, unseal
from app.notifications.sms_transport import (
    NativeSMS,
    SMSFailure,
    Submission,
    segments,
    next_state,
    TERMINAL,
    message_id,
)


def host_authorized():
    if (
        not settings.sms_native_transport_enabled
        or not settings.sms_egress_approved
        or settings.offline_mode
    ):
        raise ValueError(
            "Native SMS host transport/egress authorization required; disabled by default"
        )


def route(db):
    row = db.get(Routing, 1)
    return (
        row.config if row else {"primary": "mock", "secondary": None, "failover": False}
    )


def credential_key(name):
    return "sms.native." + name


def credentials(db, name):
    row = db.get(Credential, credential_key(name))
    if not row:
        raise ValueError("Provider credentials not configured")
    try:
        return json.loads(unseal(settings.notifications_vault_key_file, row.ciphertext))
    except (OSError, ValueError):
        raise ValueError("Private provider credentials unavailable") from None


def store_credentials(db, name, raw, actor):
    try:
        values = json.loads(raw)
        required = (
            {"api_key_sid", "api_key_secret", "webhook_auth_token"}
            if name == "twilio"
            else {"api_key", "webhook_public_key"}
        )
        if (
            not isinstance(values, dict)
            or set(values) != required
            or any(
                not isinstance(v, str)
                or not 1 <= len(v) <= 512
                or any(ord(c) < 32 for c in v)
                for v in values.values()
            )
        ):
            raise ValueError()
        if name == "twilio":
            if not re.fullmatch(r"SK[a-fA-F0-9]{32}", values["api_key_sid"]):
                raise ValueError()
            webhook = values.pop("webhook_auth_token")
        else:
            import base64
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )

            Ed25519PublicKey.from_public_bytes(
                base64.b64decode(values.pop("webhook_public_key"), validate=True)
            )
            webhook = json.loads(raw)["webhook_public_key"]
    except (ValueError, TypeError):
        raise ValueError("Invalid private provider credential contract") from None
    provider = db.get(Provider, name)
    if not provider:
        raise ValueError("Configure provider first")
    pending = db.scalar(
        select(Attempt.id)
        .join(Delivery)
        .where(
            Attempt.provider_name == name,
            Attempt.acceptance != "not_accepted",
            Delivery.state.not_in(TERMINAL),
        )
        .limit(1)
    )
    if (
        pending
        and provider.webhook_ciphertext
        and unseal(settings.sms_callback_key_file, provider.webhook_ciphertext)
        != webhook
    ):
        raise ValueError(
            "Drain/reconcile pending messages before rotating callback verification keys"
        )
    ciphertext = seal(settings.notifications_vault_key_file, json.dumps(values))
    row = db.get(Credential, credential_key(name))
    if row:
        row.ciphertext = ciphertext
        row.updated_at = clock()
    else:
        db.add(Credential(provider=credential_key(name), ciphertext=ciphertext))
    provider.webhook_ciphertext = seal(settings.sms_callback_key_file, webhook)
    provider.live_authorized = False
    provider.revision += 1
    audit(db, actor, "sms_credentials_rotated", provider=name)


def provider_ready(db, name):
    host_authorized()
    row = db.get(Provider, name)
    if (
        not row
        or not row.enabled
        or not row.live_authorized
        or not row.authorization_reference
    ):
        raise ValueError("Provider lacks independent live authorization")
    c = row.config
    if (
        not c.get("registration_reference")
        or not c.get("recipient_hashes")
        or not c.get("price_per_segment_microunits")
        or not c.get("daily_limit_microunits")
        or not c.get("monthly_limit_microunits")
        or not row.webhook_ciphertext
        or not c.get("callbacks_enabled")
    ):
        raise ValueError(
            "Sender registration, callbacks, allowlist, pricing and limits required"
        )
    credentials(db, name)
    return row


def authorize_delivery(db, identifier, actor, reference, fingerprint):
    lock(db)
    host_authorized()
    d = db.get(Delivery, identifier)
    if (
        not d
        or d.channel != "sms"
        or d.state not in {"queued", "retry", "failed", "suppressed"}
    ):
        raise ValueError(
            "Only a canonical queued or safely rejected SMS can be released"
        )
    prior = list(db.scalars(select(Attempt).where(Attempt.delivery_id == d.id)))
    if (
        any(not a.provider_name or a.acceptance != "not_accepted" for a in prior)
        or d.attempts >= policy(db).max_attempts
    ):
        raise ValueError(
            "Accepted/ambiguous/history-exhausted messages cannot be released again"
        )
    p = db.get(Plan, d.plan_id)
    if p.status != "approved" or p.fingerprint != fingerprint:
        raise ValueError("Review current approved content and targets")
    routing = db.get(Routing, 1)
    names = [route(db)["primary"], route(db).get("secondary")]
    revisions = {n: provider_ready(db, n).revision for n in names if n}
    if "mock" in revisions:
        raise ValueError("Select native routing explicitly")
    d.sms_provider, d.state, d.next_attempt_at = None, "queued", utc(p.due_at)
    d.sms_authorization = {
        "actor": actor,
        "reference": reference,
        "fingerprint": fingerprint,
        "routing_revision": routing.revision,
        "providers": revisions,
        "at": clock().isoformat(),
    }
    validate_delivery(db, d, names[0], clock())
    audit(
        db,
        actor,
        "sms_live_delivery_released",
        delivery_id=d.id,
        plan_id=p.id,
        provider=names[0],
    )
    return d


def validate_delivery(db, d, name, now):
    from app.notifications.controls import require_processing

    require_processing(db)
    row = provider_ready(db, name)
    p = db.get(Plan, d.plan_id)
    a = d.sms_authorization or {}
    operator = db.get(User, a.get("actor")) if a.get("actor") else None
    approver = db.get(User, p.approved_by) if p.approved_by else None
    r = db.get(Routing, 1)
    if (
        not operator
        or not operator.is_active
        or operator.role != "admin"
        or not approver
        or not approver.is_active
        or approver.role not in {"admin", "operator"}
        or p.status != "approved"
        or a.get("fingerprint") != p.fingerprint
        or not r
        or a.get("routing_revision") != r.revision
        or a.get("providers", {}).get(name) != row.revision
    ):
        raise ValueError("SMS approval/configuration changed; review required")
    from app.customer_status.service import linked_approval

    linked_approval(db, p, operator.id)
    from app.notifications.service import current_snapshot
    current, _ = current_snapshot(db, p, now)
    if digest(current) != p.fingerprint:
        raise ValueError("Source, maintenance, targeting or consent changed")
    recipient = db.get(Recipient, d.recipient_id)
    if recipient and recipient.application_id is not None:
        from app.signup.communications import contact
        account = contact(db, recipient)
    else:
        account = db.get(CustomerAccount, recipient.customer_id) if recipient and recipient.customer_id is not None else None
        if (
            not account
            or account.source_deleted_at
            or account.sms_consent != "granted"
            or not account.consent_record
            or account.sms_opt_out
            or account.communication_preference not in {"sms", "both"}
            or not e164(account.phone)
            or contact_hash(account.phone) != recipient.contact_hash
            or db.get(Suppression, account.id)
        ):
            raise ValueError("Customer consent/contact/suppression prevents SMS")
    from app.notifications.models import NotificationContactSuppression
    if db.get(NotificationContactSuppression, contact_hash(account.phone)):
        raise ValueError("Shared contact opt-out")
    blocked = db.scalar(
        select(Event.key)
        .where(
            Event.contact_hash == native_contact_hash(account.phone),
            Event.kind == "stop",
        )
        .limit(1)
    )
    if blocked:
        raise ValueError(
            "Verified cross-provider opt-out requires separate consent review"
        )
    if (
        native_contact_hash(account.phone) not in row.config["recipient_hashes"]
        or p.kind not in row.config["categories"]
    ):
        raise ValueError("Recipient/category not independently authorized for provider")
    return row, p, account


def cost_value():
    return case(
        (
            Attempt.reported_cost_microunits > Attempt.estimated_cost_microunits,
            Attempt.reported_cost_microunits,
        ),
        else_=Attempt.estimated_cost_microunits,
    )


def spending(db, name, since):
    return int(
        db.scalar(
            select(func.coalesce(func.sum(cost_value()), 0)).where(
                Attempt.provider_name == name,
                Attempt.created_at >= since,
                Attempt.acceptance != "not_accepted",
            )
        )
        or 0
    )


def enforce_budget(db, row, amount, now):
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if (
        spending(db, row.name, start) + amount > row.config["daily_limit_microunits"]
        or spending(db, row.name, start.replace(day=1)) + amount
        > row.config["monthly_limit_microunits"]
    ):
        raise ValueError("Provider spending limit reached")
    p = policy(db)
    total = int(
        db.scalar(
            select(func.coalesce(func.sum(cost_value()), 0)).where(
                Attempt.provider_name.is_not(None),
                Attempt.created_at >= now - timedelta(days=1),
                Attempt.acceptance != "not_accepted",
            )
        )
        or 0
    )
    if total + amount > p.daily_budget_microunits:
        raise ValueError("Global notification spending limit reached")


def recover_claims(db, now):
    rows = list(
        db.scalars(
            select(Attempt)
            .where(Attempt.provider_name.is_not(None), Attempt.acceptance == "pending", Attempt.lease_expires_at <= now)
            .order_by(Attempt.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    )
    for a in rows:
        d = db.get(Delivery, a.delivery_id)
        a.acceptance, a.outcome, a.updated_at = (
            "unknown",
            "reconciliation_required",
            now,
        )
        if d.state == "submitted":
            d.state, d.last_error = "reconciliation_required", "worker_interrupted"
        audit(
            db,
            None,
            "sms_interrupted_claim_quarantined",
            delivery_id=d.id,
            provider=a.provider_name,
        )
    return len(rows)


def reserve(db, now):
    lock(db)
    recover_claims(db, now)
    from app.notifications.controls import control_status

    controls = control_status(db)
    if controls["paused"] or controls["emergency_disabled"]:
        return None
    rows = list(
        db.scalars(
            select(Delivery)
            .where(
                Delivery.channel == "sms",
                Delivery.sms_authorization.is_not(None),
                Delivery.state.in_(["queued", "retry"]),
                Delivery.next_attempt_at <= now,
            )
            .order_by(Delivery.next_attempt_at, Delivery.id)
            .limit(20)
            .with_for_update(skip_locked=True)
        )
    )
    for d in rows:
        routing = route(db)
        name = d.sms_provider or routing["primary"]
        try:
            prior = db.scalar(
                select(Attempt.id)
                .where(
                    Attempt.delivery_id == d.id,
                    Attempt.provider_name.is_not(None),
                    Attempt.acceptance != "not_accepted",
                )
                .limit(1)
            )
            if prior:
                d.state, d.last_error = (
                    "reconciliation_required",
                    "prior_acceptance_unresolved",
                )
                continue
            row, plan, account = validate_delivery(db, d, name, now)
            p = policy(db)
            if d.attempts >= p.max_attempts:
                raise ValueError("Bounded attempt limit reached")
            due = quiet_due(
                now, p, plan.snapshot["request"].get("critical_quiet_bypass", False)
            )
            if due > now:
                d.next_attempt_at = due
                continue
            # Canonical recipient cooldown plus all native attempts, including
            # ambiguous reservations. Rejected-before-acceptance attempts may retry.
            counts = (
                lambda seconds, personal=False: db.scalar(
                    select(func.count(Attempt.id))
                    .join(Delivery)
                    .where(
                        Attempt.provider_name.is_not(None),
                        Attempt.acceptance != "not_accepted",
                        Attempt.created_at >= now - timedelta(seconds=seconds),
                        *(
                            [Attempt.destination_hash == native_contact_hash(account.phone)]
                            if personal
                            else []
                        ),
                    )
                )
                or 0
            )
            if counts(60) >= p.max_sms_per_minute or counts(3600) >= p.max_sms_per_hour:
                d.next_attempt_at = now + timedelta(minutes=1)
                continue
            if (
                counts(86400) >= p.max_sms_per_day
                or counts(86400, True) >= p.max_per_customer_day
            ):
                raise ValueError("SMS daily/customer frequency cap reached")
            recent = db.scalar(
                select(Attempt.id)
                .join(Delivery)
                .join(Plan)
                .where(
                    Delivery.recipient_key == d.recipient_key,
                    Delivery.id != d.id,
                    Attempt.provider_name.is_not(None),
                    Attempt.acceptance != "not_accepted",
                    Attempt.created_at
                    >= now - timedelta(seconds=p.outage_cooldown_seconds),
                    Plan.kind.in_(["outage", "update", "extended"]),
                )
                .limit(1)
            )
            if recent and plan.kind in {"outage", "update", "extended"}:
                raise ValueError("Incident cooldown active")
            parts = segments(plan.message)
            amount = parts * row.config["price_per_segment_microunits"]
            enforce_budget(db, row, amount, now)
            d.attempts += 1
            d.state, d.sms_provider = "submitted", name
            a = Attempt(
                delivery_id=d.id,
                attempt=d.attempts,
                outcome="submitted",
                provider_name=name,
                claim_token=str(uuid4()),
                acceptance="pending",
                lease_expires_at=now + timedelta(minutes=2),
                estimated_segments=parts,
                estimated_cost_microunits=amount,
                currency=row.config["currency"],
                destination_hash=native_contact_hash(account.phone),
                content_hash=digest(plan.message),
                config_revision=row.revision,
                created_at=now,
                updated_at=now,
            )
            db.add(a)
            db.flush()
            audit(
                db,
                None,
                "sms_submission_reserved",
                delivery_id=d.id,
                plan_id=plan.id,
                provider=name,
                attempt=a.attempt,
            )
            return a.id
        except (ValueError, LookupError, OSError):
            d.state, d.last_error, d.completed_at = (
                "suppressed",
                "native_approval_or_policy_changed",
                now,
            )
            audit(
                db, None, "sms_submission_suppressed", delivery_id=d.id, provider=name
            )
    return None


def record_provider_opt_out(db, attempt, delivery, code, now):
    key = digest(["provider-opt-out", attempt.id, code])
    if not db.get(Event, key):
        db.add(
            Event(
                key=key,
                provider=attempt.provider_name,
                message_id=attempt.provider_message_id or attempt.claim_token,
                kind="stop",
                raw_status="provider_opt_out",
                provider_error_code=code,
                body_hash=digest([attempt.id, code]),
                contact_hash=attempt.destination_hash,
                received_at=now,
            )
        )
    recipient = (
        db.get(Recipient, delivery.recipient_id) if delivery.recipient_id else None
    )
    account = db.get(CustomerAccount, recipient.customer_id) if recipient and recipient.customer_id is not None else None
    if account and native_contact_hash(account.phone) == attempt.destination_hash:
        db.execute(
            text("SELECT public.noc_sms_enforce_opt_out(:phone,:at)"),
            {"phone": account.phone, "at": now},
        )
    delivery.state, delivery.last_error, delivery.completed_at = (
        "suppressed",
        "provider_opt_out",
        now,
    )
    attempt.outcome = "suppressed"


def finish(db, attempt_id, result=None, failure=None, now=None):
    now = now or clock()
    lock(db)
    a = db.scalar(select(Attempt).where(Attempt.id == attempt_id).with_for_update())
    d = db.get(Delivery, a.delivery_id)
    if a.acceptance != "pending":
        return
    if failure:
        a.acceptance = "not_accepted" if failure.not_accepted else "unknown"
        a.outcome = failure.code
        a.provider_error_code = failure.provider_code
        a.updated_at = now
        d.last_error = failure.code
        if failure.code == "opt_out":
            record_provider_opt_out(db, a, d, failure.provider_code, now)
        elif not failure.not_accepted:
            d.state = "reconciliation_required"
        else:
            r = route(db)
            secondary = r.get("secondary")
            used_secondary = secondary and db.scalar(
                select(Attempt.id)
                .where(Attempt.delivery_id == d.id, Attempt.provider_name == secondary)
                .limit(1)
            )
            can_failover = (
                r.get("failover")
                and a.provider_name == r["primary"]
                and secondary
                and not used_secondary
                and failure.code in {"authentication_failed", "rate_limited"}
            )
            d.sms_provider = secondary if can_failover else a.provider_name
            retry_safe = can_failover or failure.code == "rate_limited"
            d.state = (
                "retry"
                if retry_safe and d.attempts < policy(db).max_attempts
                else "failed"
            )
            d.next_attempt_at = now + timedelta(
                seconds=max(failure.retry_after, min(300, 5 * 2 ** (d.attempts - 1)))
            )
            if d.state == "failed":
                d.completed_at = now
        db.get(Provider, a.provider_name).health = failure.code
    else:
        a.acceptance, a.outcome = "accepted", result.state
        a.provider_message_id, a.raw_status = result.message_id, result.raw_status
        a.reported_cost_microunits, a.updated_at = result.reported_cost_microunits, now
        d.provider_receipt, d.provider_status, d.provider_status_at = (
            result.message_id,
            result.raw_status,
            now,
        )
        d.state, d.last_error, d.completed_at = (
            next_state(d.state, result.state),
            None,
            now,
        )
        row = db.get(Provider, a.provider_name)
        row.last_submission_at, row.health = now, "submission_verified"
        a.provider_error_code = result.provider_error_code
        if result.opt_out:
            record_provider_opt_out(db, a, d, result.provider_error_code, now)
        # Callbacks can arrive before the HTTP receipt commits. Attach verified
        # orphan statuses only with provider/message/recipient identity matching.
        for event in db.scalars(
            select(Event)
            .where(
                Event.provider == a.provider_name,
                Event.message_id == result.message_id,
                Event.kind == "status",
                Event.delivery_id.is_(None),
                Event.contact_hash == a.destination_hash,
            )
            .order_by(Event.received_at)
        ):
            from app.notifications.sms_callbacks import apply_status

            event.delivery_id = d.id
            apply_status(d, a, event.raw_status, event.received_at)
            if event.reported_cost_microunits is not None:
                a.reported_cost_microunits = event.reported_cost_microunits
            if event.provider_error_code in {"21610", "40300"}:
                record_provider_opt_out(db, a, d, event.provider_error_code, now)
    audit(
        db,
        None,
        "sms_submission_result",
        delivery_id=d.id,
        provider=a.provider_name,
        state=d.state,
        acceptance=a.acceptance,
    )
    db.flush()


def run_native_batch(session_factory, *, transports=None, now=None, limit=20):
    host_authorized()
    if not 1 <= limit <= 100:
        raise ValueError("Bounded batch required")
    processed = 0
    for _ in range(limit):
        with session_factory() as db:
            ident = reserve(db, now or clock())
            db.commit()  # Durable claim MUST precede any provider I/O.
        if ident is None:
            break
        try:
            with session_factory() as db:
                a = db.get(Attempt, ident)
                d = db.get(Delivery, a.delivery_id)
                if a.acceptance != "pending" or d.state != "submitted":
                    continue
                row, plan, account = validate_delivery(
                    db, d, a.provider_name, now or clock()
                )
                if transports is not None and row.name not in transports:
                    raise ValueError(
                        "Mock acceptance requires an explicit transport for each selected provider"
                    )
                provider = NativeSMS(
                    row.name,
                    dict(row.config),
                    credentials(db, row.name),
                    transport=transports.get(row.name)
                    if transports is not None
                    else None,
                    authorized=True,
                )
                destination, body, key = account.phone, plan.message, a.claim_token
            try:
                result = provider.submit(destination=destination, body=body, key=key)
            except SMSFailure as error:
                with session_factory() as db:
                    finish(db, ident, failure=error, now=now)
                    db.commit()
            else:
                with session_factory() as db:
                    finish(db, ident, result=result, now=now)
                    db.commit()
        except (ValueError, LookupError, OSError):
            # No provider call was made after a failed final approval check.
            with session_factory() as db:
                lock(db)
                a = db.get(Attempt, ident)
                if a.acceptance == "pending":
                    a.acceptance, a.outcome = "not_accepted", "authorization_revoked"
                    d = db.get(Delivery, a.delivery_id)
                    d.state, d.last_error = "suppressed", "authorization_revoked"
                    db.commit()
        processed += 1
    return {"processed": processed, "external_delivery_enabled": True}


def manual_failover(db, identifier, actor, reference):
    lock(db)
    d = db.get(Delivery, identifier)
    if not d or not d.sms_authorization or d.state not in {"failed", "retry"}:
        raise ValueError("No safely rejected native submission to switch")
    a = db.scalar(
        select(Attempt)
        .where(Attempt.delivery_id == d.id)
        .order_by(Attempt.id.desc())
        .limit(1)
    )
    target = route(db).get("secondary")
    if (
        not a
        or a.acceptance != "not_accepted"
        or not target
        or target == a.provider_name
        or a.provider_name != route(db)["primary"]
        or d.attempts >= policy(db).max_attempts
    ):
        raise ValueError(
            "Failover forbidden without proof of non-acceptance and an authorized secondary"
        )
    validate_delivery(db, d, target, clock())
    row = provider_ready(db, target)
    p = db.get(Plan, d.plan_id)
    enforce_budget(
        db,
        row,
        segments(p.message) * row.config["price_per_segment_microunits"],
        clock(),
    )
    d.sms_provider, d.state, d.next_attempt_at = target, "retry", clock()
    audit(
        db,
        actor,
        "sms_manual_failover_approved",
        delivery_id=d.id,
        provider=target,
        reference=reference,
    )
    return d


def reconcile_attempt(
    db, identifier, actor, reference, supplied_id=None, *, transport=None
):
    host_authorized()
    a = db.get(Attempt, identifier)
    if not a or not a.provider_name:
        raise LookupError()
    d = db.get(Delivery, a.delivery_id)
    recipient = db.get(Recipient, d.recipient_id)
    if recipient and recipient.application_id is not None:
        from app.signup.communications import contact
        account = contact(db, recipient, consent=False)
    else:
        account = db.get(CustomerAccount, recipient.customer_id) if recipient and recipient.customer_id is not None else None
    if not account or native_contact_hash(account.phone) != a.destination_hash:
        raise ValueError(
            "Original recipient changed; preserve quarantine for manual provider investigation"
        )
    if (
        a.acceptance == "pending"
        and a.lease_expires_at
        and utc(a.lease_expires_at) > clock()
    ):
        raise ValueError("Submission lease is active; wait for bounded recovery")
    identifier = a.provider_message_id or supplied_id
    if not identifier:
        raise ValueError(
            "No receipt: independent provider investigation required; a missing callback is not proof of rejection"
        )
    identifier = message_id(a.provider_name, identifier)
    if a.provider_message_id and supplied_id and supplied_id != a.provider_message_id:
        raise ValueError("Provider receipt identity cannot change")
    row = db.get(Provider, a.provider_name)
    provider = NativeSMS(
        row.name,
        row.config,
        credentials(db, row.name),
        transport=transport,
        authorized=True,
    )
    result = provider.reconcile(identifier, destination=account.phone)
    if result.message_id != identifier:
        raise ValueError("Provider receipt mismatch")
    # An operator-supplied previously unknown receipt also requires exact approved
    # content evidence. Neither GET404 nor a failed delivery authorizes resending.
    if not a.provider_message_id:
        value = provider.last_message_body
        if value is None or digest(value) != a.content_hash:
            raise ValueError(
                "Cannot bind an unknown receipt without exact approved content evidence"
            )
        from email.utils import parsedate_to_datetime

        created = provider.last_created_at
        try:
            created = (
                datetime.fromisoformat(created.replace("Z", "+00:00"))
                if a.provider_name == "telnyx"
                else parsedate_to_datetime(created)
            )
            if created.tzinfo is None or not utc(a.created_at) - timedelta(
                seconds=30
            ) <= created <= utc(a.created_at) + timedelta(minutes=2):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError(
                "Unknown receipt requires matching provider creation-time evidence"
            ) from None
        existing = db.scalar(
            select(Attempt.id)
            .where(
                Attempt.provider_name == a.provider_name,
                Attempt.provider_message_id == identifier,
                Attempt.id != a.id,
            )
            .limit(1)
        )
        if existing:
            raise ValueError("Receipt already belongs to another attempt")
    lock(db)
    db.refresh(a)
    db.refresh(d)
    a.provider_message_id, a.acceptance, a.raw_status = (
        identifier,
        "accepted",
        result.raw_status,
    )
    a.reported_cost_microunits, a.outcome, a.updated_at = (
        result.reported_cost_microunits,
        result.state,
        clock(),
    )
    d.state, d.provider_receipt, d.provider_status = (
        next_state(d.state, result.state),
        identifier,
        result.raw_status,
    )
    d.provider_status_at = clock()
    a.provider_error_code = result.provider_error_code
    if result.opt_out:
        record_provider_opt_out(db, a, d, result.provider_error_code, clock())
    audit(
        db,
        actor,
        "sms_provider_reconciled",
        delivery_id=d.id,
        attempt_id=a.id,
        provider=a.provider_name,
        state=d.state,
        reference=reference,
    )
    db.flush()
    return d


def summary(db, since, until):
    charge = case((Attempt.acceptance == "not_accepted", 0), else_=cost_value())
    rows = db.execute(
        select(
            Attempt.provider_name,
            Attempt.outcome,
            Attempt.acceptance,
            func.count(),
            func.coalesce(func.sum(Attempt.estimated_segments), 0),
            func.coalesce(func.sum(Attempt.estimated_cost_microunits), 0),
            func.sum(Attempt.reported_cost_microunits),
            func.count(Attempt.reported_cost_microunits),
            func.coalesce(func.sum(charge), 0),
        )
        .where(
            Attempt.provider_name.is_not(None),
            Attempt.created_at >= since,
            Attempt.created_at < until,
        )
        .group_by(Attempt.provider_name, Attempt.outcome, Attempt.acceptance)
    ).all()
    result = {}
    for (
        name,
        status,
        acceptance,
        count,
        parts,
        estimated,
        reported,
        reported_count,
        reserved,
    ) in rows:
        r = result.setdefault(
            name,
            {
                "submitted_attempts": 0,
                "accepted_attempts": 0,
                "rejected_attempts": 0,
                "unresolved_attempts": 0,
                "delivered": 0,
                "failed": 0,
                "estimated_segments": 0,
                "estimated_cost_microunits": 0,
                "reserved_or_incurred_microunits": 0,
                "provider_reported_cost_microunits": 0,
                "provider_reported_count": 0,
                "currency": "USD",
            },
        )
        r["submitted_attempts"] += count
        r["accepted_attempts"] += count if acceptance == "accepted" else 0
        r["rejected_attempts"] += count if acceptance == "not_accepted" else 0
        r["unresolved_attempts"] += (
            count
            if acceptance in {"unknown", "pending"}
            or status == "reconciliation_required"
            else 0
        )
        r["delivered"] += count if status == "delivered" else 0
        r["failed"] += (
            count
            if status in {"undelivered", "failed"} and acceptance == "accepted"
            else 0
        )
        r["estimated_segments"] += parts
        r["estimated_cost_microunits"] += estimated
        r["reserved_or_incurred_microunits"] += reserved
        r["provider_reported_cost_microunits"] += reported or 0
        r["provider_reported_count"] += reported_count
    for r in result.values():
        terminal = r["delivered"] + r["failed"]
        r["delivery_success_rate"] = r["delivered"] / terminal if terminal else None
        r["provider_failure_rate"] = (
            (r["failed"] + r["rejected_attempts"]) / r["submitted_attempts"]
            if r["submitted_attempts"]
            else None
        )
        if not r["provider_reported_count"]:
            r["provider_reported_cost_microunits"] = None
    return {
        "providers": result,
        "since": since,
        "until": until,
        "method": "Submission-time UTC cohorts; delivered/known terminal outcomes and (rejected+failed)/all attempts; unresolved outcomes reported separately",
        "cost_note": "Per-attempt estimates, conservative reservations and provider-reported costs are separate; none is verified billed or collected cash",
    }


def signup_preview(db, identifier, actor):
    """Use existing protected application/draft records; never invent an incident."""
    from app.signup.models import NotificationDraft, Application
    from app.signup.service import pii

    draft = db.get(NotificationDraft, identifier)
    if not draft or draft.channel != "sms":
        raise LookupError()
    application = db.get(Application, draft.application_id)
    data = pii(application)
    phone = data.get("phone")
    allowed = bool(
        draft.eligible
        and data.get("consent_sms")
        and data.get("preferred_contact") == "sms"
        and e164(phone)
    )
    identity = native_contact_hash(phone) if e164(phone) else None
    if identity and db.scalar(
        select(Event.key)
        .where(Event.kind == "stop", Event.contact_hash == identity)
        .limit(1)
    ):
        allowed = False
    if phone and db.scalar(
        select(CustomerAccount.id)
        .where(CustomerAccount.phone == phone, CustomerAccount.sms_opt_out.is_(True))
        .limit(1)
    ):
        allowed = False
    category = (
        "installation_scheduling"
        if draft.template == "installation_scheduling"
        else "signup_application"
    )
    parts = segments(draft.policy["text"])
    previews = []
    for row in db.scalars(select(Provider).order_by(Provider.name)):
        price = row.config.get("price_per_segment_microunits")
        previews.append(
            {
                "provider": row.name,
                "eligible_for_review": bool(
                    allowed
                    and category in row.config["categories"]
                    and identity in row.config["recipient_hashes"]
                ),
                "estimated_segments": parts,
                "estimated_cost_microunits": parts * price if price else None,
                "currency": row.config["currency"],
                "delivery_enabled": False,
            }
        )
    audit(
        db,
        actor,
        "sms_signup_draft_previewed",
        draft_id=draft.id,
        application_id=application.id,
    )
    return {
        "draft_id": draft.id,
        "application_id": application.id,
        "category": category,
        "eligible_for_review": allowed,
        "providers": previews,
        "messages_sent": 0,
        "next_action": "Prepare the canonical prospect SMS plan, approve its fingerprint and independently authorize native delivery; no customer or incident is fabricated",
    }
