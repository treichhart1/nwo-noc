"""Native Slack uses canonical plans/deliveries/attempts and existing controls."""

from datetime import timedelta
from uuid import uuid4
import json
import re
from sqlalchemy import select, func, or_
from app.core.config import settings
from app.models.entities import User
from app.notifications.models import (
    NotificationPlan as Plan,
    NotificationDelivery as Delivery,
    NotificationAttempt as Attempt,
    NotificationCredential as Credential,
)
from app.notifications.slack_models import (
    SlackIntegration as Integration,
    SlackSource as Source,
    SlackThread as Thread,
    SlackInteraction as Interaction,
)
from app.notifications.service import lock, clock, utc, digest, audit, policy
from app.notifications.controls import require_processing
from app.notifications.secrets import seal, unseal
from app.notifications import slack_sources as sources
from app.notifications.slack_transport import NativeSlack, SlackFailure, webhook_url

RANK = {"info": 0, "warning": 1, "critical": 2}


def host_authorized():
    if (
        not settings.slack_native_transport_enabled
        or not settings.slack_egress_approved
        or settings.offline_mode
    ):
        raise ValueError("Independent native Slack host authorization required")


def credentials(db):
    row = db.get(Credential, "native_slack")
    if not row:
        raise ValueError("Private Slack credentials unavailable")
    try:
        return json.loads(unseal(settings.notifications_vault_key_file, row.ciphertext))
    except (ValueError, OSError):
        raise ValueError("Private Slack credentials unavailable") from None


def store_credentials(db, raw, actor):
    row = db.get(Integration, 1)
    if not row:
        raise ValueError("Configure Slack first")
    if db.scalar(
        select(Attempt.id)
        .join(Delivery)
        .where(
            Attempt.slack_mode.is_not(None),
            Attempt.acceptance.in_(["pending", "unknown"]),
            Delivery.state.in_(["submitted", "accepted", "reconciliation_required"]),
        )
        .limit(1)
    ):
        raise ValueError(
            "Drain and reconcile Slack attempts before credential rotation"
        )
    try:
        v = json.loads(raw)
        expected = (
            {"bot_token", "signing_secret"}
            if row.config["mode"] == "bot"
            else {"webhooks", "signing_secret"}
        )
        if not isinstance(v, dict) or set(v) != expected:
            raise ValueError()
        signing = v.pop("signing_secret")
        if not isinstance(signing, str) or not re.fullmatch(
            r"[a-fA-F0-9]{32,128}", signing
        ):
            raise ValueError()
        if row.config["mode"] == "bot":
            if not isinstance(v["bot_token"], str) or not re.fullmatch(
                r"xoxb-[A-Za-z0-9-]{10,500}", v["bot_token"]
            ):
                raise ValueError()
        else:
            if not isinstance(v["webhooks"], dict) or set(v["webhooks"]) != set(
                row.config["approved_channels"]
            ):
                raise ValueError()
            for value in v["webhooks"].values():
                webhook_url(value, row.config["workspace_id"])
        encrypted = seal(settings.notifications_vault_key_file, json.dumps(v))
        verifier = seal(settings.slack_callback_key_file, signing)
    except (ValueError, TypeError, KeyError, OSError):
        raise ValueError(
            "Invalid private Slack credential contract or key file"
        ) from None
    stored = db.get(Credential, "native_slack")
    if stored:
        stored.ciphertext = encrypted
        stored.updated_at = clock()
    else:
        db.add(Credential(provider="native_slack", ciphertext=encrypted))
    row.signing_ciphertext = verifier
    row.revision += 1
    row.live_authorized = False
    audit(db, actor, "slack_credentials_rotated")


def ready(db):
    host_authorized()
    require_processing(db)
    r = db.get(Integration, 1)
    if (
        not r
        or not r.config.get("enabled")
        or not r.live_authorized
        or not r.authorization_reference
        or not r.signing_ciphertext
        or not r.config.get("test_channels")
    ):
        raise ValueError("Slack lacks independent live authorization")
    credentials(db)
    return r


def route(config, fields):
    role = config["routing"].get(
        fields["event"],
        "maintenance"
        if fields["event"] in {"maintenance", "overdue_maintenance"}
        else "rf"
        if fields["event"] in {"rf_degradation", "suspected_interference", "congestion"}
        else "technician"
        if fields["event"] in {"technician_assignment", "installation", "signup"}
        else "default",
    )
    if fields["severity"] == "critical":
        role = "critical"
    channel = config["channels"][role]
    if (
        channel not in config["approved_channels"]
        or channel not in config["test_channels"]
    ):
        raise ValueError("Slack channel is not independently approved")
    return channel


def prepare(db, kind, identifier, actor):
    lock(db)
    fields = sources.snapshot(db, kind, identifier)
    fingerprint = digest(fields)
    key = digest(["native-slack", kind, str(identifier), fingerprint])
    p = db.scalar(select(Plan).where(Plan.dedupe_key == key))
    if p:
        return p
    p = Plan(
        dedupe_key=key,
        incident_id=fields["incident_id"],
        kind="update",
        severity=fields["severity"],
        confidence="recorded",
        status="draft",
        target={},
        evidence={},
        snapshot={"native_slack": fields},
        fingerprint=fingerprint,
        created_by=actor,
        due_at=clock(),
        message="Internal Slack evidence; no customer messaging",
        slack_payload={},
    )
    db.add(p)
    db.flush()
    db.add(
        Source(
            plan_id=p.id,
            kind=kind,
            identifier=str(identifier),
            site_id=fields["site_id"],
            incident_id=fields["incident_id"],
            work_order_id=fields["work_order_id"],
        )
    )
    audit(db, actor, "slack_draft_created", plan_id=p.id, source_kind=kind)
    return p


def topic(fields):
    return (
        "incident:" + str(fields["incident_id"])
        if fields.get("incident_id")
        else fields["source_kind"] + ":" + fields["source_id"]
    )


def approve(db, plan_id, actor, reference, fingerprint):
    lock(db)
    r = ready(db)
    p = db.get(Plan, plan_id)
    src = db.get(Source, plan_id)
    if not p:
        raise LookupError()
    if src:
        fields = sources.snapshot(db, src.kind, src.identifier)
        if p.fingerprint != digest(fields) or p.fingerprint != fingerprint:
            raise ValueError("Source changed; prepare and review again")
    else:
        # Existing NOC-96 incident/maintenance plan approval remains mandatory.
        if p.status != "approved" or p.fingerprint != fingerprint:
            raise ValueError("Review and approve the canonical notification plan first")
        fields = sources.snapshot(db, "plan", str(p.id))
        db.add(
            Source(
                plan_id=p.id,
                kind="plan",
                identifier=str(p.id),
                site_id=fields["site_id"],
                incident_id=fields["incident_id"],
                work_order_id=None,
            )
        )
    if p.status not in {"draft", "approved"}:
        raise ValueError("Plan cannot be released")
    u = db.get(User, actor)
    if not u or not u.is_active or u.role != "admin":
        raise ValueError("Active administrator required")
    channel = route(r.config, fields)
    if RANK[fields["severity"]] < RANK[r.config["minimum_severity"]]:
        raise ValueError("Below configured Slack severity threshold")
    d = db.scalar(
        select(Delivery).where(
            Delivery.plan_id == p.id,
            Delivery.channel == "slack",
            Delivery.recipient_key == channel,
        )
    )
    if d:
        if d.attempts or d.state not in {"queued", "suppressed"}:
            raise ValueError("A submitted Slack delivery cannot be released again")
    else:
        d = Delivery(
            plan_id=p.id,
            channel="slack",
            recipient_key=channel,
            next_attempt_at=clock(),
        )
        db.add(d)
        db.flush()
    # Suppress legacy simulated Slack rows before native release. Never alter SMS.
    for old in db.scalars(
        select(Delivery).where(
            Delivery.plan_id == p.id,
            Delivery.channel == "slack",
            Delivery.id != d.id,
            Delivery.state.in_(["queued", "retry"]),
        )
    ):
        old.state = "suppressed"
        old.last_error = "native_slack_released"
    d.state = "queued"
    d.next_attempt_at = clock()
    d.slack_context = {
        "source_fingerprint": digest(fields),
        "fields": fields,
        "topic": topic(fields),
        "channel": channel,
    }
    d.slack_authorization = {
        "actor": actor,
        "reference": reference,
        "revision": r.revision,
        "fingerprint": fingerprint,
    }
    p.status = "approved"
    p.approved_by = actor
    p.approved_at = clock()
    audit(
        db,
        actor,
        "slack_native_delivery_released",
        plan_id=p.id,
        delivery_id=d.id,
        channel=channel,
    )
    return d


def validate(db, d, now):
    r = ready(db)
    p = db.get(Plan, d.plan_id)
    s = db.get(Source, p.id)
    a = d.slack_authorization or {}
    actor = db.get(User, a.get("actor")) if a.get("actor") else None
    if (
        not actor
        or not actor.is_active
        or actor.role != "admin"
        or p.status not in {"approved", "complete"}
        or a.get("revision") != r.revision
        or a.get("fingerprint") != p.fingerprint
        or not s
    ):
        raise ValueError("Slack approval changed")
    fields = sources.snapshot(db, s.kind, s.identifier, now)
    if (
        digest(fields) != d.slack_context["source_fingerprint"]
        or route(r.config, fields) != d.recipient_key
    ):
        raise ValueError("Slack event/channel changed; review again")
    return r, fields


def recover(db, now):
    for a in db.scalars(
        select(Attempt)
        .where(
            Attempt.slack_mode.is_not(None),
            Attempt.acceptance == "pending",
            Attempt.lease_expires_at <= now,
        )
        .with_for_update(skip_locked=True)
        .limit(100)
    ):
        a.acceptance = "unknown"
        a.outcome = "reconciliation_required"
        a.updated_at = now
        d = db.get(Delivery, a.delivery_id)
        d.state = "reconciliation_required"
        d.last_error = "slack_worker_interrupted"
        audit(db, None, "slack_claim_quarantined", delivery_id=d.id)


def reserve(db, now):
    lock(db)
    recover(db, now)
    from app.notifications.controls import control_status

    c = control_status(db)
    if c["paused"] or c["emergency_disabled"]:
        return None
    r = db.get(Integration, 1)
    if r and r.backoff_until and utc(r.backoff_until) > now:
        return None
    rows = db.scalars(
        select(Delivery)
        .where(
            Delivery.channel == "slack",
            Delivery.slack_authorization.is_not(None),
            Delivery.state.in_(["queued", "retry"]),
            Delivery.next_attempt_at <= now,
        )
        .order_by(Delivery.id)
        .limit(20)
        .with_for_update(skip_locked=True)
    )
    for d in rows:
        try:
            r, fields = validate(db, d, now)
            if db.scalar(
                select(Attempt.id)
                .where(
                    Attempt.delivery_id == d.id,
                    Attempt.slack_mode.is_not(None),
                    or_(
                        Attempt.acceptance.is_(None),
                        Attempt.acceptance != "not_accepted",
                    ),
                )
                .limit(1)
            ):
                d.state = "reconciliation_required"
                continue
            if d.attempts >= policy(db).max_attempts:
                d.state = "dead_letter"
                continue
            key = digest(
                [r.config["workspace_id"], d.recipient_key, d.slack_context["topic"]]
            )
            t = db.get(Thread, key)
            if t and t.pending_delivery_id and t.pending_delivery_id != d.id:
                d.next_attempt_at = now + timedelta(seconds=60)
                d.last_error = "slack_thread_root_unresolved"
                continue
            recent = list(
                db.scalars(
                    select(Attempt)
                    .where(
                        Attempt.slack_mode.is_not(None),
                        Attempt.created_at >= now - timedelta(hours=1),
                    )
                    .order_by(Attempt.created_at)
                    .limit(2001)
                )
            )
            if (
                len(recent) >= r.config["max_per_hour"]
                or sum(utc(x.created_at) >= now - timedelta(minutes=1) for x in recent)
                >= r.config["max_per_minute"]
            ):
                d.next_attempt_at = now + timedelta(minutes=1)
                continue
            channel_latest = next(
                (x for x in reversed(recent) if x.slack_channel_id == d.recipient_key),
                None,
            )
            if channel_latest and utc(channel_latest.created_at) > now - timedelta(
                seconds=1
            ):
                d.next_attempt_at = now + timedelta(seconds=1)
                continue
            prior = list(
                db.execute(
                    select(Delivery, Attempt.created_at)
                    .join(Attempt, Attempt.delivery_id == Delivery.id)
                    .where(
                        Delivery.channel == "slack",
                        Delivery.id != d.id,
                        Delivery.slack_authorization.is_not(None),
                        Delivery.state.in_(
                            ["submitted", "accepted", "reconciliation_required"]
                        ),
                        Attempt.created_at
                        >= now - timedelta(seconds=r.config["cooldown_seconds"]),
                    )
                    .order_by(Attempt.created_at.desc())
                    .limit(2001)
                )
            )
            for old, last_sent_at in prior:
                ctx = old.slack_context or {}
                if (
                    ctx.get("topic") == d.slack_context["topic"]
                    and old.recipient_key == d.recipient_key
                ):
                    previous = ctx.get("fields", {})
                    escalation = RANK[fields["severity"]] > RANK.get(
                        previous.get("severity"), -1
                    )
                    transition = (
                        fields["event"] in {"recovery", "incident_closed"}
                        and previous.get("event") != fields["event"]
                    )
                    assignment = fields.get("technician_id") != previous.get(
                        "technician_id"
                    )
                    if not escalation and not transition and not assignment:
                        d.next_attempt_at = utc(last_sent_at) + timedelta(
                            seconds=r.config["cooldown_seconds"]
                        )
                        break
            else:
                if not t:
                    t = Thread(
                        key=key,
                        workspace=r.config["workspace_id"],
                        channel=d.recipient_key,
                        topic=d.slack_context["topic"],
                    )
                    db.add(t)
                if not t.timestamp and r.config["mode"] == "bot":
                    t.pending_delivery_id = d.id
                a = Attempt(
                    delivery_id=d.id,
                    attempt=d.attempts + 1,
                    outcome="submitted",
                    slack_mode=r.config["mode"],
                    slack_workspace_id=r.config["workspace_id"],
                    slack_channel_id=d.recipient_key,
                    slack_thread_ts=t.timestamp,
                    slack_revision=r.revision,
                    acceptance="pending",
                    claim_token=str(uuid4()),
                    lease_expires_at=now + timedelta(minutes=2),
                    created_at=now,
                    updated_at=now,
                )
                d.state = "submitted"
                d.attempts += 1
                db.add(a)
                db.flush()
                audit(
                    db,
                    None,
                    "slack_claim_committed_before_io",
                    delivery_id=d.id,
                    attempt_id=a.id,
                )
                return a.id
        except (ValueError, LookupError, OSError):
            d.state = "suppressed"
            d.last_error = "slack_approval_or_source_changed"
    return None


def finish(db, identifier, *, receipt=None, error=None, now=None):
    now = now or clock()
    lock(db)
    a = db.scalar(select(Attempt).where(Attempt.id == identifier).with_for_update())
    d = db.get(Delivery, a.delivery_id)
    if a.acceptance != "pending":
        return
    r = db.get(Integration, 1)
    key = digest([a.slack_workspace_id, d.recipient_key, d.slack_context["topic"]])
    t = db.get(Thread, key)
    a.updated_at = now
    if error:
        a.acceptance = "not_accepted" if error.rejected else "unknown"
        a.outcome = error.code
        d.last_error = error.code
        if error.rejected:
            if t and t.pending_delivery_id == d.id:
                t.pending_delivery_id = None
            d.state = (
                "retry"
                if error.code == "rate_limited" and d.attempts < policy(db).max_attempts
                else "dead_letter"
            )
            d.next_attempt_at = now + timedelta(seconds=error.retry_after)
            if error.code == "rate_limited":
                r.backoff_until = d.next_attempt_at
        else:
            d.state = "reconciliation_required"
        r.health = error.code
    else:
        a.acceptance = "accepted"
        a.outcome = "accepted"
        a.slack_ts = receipt.timestamp
        a.raw_status = receipt.raw_status
        d.state = "accepted"
        d.provider_receipt = receipt.timestamp
        d.provider_status = receipt.raw_status
        d.provider_status_at = now
        d.completed_at = now
        if receipt.timestamp and t and not t.timestamp:
            t.timestamp = receipt.timestamp
            t.pending_delivery_id = None
        r.last_submission_at = now
        r.health = "submission_verified"
    audit(
        db,
        None,
        "slack_attempt_completed",
        delivery_id=d.id,
        attempt_id=a.id,
        state=d.state,
        acceptance=a.acceptance,
    )
    db.flush()


def run_batch(factory, *, transport=None, now=None, limit=20):
    host_authorized()
    if not 1 <= limit <= 100:
        raise ValueError("Bounded Slack batch required")
    count = 0
    for _ in range(limit):
        with factory() as db:
            identifier = reserve(db, now or clock())
            db.commit()
        if identifier is None:
            break
        try:
            with factory() as db:
                a = db.get(Attempt, identifier)
                d = db.get(Delivery, a.delivery_id)
                if a.acceptance != "pending" or d.state != "submitted":
                    continue
                r, fields = validate(db, d, now or clock())
                if a.slack_revision != r.revision:
                    raise ValueError("Slack configuration changed")
                provider = NativeSlack(
                    dict(r.config),
                    credentials(db),
                    transport=transport,
                    authorized=True,
                )
                body = sources.payload(
                    fields, r.config, delivery_id=d.id, thread_ts=a.slack_thread_ts
                )
                destination = d.recipient_key
                key = a.claim_token
                db.commit()
            result = provider.send(key=key, destination=destination, payload=body)
            with factory() as db:
                finish(db, identifier, receipt=result, now=now)
                db.commit()
        except SlackFailure as e:
            with factory() as db:
                finish(db, identifier, error=e, now=now)
                db.commit()
        except (ValueError, OSError):
            # Only local validation before submit may prove no acceptance. Treat
            # unclassified post-call failures conservatively as unknown.
            with factory() as db:
                finish(db, identifier, error=SlackFailure(), now=now)
                db.commit()
        count += 1
    return {"processed": count, "provider": "slack", "no_exactly_once_guarantee": True}


def reconcile(db, attempt_id, timestamp, actor, reference, *, transport=None):
    host_authorized()
    lock(db)
    a = db.get(Attempt, attempt_id)
    if not a or not a.slack_mode:
        raise LookupError()
    if a.slack_mode != "bot" or a.acceptance not in {"accepted", "unknown"}:
        raise ValueError("Bot acceptance proof or expired quarantined claim required")
    d = db.get(Delivery, a.delivery_id)
    r = db.get(Integration, 1)
    if a.slack_workspace_id != r.config["workspace_id"]:
        raise ValueError("Restore original workspace configuration for reconciliation")
    if a.slack_ts and a.slack_ts != timestamp:
        raise ValueError("Receipt mismatch")
    from datetime import datetime, timezone

    at = datetime.fromtimestamp(float(timestamp), timezone.utc)
    if not a.slack_ts and not utc(a.created_at) - timedelta(seconds=30) <= at <= utc(
        a.created_at
    ) + timedelta(minutes=2):
        raise ValueError("Receipt outside attempt creation window")
    provider = NativeSlack(
        r.config, credentials(db), transport=transport, authorized=True
    )
    expected = sources.payload(
        d.slack_context["fields"],
        r.config,
        delivery_id=d.id,
        thread_ts=a.slack_thread_ts,
    )
    # Caller commits/releases the validation transaction before any provider GET.
    return a, d, r, provider, expected


def accept_reconciliation(db, attempt_id, receipt, actor, reference):
    lock(db)
    a = db.scalar(select(Attempt).where(Attempt.id == attempt_id).with_for_update())
    d = db.get(Delivery, a.delivery_id)
    if a.acceptance not in {"accepted", "unknown"} or (
        a.slack_ts and a.slack_ts != receipt.timestamp
    ):
        raise ValueError("Attempt changed during reconciliation")
    collision = db.scalar(
        select(Attempt.id)
        .where(
            Attempt.slack_workspace_id == a.slack_workspace_id,
            Attempt.slack_channel_id == a.slack_channel_id,
            Attempt.slack_ts == receipt.timestamp,
            Attempt.id != a.id,
        )
        .limit(1)
    )
    if collision:
        raise ValueError("Receipt already belongs to another attempt")
    a.acceptance = "accepted"
    a.outcome = "accepted"
    a.slack_ts = receipt.timestamp
    a.raw_status = receipt.raw_status
    a.updated_at = clock()
    d.state = "accepted"
    d.provider_receipt = receipt.timestamp
    d.provider_status = receipt.raw_status
    d.completed_at = clock()
    key = digest([a.slack_workspace_id, a.slack_channel_id, d.slack_context["topic"]])
    t = db.get(Thread, key)
    if t and not a.slack_thread_ts and not t.timestamp:
        t.timestamp = receipt.timestamp
        t.pending_delivery_id = None
    audit(
        db,
        actor,
        "slack_reconciliation_verified",
        delivery_id=d.id,
        attempt_id=a.id,
        reference=reference,
    )


def process_interactions(factory, limit=50):
    from sqlalchemy import text
    from app.models.entities import Incident, IncidentNote
    from app.notifications.slack_models import SlackUserMap

    count = 0
    with factory() as db:
        lock(db)
        r = db.get(Integration, 1)
        if (
            not r
            or not r.live_authorized
            or not r.config.get("enabled")
            or not r.config.get("interactions_enabled")
        ):
            return {"processed": 0}
        require_processing(db)
        rows = list(
            db.scalars(
                select(Interaction)
                .where(Interaction.state == "pending")
                .order_by(Interaction.received_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for event in rows:
            allowed = (
                event.workspace == r.config["workspace_id"]
                and event.channel in r.config["approved_channels"]
                and event.channel in r.config["test_channels"]
                and db.scalar(
                    text("SELECT public.noc_slack_interaction_allowed(:w,:u,:c,:d)"),
                    {
                        "w": event.workspace,
                        "u": event.slack_user_id,
                        "c": event.channel,
                        "d": event.delivery_id,
                    },
                )
            )
            d = db.get(Delivery, event.delivery_id)
            if not allowed or d.provider_receipt != event.message_ts:
                event.state = "denied"
                continue
            mapping = db.get(SlackUserMap, event.slack_user_id)
            src = db.get(Source, d.plan_id)
            incident = db.scalar(
                select(Incident).where(Incident.id == src.incident_id).with_for_update()
            )
            if not incident or incident.status in {"closed", "resolved"}:
                event.state = "stale"
                continue
            if event.action == "noc_ack":
                if not incident.acknowledged_at:
                    incident.acknowledged_at = clock()
                    if incident.status == "open":
                        incident.status = "acknowledged"
                    incident.revision += 1
                note = "Acknowledged through verified Slack interaction."
            elif event.action == "noc_review":
                note = "Technician review requested through verified Slack interaction. No dispatch or network change authorized."
                incident.revision += 1
            else:
                event.state = "processed"
                continue
            db.add(
                IncidentNote(
                    incident_id=incident.id, user_id=mapping.user_id, body=note
                )
            )
            audit(
                db,
                mapping.user_id,
                "slack_incident_interaction",
                incident_id=incident.id,
                interaction_action=event.action,
                event_key=event.key,
            )
            event.state = "processed"
            count += 1
        db.commit()
    return {"processed": count, "network_changes": 0, "customer_sms_approvals": 0}


def discover(db, limit=20):
    """Optional bounded draft preparation only; never authorizes a delivery."""
    from app.models.entities import Incident, MonitoringAlert, MonitoringWorker
    from app.tower_health.models import TowerFinding
    from app.field.models import WorkOrder
    from app.maintenance_ops.models import Schedule
    from app.signup.models import Application

    lock(db)
    if not db.get(Integration, 1):
        return 0
    r = db.get(Integration, 1)
    if (
        db.scalar(
            select(func.count())
            .select_from(Source)
            .join(Plan, Plan.id == Source.plan_id)
            .where(Plan.created_at >= clock() - timedelta(hours=1))
        )
        >= 200
    ):
        return 0
    actor = db.scalar(
        select(User)
        .where(User.is_active.is_(True), User.role == "admin")
        .order_by(User.id)
        .limit(1)
    )
    if not actor:
        return 0
    created = 0
    for kind, model in [
        ("incident", Incident),
        ("monitoring", MonitoringAlert),
        ("tower", TowerFinding),
        ("work_order", WorkOrder),
        ("maintenance", Schedule),
        ("signup", Application),
        ("worker", MonitoringWorker),
    ]:
        for identifier in db.scalars(
            select(model.id).order_by(model.id.desc()).limit(limit)
        ):
            if created >= limit:
                return created
            try:
                fields = sources.snapshot(db, kind, str(identifier))
                if RANK[fields["severity"]] < RANK[r.config["minimum_severity"]]:
                    continue
                before = db.scalar(select(func.count()).select_from(Source))
                prepare(db, kind, str(identifier), actor.id)
                if db.scalar(select(func.count()).select_from(Source)) > before:
                    created += 1
            except (ValueError, LookupError):
                continue
    return created
