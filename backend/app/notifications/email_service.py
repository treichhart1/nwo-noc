"""Email claims on NOC-96 outbox. Intent is committed before any network I/O."""

from datetime import timedelta, timezone
import hashlib, hmac, json
from uuid import uuid4
from sqlalchemy import select, func
from app.core.config import settings
from app.models.entities import User
from app.customers.models import CustomerAccount
from app.signup.models import Application
from app.signup import service as signup
from app.notifications.models import (
    NotificationPlan as Plan,
    NotificationDelivery as Delivery,
    NotificationAttempt as Attempt,
    NotificationCredential as Credential,
)
from app.notifications.email_models import (
    EmailProvider as Provider,
    EmailPreference as Preference,
    EmailSuppression as Suppression,
    EmailTemplate as Template,
    EmailEnvelope as Envelope,
    EmailEvent as Event,
    EmailPreferenceEvent as PreferenceEvent,
    EmailRoutingRow as Routing,
)
from app.notifications.email_contracts import address
from app.notifications.email_transport import (
    EmailFailure,
    MockEmail,
    SendGrid,
    SMTP,
    render,
    verified_events,
)
from app.notifications import service as ns
from app.notifications.secrets import cipher, seal, unseal
from app.notifications.controls import require_processing, emergency_disabled


def identity(value):
    return hmac.new(
        settings.secret_key.encode(),
        ("email:" + address(value).casefold()).encode(),
        hashlib.sha256,
    ).hexdigest()


def encrypt(value):
    return (
        cipher(settings.notifications_vault_key_file)
        .encrypt(json.dumps(value).encode())
        .decode()
    )


def decrypt(value):
    try:
        return json.loads(
            cipher(settings.notifications_vault_key_file).decrypt(value.encode())
        )
    except Exception:
        raise ValueError("Private email evidence unavailable") from None


def subject(db, customer_id=None, application_id=None):
    if (customer_id is None) == (application_id is None):
        raise ValueError("One canonical subject required")
    if customer_id:
        row = db.get(CustomerAccount, customer_id)
        if not row or row.source_deleted_at:
            raise ValueError("Customer unavailable")
        return "customer:" + str(row.id), address(row.email)
    row = db.get(Application, application_id)
    if not row or row.redacted_at or not row.wrapped_key:
        raise ValueError("Applicant unavailable")
    return "application:" + str(row.id), address(signup.pii(row).get("email"))


def permission(db, body, actor):
    ns.lock(db)
    key, email = subject(db, body.customer_id, body.application_id)
    if email != body.verified_address:
        raise ValueError("Independent verification must match current canonical email")
    p = db.scalar(
        select(Preference).where(
            Preference.subject_key == key, Preference.purpose == body.purpose
        )
    )
    if not p:
        p = Preference(
            subject_key=key,
            customer_id=body.customer_id,
            application_id=body.application_id,
            purpose=body.purpose,
            revision=0,
        )
        db.add(p)
    p.contact_hash = identity(email)
    p.granted = body.granted
    p.reference = body.reference
    p.verified_by = actor
    p.revision += 1
    p.updated_at = ns.clock()
    db.flush()
    db.add(
        PreferenceEvent(
            preference_id=p.id,
            revision=p.revision,
            granted=p.granted,
            contact_hash=p.contact_hash,
            reference=p.reference,
            actor_id=actor,
        )
    )
    ns.audit(
        db,
        actor,
        "email_purpose_permission_changed",
        preference_id=p.id,
        granted=p.granted,
        purpose=p.purpose,
    )
    # Neither changing channel preferences nor provider switching clears suppression.
    return p


def host_authorized():
    if (
        not settings.email_native_transport_enabled
        or not settings.email_egress_approved
        or settings.offline_mode
    ):
        raise ValueError("Independent host email/egress authorization required")


def ready(db, name, *, mock=False):
    row = db.get(Provider, name)
    if not row or not row.config.get("enabled"):
        raise ValueError("Email provider disabled")
    if mock:
        if name != "mock" or not settings.offline_mode or settings.app_env != "staging":
            raise ValueError("Mock only in offline staging")
        return row
    host_authorized()
    if (
        name not in {"sendgrid", "smtp"}
        or not row.live_authorized
        or not row.authorization_reference
    ):
        raise ValueError("Independent provider authorization required")
    c = row.config
    if not c.get("recipient_hashes") or c.get("estimated_cost_microunits") is None:
        raise ValueError("Reviewed recipient allowlist and pricing required")
    if name == "sendgrid" and (
        not row.webhook_public_key or not c.get("callbacks_enabled")
    ):
        raise ValueError("Signed SendGrid callback configuration required")
    if name == "smtp" and c.get("smtp_host") not in settings.email_smtp_allowed_hosts:
        raise ValueError("SMTP host not approved at host boundary")
    secret = db.get(Credential, "email.native." + name)
    if not secret:
        raise ValueError("Email credentials unavailable")
    return row


def source_snapshot(db, source, template):
    kind = source["source_kind"]
    identifier = source["source_id"]
    purpose = template.config["purpose"]
    if kind == "signup":
        a = db.get(Application, identifier)
        if (
            not a
            or a.redacted_at
            or a.id != source.get("application_id")
            or source.get("customer_id")
            or purpose not in {"signup", "installation"}
        ):
            raise ValueError("Current signup source required")
        data = signup.pii(a)
        if not data.get("consent_email") or data.get("consent_withdrawn_at"):
            raise ValueError("Applicant email permission withdrawn")
        gates = {
            "survey_scheduling": {"site_survey_required", "survey_scheduled"},
            "qualification_result": {
                "qualified",
                "site_survey_required",
                "qualification_pending",
                "rejected",
            },
            "quote_available": {"quote_sent", "customer_accepted"},
            "installation_scheduling": {
                "installation_pending",
                "installation_scheduled",
            },
            "installation_reminder": {"installation_scheduled"},
            "appointment_rescheduling": {"survey_scheduled", "installation_scheduled"},
            "installation_completion": {"installed", "activation_pending", "completed"},
            "activation_update": {"activation_pending", "completed"},
        }
        if template.key in gates and a.state not in gates[template.key]:
            raise ValueError("Source lifecycle does not support this communication")
        if template.key == "quote_available":
            from app.signup.models import Quote

            if not db.scalar(
                select(Quote.id)
                .where(
                    Quote.application_id == a.id,
                    Quote.approved_by.is_not(None),
                    Quote.issued_at.is_not(None),
                    Quote.expires_at > ns.clock(),
                    Quote.status.in_(["issued", "accepted"]),
                )
                .limit(1)
            ):
                raise ValueError("Issued approved unexpired quote required")
        return {
            "kind": kind,
            "id": a.id,
            "revision": a.revision,
            "state": a.state,
            "reference": a.reference
            if hasattr(a, "reference")
            else "application-" + str(a.id),
            "status": a.state,
        }
    if kind == "cancellation":
        from app.retention.models import Case, CaseService

        c = db.get(Case, identifier)
        if (
            not c
            or c.redacted_at
            or not c.verified_by
            or c.customer_id != source.get("customer_id")
            or source.get("application_id")
            or purpose not in {"cancellation", "retention"}
        ):
            raise ValueError("Identity-reviewed cancellation required")
        if template.key == "retention_offer":
            from app.retention.models import Offer
            from app.retention.service import offer_context
            from app.signup import crypto

            offer = db.scalar(
                select(Offer)
                .where(
                    Offer.case_id == c.id,
                    Offer.status == "approved",
                    Offer.approved_by.is_not(None),
                    Offer.expires_at > ns.clock(),
                )
                .order_by(Offer.id.desc())
                .limit(1)
            )
            if not offer or crypto.unseal(c, offer.terms_ciphertext).get(
                "service_evidence"
            ) != offer_context(db, c):
                raise ValueError(
                    "Independent current retention offer approval required"
                )
        if template.key == "cancellation_completion":
            links = list(
                db.scalars(select(CaseService).where(CaseService.case_id == c.id))
            )
            if (
                c.status not in {"closed", "disconnected"}
                or not links
                or any(not x.actual_disconnected_at for x in links)
            ):
                raise ValueError("Observed disconnection evidence required")
        return {
            "kind": kind,
            "id": c.id,
            "revision": c.revision,
            "state": c.status,
            "reference": c.reference,
            "status": c.status,
        }
    if kind == "network":
        p = db.get(Plan, identifier)
        if (
            not p
            or p.snapshot.get("email")
            or p.snapshot.get("prospect")
            or p.snapshot.get("retention")
            or purpose not in {"operational", "maintenance"}
        ):
            raise ValueError("Canonical network source required")
        current, _ = ns.current_snapshot(db, p, ns.clock())
        # Verified impact, independent of SMS consent. Uncertain mappings excluded.
        impacts = [
            x
            for x in current.get("impacts", [])
            if x.get("customer_id") == source.get("customer_id")
            and x.get("classification") == "confirmed"
        ]
        request = current["request"]
        blocked = {
            "flapping",
            "planned_maintenance_suppression",
            "unconfirmed_or_brief_event",
        }
        if any(
            r.get("reason") in blocked
            for r in current.get("recipients", [])
            if r.get("customer_id") == source.get("customer_id")
        ):
            raise ValueError("Network source currently suppressed")
        if not impacts or not request["evidence"]["confirmed"]:
            raise ValueError("Confirmed customer impact required")
        if request["kind"] == "restoration" and (
            not request["evidence"].get("recovery_verified")
            or request["evidence"].get("recovery_samples", 0) < 3
        ):
            raise ValueError("Independent recovery evidence required")
        if (
            template.key in {"verified_restoration", "maintenance_restoration"}
            and request["kind"] != "restoration"
        ):
            raise ValueError(
                "Restoration template requires independently verified recovery"
            )
        if request["kind"] == "restoration" and template.key not in {
            "verified_restoration",
            "maintenance_restoration",
        }:
            raise ValueError("Recovery requires restoration template")
        if (purpose == "maintenance") != bool(p.maintenance_id):
            raise ValueError("Network source purpose mismatch")
        return {
            "kind": kind,
            "id": p.id,
            "source_fingerprint": ns.digest(current),
            "reference": ("incident-" + str(p.incident_id))
            if p.incident_id
            else "maintenance-" + str(p.maintenance_id),
            "status": request["kind"],
        }
    raise ValueError("Unsupported canonical source")


def snapshot(db, e):
    t = db.get(Template, e.template_id)
    p = db.get(Preference, e.preference_id)
    if not t or not p or not p.granted:
        raise ValueError("Verified email purpose permission required")
    key, email = subject(db, p.customer_id, p.application_id)
    h = identity(email)
    if h != p.contact_hash or db.get(Suppression, h):
        raise ValueError("Email changed or shared email suppression")
    if t.config["purpose"] != p.purpose:
        raise ValueError("Independent purpose permission required")
    s = source_snapshot(db, e.source, t)
    payload = render(t.config, s)
    return {
        "source": s,
        "template_id": t.id,
        "template_revision": t.revision,
        "preference_id": p.id,
        "preference_revision": p.revision,
        "contact_hash": h,
        "message": payload,
        "policy": ns.policy(db).model_dump(mode="json"),
    }, email


def prepare(db, body, actor):
    ns.lock(db)
    require_processing(db)
    t = db.get(Template, body.template_id)
    if not t:
        raise LookupError()
    key, email = subject(db, body.customer_id, body.application_id)
    p = db.scalar(
        select(Preference).where(
            Preference.subject_key == key, Preference.purpose == t.config["purpose"]
        )
    )
    if not p:
        raise ValueError("Verify identity and purpose permission first")
    routing = db.get(Routing, 1)
    name = routing.config["primary"] if routing else "mock"
    if not db.get(Provider, name):
        raise ValueError("Configure selected provider first")
    source = body.model_dump(exclude={"template_id"})
    temporary = Envelope(source=source, template_id=t.id, preference_id=p.id)
    current, email = snapshot(db, temporary)
    fingerprint = ns.digest(current)
    dedupe = ns.digest(["email", source, t.id, p.id])
    old = db.scalar(select(Plan).where(Plan.dedupe_key == dedupe))
    if old:
        if old.fingerprint != fingerprint:
            raise ValueError(
                "Source changed; select a new template revision before redrafting"
            )
        return old
    plan = Plan(
        dedupe_key=dedupe,
        kind="email_" + t.config["purpose"],
        severity="info",
        confidence="operator_review",
        status="draft",
        target={"source": source},
        evidence={"email_source": source},
        snapshot={"email": True},
        fingerprint=fingerprint,
        created_by=actor,
        due_at=ns.clock(),
        message="Encrypted email; review private preview",
        slack_payload={},
    )
    db.add(plan)
    db.flush()
    d = Delivery(
        plan_id=plan.id,
        channel="email",
        recipient_key=key,
        state="draft",
        next_attempt_at=ns.clock(),
    )
    db.add(d)
    db.flush()
    db.add(
        Envelope(
            delivery_id=d.id,
            provider=name,
            source=source,
            template_id=t.id,
            preference_id=p.id,
            contact_hash=identity(email),
            ciphertext=encrypt({"destination": email, "payload": current["message"]}),
        )
    )
    ns.audit(db, actor, "email_draft_prepared", plan_id=plan.id, delivery_id=d.id)
    return plan


def validate(db, d, e, *, mock=False):
    require_processing(db)
    plan = db.get(Plan, d.plan_id)
    auth = e.authorization or {}
    actor = db.get(User, auth.get("actor")) if auth.get("actor") else None
    p = ready(db, e.provider, mock=mock)
    current, email = snapshot(db, e)
    if (
        not actor
        or not actor.is_active
        or actor.role != "admin"
        or plan.status != "approved"
        or auth.get("fingerprint") != plan.fingerprint
        or ns.digest(current) != plan.fingerprint
        or auth.get("provider_revision") != p.revision
    ):
        raise ValueError("Email approval or source changed")
    c = p.config
    if current["source"]["kind"] == "network":
        from app.customer_status.service import linked_approval

        linked_approval(db, db.get(Plan, e.source["source_id"]), actor.id)
    if current["contact_hash"] not in c.get("recipient_hashes", []) or db.get(
        Template, e.template_id
    ).config["purpose"] not in c.get("purposes", []):
        raise ValueError("Recipient/purpose not independently approved")
    day = ns.clock() - timedelta(days=1)
    month = ns.clock().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for start, countlimit, budget in (
        (day, c["daily_messages"], c["daily_budget_microunits"]),
        (month, c["monthly_messages"], c["monthly_budget_microunits"]),
    ):
        q = select(
            func.count(Attempt.id),
            func.coalesce(func.sum(Attempt.estimated_cost_microunits), 0),
        ).where(
            Attempt.email_provider == e.provider,
            Attempt.created_at >= start,
            Attempt.acceptance != "not_accepted",
        )
        count, cost = db.execute(q).one()
        if count >= countlimit or (
            c.get("estimated_cost_microunits") is not None
            and cost + c["estimated_cost_microunits"] > budget
        ):
            raise ValueError("Email usage/budget exhausted")
    perminute = db.scalar(
        select(func.count(Attempt.id)).where(
            Attempt.email_provider == e.provider,
            Attempt.created_at >= ns.clock() - timedelta(minutes=1),
        )
    )
    if perminute >= c["per_minute"]:
        raise ValueError("Email rate limit")
    recent = db.scalar(
        select(Delivery.id)
        .join(Envelope, Envelope.delivery_id == Delivery.id)
        .where(
            Envelope.contact_hash == e.contact_hash,
            Delivery.id != d.id,
            Delivery.state.in_(
                [
                    "submitting",
                    "accepted",
                    "sent",
                    "delivered",
                    "simulated",
                    "reconciliation_required",
                ]
            ),
            Delivery.next_attempt_at
            >= ns.clock() - timedelta(seconds=ns.policy(db).outage_cooldown_seconds),
        )
        .limit(1)
    )
    if recent:
        raise ValueError("Email contact cooldown")
    return p, current, email


def approve(db, identifier, body, actor, *, mock=False, provider=None):
    ns.lock(db)
    d = db.get(Delivery, identifier)
    e = db.get(Envelope, identifier)
    if not d or not e or d.channel != "email":
        raise LookupError()
    if not body.confirm or not body.fingerprint:
        raise ValueError("Independent exact email approval required")
    previous = list(db.scalars(select(Attempt).where(Attempt.delivery_id == d.id)))
    if previous and any(a.acceptance != "not_accepted" for a in previous):
        raise ValueError("Ambiguous/accepted submission cannot be retried or switched")
    if (
        d.state not in {"draft", "failed", "dead_letter", "suppressed"}
        or d.attempts >= 3
    ):
        raise ValueError("Only unsubmitted or proven rejection can be approved")
    if provider:
        if provider not in {"sendgrid", "smtp", "mock"}:
            raise ValueError("Unsupported provider")
        e.provider = provider
    p = ready(db, e.provider, mock=mock)
    plan = db.get(Plan, d.plan_id)
    current, _ = snapshot(db, e)
    if plan.fingerprint != body.fingerprint or ns.digest(current) != plan.fingerprint:
        raise ValueError("Source/consent/content changed")
    e.authorization = {
        "actor": actor,
        "reference": body.reference,
        "fingerprint": body.fingerprint,
        "provider_revision": p.revision,
        "simulation": mock,
    }
    d.budget_charge_microunits = p.config.get("estimated_cost_microunits")
    plan.estimated_cost_microunits = d.budget_charge_microunits
    plan.status = "approved"
    plan.approved_by = actor
    plan.approved_at = ns.clock()
    d.state = "queued"
    d.next_attempt_at = ns.clock()
    validate(db, d, e, mock=mock)
    ns.audit(
        db,
        actor,
        "email_independently_approved",
        plan_id=plan.id,
        delivery_id=d.id,
        simulation=mock,
    )
    return d


def run_batch(factory, *, mock=False, transport=None, smtp_factory=None, limit=20):
    if not 1 <= limit <= 100:
        raise ValueError("Bounded batch required")
    if mock and (not settings.offline_mode or settings.app_env != "staging"):
        raise ValueError("Offline staging required")
    if not mock:
        host_authorized()
    processed = 0
    for _ in range(limit):
        with factory() as db:
            ns.lock(db)
            # Expired in-flight claims are ambiguous, never queued again.
            for a in db.scalars(
                select(Attempt)
                .where(
                    Attempt.email_provider.is_not(None),
                    Attempt.acceptance == "pending",
                    Attempt.lease_expires_at < ns.clock(),
                )
                .with_for_update()
            ):
                a.acceptance = "unknown"
                a.outcome = "reconciliation_required"
                db.get(Delivery, a.delivery_id).state = "reconciliation_required"
            d = db.scalar(
                select(Delivery)
                .where(
                    Delivery.channel == "email",
                    Delivery.state.in_(["queued", "retry"]),
                    Delivery.next_attempt_at <= ns.clock(),
                )
                .order_by(Delivery.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not d:
                db.commit()
                break
            e = db.get(Envelope, d.id)
            try:
                p, current, email = validate(db, d, e, mock=mock)
            except ValueError:
                d.state = "suppressed"
                d.last_error = "approval_or_eligibility_changed"
                db.commit()
                continue
            due = ns.quiet_due(ns.clock(), ns.policy(db), False)
            if due > ns.clock():
                d.next_attempt_at = due
                db.commit()
                continue
            payload = decrypt(e.ciphertext)
            if payload != {"destination": email, "payload": current["message"]}:
                raise ValueError("Encrypted email approval mismatch")
            secret = {}
            if not mock:
                secret = json.loads(
                    unseal(
                        settings.notifications_vault_key_file,
                        db.get(Credential, "email.native." + e.provider).ciphertext,
                    )
                )
            provider = (
                MockEmail()
                if mock
                else (
                    SendGrid(p.config, secret, transport=transport)
                    if e.provider == "sendgrid"
                    else SMTP(p.config, secret, factory=smtp_factory)
                )
            )
            d.attempts += 1
            d.state = "submitting"
            token = ns.digest(["email-attempt", str(uuid4())])
            e.correlation = token
            a = Attempt(
                delivery_id=d.id,
                attempt=d.attempts,
                outcome="intent_committed",
                email_provider=e.provider,
                claim_token=str(uuid4()),
                acceptance="pending",
                lease_expires_at=ns.clock() + timedelta(seconds=90),
                destination_hash=e.contact_hash,
                content_hash=plan_hash(db, d),
                config_revision=p.revision,
                estimated_cost_microunits=p.config.get("estimated_cost_microunits"),
                currency=p.config["currency"],
            )
            db.add(a)
            db.flush()
            ident = a.id
            did = d.id
            key = "noc-email:" + str(d.id)
            db.commit()
        # No global database locks during remote I/O. Emergency latch rechecked.
        try:
            with factory() as check:
                require_processing(check)
                validate_pending(check, did, mock)
            result = provider.send(
                key=key,
                destination=payload["destination"],
                payload=payload["payload"],
                correlation=token,
            )
            failure = None
        except EmailFailure as exc:
            result = None
            failure = exc
        except Exception:
            result = None
            failure = EmailFailure()
        with factory() as db:
            ns.lock(db)
            a = db.get(Attempt, ident)
            d = db.get(Delivery, did)
            e = db.get(Envelope, did)
            p = db.get(Provider, e.provider)
            if result:
                a.acceptance = "accepted"
                a.outcome = "simulated" if result.simulated else "accepted"
                a.provider_message_id = result.message_id
                # A verified callback can race the HTTP response; never regress it.
                if d.state == "submitting":
                    d.state = "simulated" if result.simulated else "accepted"
                d.provider_receipt = result.message_id
                p.last_submission_at = ns.clock()
                p.health = "submission_accepted"
            elif a.acceptance == "accepted":
                # A signed event may establish acceptance before an ambiguous HTTP response.
                a.provider_error_code = failure.code
                a.outcome = "signed_event_before_transport_result"
            else:
                a.acceptance = failure.acceptance
                a.outcome = failure.code
                d.last_error = failure.code
                if d.state == "submitting":
                    d.state = (
                        "reconciliation_required"
                        if failure.acceptance != "not_accepted"
                        else (
                            "retry"
                            if failure.code == "rate_limited" and d.attempts < 3
                            else "dead_letter"
                        )
                    )
                    d.next_attempt_at = ns.clock() + timedelta(
                        seconds=failure.retry_after
                    )
                p.health = failure.code
            a.updated_at = ns.clock()
            db.commit()
            processed += 1
    return {"processed": processed, "simulated": mock}


def plan_hash(db, d):
    return db.get(Plan, d.plan_id).fingerprint


def validate_pending(db, identifier, mock):
    d = db.get(Delivery, identifier)
    e = db.get(Envelope, identifier)
    # Current intent counts against budget; do not count it twice on final recheck.
    require_processing(db)
    p = ready(db, e.provider, mock=mock)
    current, email = snapshot(db, e)
    auth = e.authorization or {}
    actor = db.get(User, auth.get("actor")) if auth.get("actor") else None
    if (
        not actor
        or not actor.is_active
        or actor.role != "admin"
        or auth.get("provider_revision") != p.revision
        or ns.digest(current) != plan_hash(db, d)
        or identity(email) not in p.config["recipient_hashes"]
    ):
        raise ValueError("Email authorization revoked before I/O")


def apply_events(db, body, headers):
    p = db.get(Provider, "sendgrid")
    if not p or not p.webhook_public_key or not p.config.get("callbacks_enabled"):
        raise ValueError("Signed callback not configured")
    events = verified_events(
        body, headers, p.webhook_public_key, int(ns.clock().timestamp())
    )
    ns.lock(db)
    count = 0
    for event in events:
        token = event["noc_attempt"]
        e = db.scalar(select(Envelope).where(Envelope.correlation == token))
        if (
            not e
            or e.provider != "sendgrid"
            or identity(event["email"]) != e.contact_hash
        ):
            raise ValueError("Message does not belong to this provider/recipient")
        d = db.get(Delivery, e.delivery_id)
        a = db.scalar(
            select(Attempt)
            .where(Attempt.delivery_id == d.id, Attempt.email_provider == "sendgrid")
            .order_by(Attempt.id.desc())
            .limit(1)
        )
        if not a or a.acceptance == "not_accepted":
            raise ValueError("No submitted provider intent")
        key = ns.digest(["sendgrid", event["sg_event_id"]])
        digest = ns.digest(event)
        old = db.get(Event, key)
        if old:
            if old.body_hash != digest:
                raise ValueError("Conflicting webhook event identity")
            continue
        kind = event["event"]
        raw = event.get("sg_message_id")
        if raw is not None and (not isinstance(raw, str) or len(raw) > 255):
            raise ValueError("Bounded provider identifier required")
        db.add(
            Event(
                key=key,
                delivery_id=d.id,
                kind=kind,
                body_hash=digest,
                provider_message_id=raw,
            )
        )
        db.flush()
        if kind in {"bounce", "spamreport", "unsubscribe", "group_unsubscribe"}:
            hold = db.get(Suppression, e.contact_hash)
            if not hold:
                db.add(
                    Suppression(contact_hash=e.contact_hash, reason=kind, evidence=key)
                )
            state = {
                "bounce": "undelivered",
                "spamreport": "complaint",
                "unsubscribe": "unsubscribed",
                "group_unsubscribe": "unsubscribed",
            }[kind]
            if d.state not in {"complaint", "unsubscribed"}:
                d.state = state
        elif d.state not in {
            "delivered",
            "undelivered",
            "failed",
            "complaint",
            "unsubscribed",
            "suppressed",
        }:
            d.state = {
                "processed": "accepted",
                "delivered": "delivered",
                "deferred": "deferred",
                "dropped": "failed",
            }[kind]
        a.acceptance = "accepted"
        a.raw_status = kind
        d.provider_status = kind
        d.provider_status_at = ns.clock()
        p.last_callback_at = ns.clock()
        count += 1
        ns.audit(db, None, "email_verified_event", delivery_id=d.id, kind=kind)
    return {"processed": count}
