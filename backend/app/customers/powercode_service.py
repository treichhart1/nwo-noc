"""Reviewed NOC contracts; no live Powercode transport or billing operations."""

import hashlib
import json
from uuid import uuid4
from datetime import timedelta, timezone
from decimal import Decimal
from sqlalchemy import select, text, func
from app.models.entities import AuditLog
from app.customers.models import CustomerAccount, CustomerService
from app.customers.powercode_models import (
    PowercodeConnection,
    PowercodePackage,
    PowercodePackageMapping,
    PowercodeProvision,
    PowercodeAttempt,
    PowercodeProvisionEvent,
)
from app.customers.powercode_contracts import NATIVE_BLOCKER
from app.notifications.models import NotificationCredential
from app.notifications.secrets import seal
from app.core.config import settings
from app.signup import service as signup, crypto
from app.signup.models import (
    Application,
    Plan,
    Quote,
    Qualification,
    AppWork,
    ProvisionDraft,
    QuoteResponse,
)
from app.field.models import WorkOrder, now


def admin(user):
    if user.role != "admin":
        raise PermissionError("Administrator required")


def lock(db):
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(1300001)")):
        raise ValueError("Powercode operation already in progress; refresh safely")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def audit(db, user, action, ident="powercode"):
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            action="powercode." + action,
            entity_type="powercode_integration",
            entity_id=str(ident),
            details={"native_enabled": False},
        )
    )


def config_view(db):
    c = db.get(PowercodeConnection, 1)
    return {
        "endpoint": c.endpoint if c else None,
        "allowed_origin": c.allowed_origin if c else None,
        "auth_method": c.auth_method if c else "unverified",
        "revision": c.revision if c else 0,
        "emergency_disabled": c.emergency_disabled if c else True,
        "native_enabled": False,
        "credential_configured": db.get(NotificationCredential, "powercode")
        is not None,
        "connection_status": "blocked_contract_verification",
        "blocker": NATIVE_BLOCKER,
        "tls_verification": True,
        "redirects": False,
        "timeout_seconds": 5,
        "connect_seconds": 3,
        "automatic_write_retries": 0,
        "last_successful_native_connection": None,
    }


def configure(db, user, b):
    admin(user)
    lock(db)
    c = db.get(PowercodeConnection, 1)
    if (c.revision if c else 0) != b.expected_revision:
        raise ValueError("Connection changed; refresh")
    if c is None:
        c = PowercodeConnection(id=1, updated_by=user.id)
        db.add(c)
    for k in ("endpoint", "allowed_origin", "auth_method", "emergency_disabled"):
        setattr(c, k, getattr(b, k))
    c.revision = (c.revision or 0) + 1
    c.updated_by = user.id
    c.updated_at = now()
    db.flush()
    audit(db, user, "configuration_updated")
    return config_view(db)


def credential(db, user, b):
    admin(user)
    lock(db)
    try:
        value = seal(settings.notifications_vault_key_file, b.value.get_secret_value())
    except (ValueError, OSError):
        raise ValueError("Owned private vault encryption key required") from None
    c = db.get(NotificationCredential, "powercode")
    if c:
        c.ciphertext = value
    else:
        db.add(NotificationCredential(provider="powercode", ciphertext=value))
    audit(db, user, "credential_stored")
    return {"encrypted": True, "native_enabled": False}


def package_view(p):
    result = {
        k: getattr(p, k)
        for k in (
            "external_id",
            "name",
            "technology",
            "audience",
            "available",
            "currency",
            "monthly",
            "installation",
            "download_mbps",
            "upload_mbps",
            "source_updated_at",
        )
    }
    for k in ("monthly", "installation", "download_mbps", "upload_mbps"):
        if result[k] is not None:
            result[k] = format(Decimal(str(result[k])).quantize(Decimal("0.01")), "f")
    if result["source_updated_at"] is not None:
        result["source_updated_at"] = (
            result["source_updated_at"].astimezone(timezone.utc).isoformat()
        )
    return result


def package_digest(p):
    return digest(package_view(p))


def catalog_sync(db, batch):
    from app.customers.providers import ProviderFailure
    from app.customers.sync import unique, utc

    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(1300001)")):
        raise ProviderFailure("overlap_skipped")
    for key, record in unique(batch.packages).items():
        p = db.get(PowercodePackage, key)
        values = record.model_dump(exclude={"updated_at"})
        if p:
            if record.updated_at < utc(p.source_updated_at):
                raise ProviderFailure("stale_snapshot")
            if record.updated_at == utc(p.source_updated_at) and any(
                getattr(p, k) != v for k, v in values.items()
            ):
                raise ProviderFailure("conflicting_duplicate")
        else:
            p = PowercodePackage(external_id=key)
            db.add(p)
        for k, v in values.items():
            setattr(p, k, v)
        p.source_updated_at = record.updated_at
        p.last_synced_at = now()
    # Absent catalog entries are retained; availability requires explicit evidence.
    db.flush()


def map_package(db, user, b):
    admin(user)
    lock(db)
    p = db.get(PowercodePackage, b.package_id)
    plan = db.get(Plan, b.plan_id)
    if not p or not plan or not p.available or not b.reviewed_differences:
        raise ValueError(
            "Available package, existing plan and explicit review required"
        )
    if (
        plan.revision != b.expected_plan_revision
        or package_digest(p) != b.package_digest
    ):
        raise ValueError("Package or plan changed; review current terms")
    if p.technology != plan.technology or p.audience not in ("both", plan.audience):
        raise ValueError("Package technology/audience does not match plan")
    plan.powercode_plan_ref = p.external_id
    plan.revision += 1
    m = db.get(PowercodePackageMapping, plan.id)
    if not m:
        m = PowercodePackageMapping(plan_id=plan.id)
        db.add(m)
    m.package_id = p.external_id
    m.package_digest = package_digest(p)
    m.plan_revision = plan.revision
    m.approved_by = user.id
    m.approved_at = now()
    audit(db, user, "package_mapping_reviewed", plan.id)
    db.flush()
    return {
        "plan_id": plan.id,
        "plan_revision": plan.revision,
        "package_id": p.external_id,
        "signup_publication_changed": False,
    }


def reviewed_mapping(db, plan):
    m = db.get(PowercodePackageMapping, plan.id)
    p = db.get(PowercodePackage, m.package_id) if m else None
    if (
        not m
        or not p
        or not p.available
        or m.plan_revision != plan.revision
        or m.package_digest != package_digest(p)
        or plan.powercode_plan_ref != p.external_id
    ):
        raise ValueError("Current administrator-reviewed package mapping required")
    return m, p


def snapshot(db, a):
    if (
        not a
        or a.redacted_at
        or a.state not in ("installed", "activation_pending", "completed")
    ):
        raise ValueError("Installed, non-redacted application required")
    plan = db.get(Plan, a.plan_id)
    m, p = reviewed_mapping(db, plan)
    q = db.scalar(
        select(Quote)
        .where(Quote.application_id == a.id)
        .order_by(Quote.number.desc())
        .limit(1)
    )
    response = (
        db.scalar(
            select(QuoteResponse).where(
                QuoteResponse.quote_id == q.id, QuoteResponse.decision == "accepted"
            )
        )
        if q
        else None
    )
    if (
        not q
        or q.status != "accepted"
        or not q.approved_by
        or q.plan_id != plan.id
        or not (q.acceptance_ciphertext or response)
    ):
        # Public append-only responses and legacy encrypted acceptance are distinct evidence.
        raise ValueError("Latest approved accepted quote required")
    quals = db.scalar(
        select(Qualification.id).where(Qualification.application_id == a.id).limit(1)
    )
    if not quals:
        raise ValueError("Documented qualification required")
    w = db.scalar(
        select(WorkOrder)
        .join(AppWork, AppWork.work_order_id == WorkOrder.id)
        .where(AppWork.application_id == a.id, AppWork.kind == "installation")
    )
    if not w or w.status != "completed" or not w.completed_at:
        raise ValueError("Completed canonical installation work order required")
    if a.proposed_site_id is not None and w.site_id != a.proposed_site_id:
        raise ValueError("Installation site differs from reviewed application")
    if not db.scalar(
        select(ProvisionDraft.id).where(ProvisionDraft.application_id == a.id)
    ):
        raise ValueError("Existing approved signup provisioning preparation required")
    matches = signup.matches(db, a)
    if matches.get("truncated") or (
        matches["customers"]
        and a.matched_customer_id not in {r["id"] for r in matches["customers"]}
    ):
        raise ValueError(
            "Duplicate customer match must be resolved before provisioning"
        )
    if any(
        r["state"] not in ("cancelled", "rejected") for r in matches["applications"]
    ):
        raise ValueError(
            "Potential duplicate application must be resolved before provisioning"
        )
    existing = (
        db.get(CustomerAccount, a.matched_customer_id)
        if a.matched_customer_id
        else None
    )
    if existing and existing.source_deleted_at:
        raise ValueError("Matched customer is no longer current")
    pii = signup.pii(a)
    if (
        not pii.get("customer_name")
        or not pii.get("service_address")
        or not (pii.get("email") or pii.get("phone"))
    ):
        raise ValueError("Verified identity, address and contact information required")
    return {
        "application_id": a.id,
        "application_revision": a.revision,
        "plan_id": plan.id,
        "plan_revision": plan.revision,
        "package_id": p.external_id,
        "package_digest": m.package_digest,
        "quote_id": q.id,
        "quote": q.snapshot,
        "work_order_id": w.id,
        "work_order_revision": w.revision,
        "installation_site_id": w.site_id,
        "installed_asset_id": w.asset_id,
        "matched_customer_id": a.matched_customer_id,
        "existing_external_account_id": existing.external_id if existing else None,
        "applicant": {
            k: pii.get(k)
            for k in (
                "customer_name",
                "business_name",
                "service_address",
                "billing_address",
                "email",
                "phone",
            )
        },
        "duplicate_review": matches,
        "operations": [
            "reuse_account" if existing else "create_account",
            "assign_service",
        ],
        "activation_authorized": False,
        "billing_changes_authorized": False,
        "contract": "noc-provision-preview-v1",
    }


def event(db, row, user, action, **facts):
    row.updated_at = now()
    row.revision = (row.revision or 0) + 1
    db.add(
        PowercodeProvisionEvent(
            provision_id=row.id,
            actor_id=user.id if user else None,
            action=action,
            status=row.status,
            evidence=facts,
        )
    )
    audit(db, user, action, row.id)


def prepare(db, user, b):
    admin(user)
    lock(db)
    if not all(
        getattr(b, k)
        for k in (
            "identity_verified",
            "address_reviewed",
            "qualification_reviewed",
            "duplicate_reviewed",
        )
    ):
        raise ValueError("All verified prerequisites require explicit review")
    a = db.scalar(
        select(Application).where(Application.id == b.application_id).with_for_update()
    )
    if not a or a.revision != b.expected_application_revision:
        raise ValueError("Application changed; refresh")
    v = snapshot(db, a)
    d = digest(v)
    row = db.scalar(
        select(PowercodeProvision)
        .where(PowercodeProvision.application_id == a.id)
        .with_for_update()
    )
    if row:
        if row.preview_digest != d:
            raise ValueError(
                "Existing provisioning record differs; reconciliation review required"
            )
        return provision_view(row)
    row = PowercodeProvision(
        application_id=a.id,
        status="pending_review",
        preview_digest=d,
        preview_ciphertext=crypto.seal(a, v),
        application_revision=a.revision,
        quote_id=v["quote_id"],
        mapping_digest=v["package_digest"],
        attestations=b.model_dump(
            exclude={"application_id", "expected_application_revision"}
        ),
    )
    db.add(row)
    db.flush()
    event(db, row, user, "preview_prepared")
    db.flush()
    return provision_view(row)


def provision_view(r):
    return {
        k: getattr(r, k)
        for k in (
            "id",
            "application_id",
            "status",
            "preview_digest",
            "revision",
            "approved_by",
            "approved_at",
            "external_account_id",
            "external_service_id",
            "activation_approved_by",
            "activation_approved_at",
            "error_code",
            "created_at",
            "updated_at",
        )
    }


def current(db, r):
    a = db.get(Application, r.application_id)
    if digest(snapshot(db, a)) != r.preview_digest:
        raise ValueError("Approval source changed; reconciliation required")
    return a


def decide(db, user, ident, b, action):
    admin(user)
    lock(db)
    r = db.scalar(
        select(PowercodeProvision)
        .where(PowercodeProvision.id == ident)
        .with_for_update()
    )
    if not r:
        raise LookupError()
    if (
        r.revision != b.expected_revision
        or r.preview_digest != b.preview_digest
        or not b.authorize
    ):
        raise ValueError(
            "Current preview fingerprint and explicit authorization required"
        )
    if action == "cancel":
        if (
            r.status not in ("pending_review", "approved", "queued", "failed")
            or r.external_account_id
            or r.external_service_id
        ):
            raise ValueError(
                "Submitted or partially accepted operations cannot be cancelled without reconciliation"
            )
        r.status = "cancelled"
    elif action == "approve":
        if r.status != "pending_review":
            raise ValueError("Pending review required")
        current(db, r)
        r.status = "approved"
        r.approved_by = user.id
        r.approved_at = now()
    elif action == "queue":
        if r.status != "approved":
            raise ValueError("Independent approval required")
        current(db, r)
        r.status = "queued"
    elif action == "approve_activation":
        if (
            r.status != "activation_pending"
            or not r.external_service_id
            or not b.independently_authorized
        ):
            raise ValueError(
                "Verified service assignment and independent activation authorization required"
            )
        current(db, r)
        r.activation_approved_by = user.id
        r.activation_approved_at = now()
        # Evidence only: never invoke billing/activation, even in the mock adapter.
    else:
        raise ValueError("Unsupported operation")
    event(db, r, user, action)
    db.flush()
    return provision_view(r)


def preview(db, user, ident):
    admin(user)
    r = db.get(PowercodeProvision, ident)
    if not r:
        raise LookupError()
    a = current(db, r)
    audit(db, user, "preview_viewed", ident)
    return {
        "record": provision_view(r),
        "preview": crypto.unseal(a, r.preview_ciphertext),
        "native_enabled": False,
        "blocker": NATIVE_BLOCKER,
    }


def simulated_worker(factory, provider):
    """One durable stage per invocation. Explicit injected MockTransport only.

    Submitted intent commits before HTTP; verified IDs commit before next stage.
    A crash/unknown outcome quarantines, never repeats account creation. This is
    not registered with production workers and cannot contact native Powercode.
    """
    from app.customers.powercode_transport import FixtureProvisioner

    if not isinstance(provider, FixtureProvisioner) or not (
        settings.app_env == "staging"
        and settings.offline_mode
        and settings.powercode_fixture_enabled
    ):
        raise ValueError("Explicit offline synthetic provisioning required")
    with factory() as db:
        c = db.get(PowercodeConnection, 1)
        if not c or c.emergency_disabled:
            return {"status": "disabled"}
        r = db.scalar(
            select(PowercodeProvision)
            .where(
                (
                    (PowercodeProvision.status.in_(["queued", "account_created"]))
                    | (
                        (PowercodeProvision.status == "submitted")
                        & (
                            PowercodeProvision.updated_at
                            < now() - timedelta(seconds=30)
                        )
                    )
                )
            )
            .order_by(PowercodeProvision.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not r:
            return {"status": "idle"}
        if r.status == "submitted":
            r.status = "reconciliation_required"
            r.error_code = "interrupted_submission"
            attempts = list(
                db.scalars(
                    select(PowercodeAttempt).where(
                        PowercodeAttempt.provision_id == r.id,
                        PowercodeAttempt.ended_at.is_(None),
                    )
                )
            )
            for old in attempts:
                old.outcome = "ambiguous"
                old.error_code = "interrupted_submission"
                old.ended_at = now()
            event(db, r, None, "restart_quarantined")
            db.commit()
            return {"status": r.status}
        try:
            a = current(db, r)
        except ValueError:
            r.status = "reconciliation_required"
            r.error_code = "source_changed"
            event(db, r, None, "source_changed")
            db.commit()
            return {"status": r.status}
        payload = crypto.unseal(a, r.preview_ciphertext)
        if r.status == "queued" and payload["existing_external_account_id"]:
            r.external_account_id = payload["existing_external_account_id"]
            r.status = "account_created"
            event(db, r, None, "existing_account_reused")
            db.commit()
            return {"status": r.status}
        op = "service" if r.external_account_id else "account"
        attempt = PowercodeAttempt(
            provision_id=r.id,
            operation_key=str(uuid4()),
            operation=op,
            outcome="submitted",
        )
        db.add(attempt)
        r.status = "submitted"
        event(db, r, None, "submission_intent", operation=op)
        db.flush()
        rid, aid, key = r.id, attempt.id, attempt.operation_key
        account = r.external_account_id
        db.commit()
    # Only NOC synthetic fields, never represented as a native API mapping.
    request = {"preview": payload, "external_account_id": account}
    with factory() as control:
        configured = control.get(PowercodeConnection, 1)
        allowed = bool(configured and not configured.emergency_disabled)
    if not allowed:
        from app.customers.powercode_contracts import ProvisionResult

        result = ProvisionResult(outcome="not_accepted", error_code="unavailable")
    else:
        result = provider.submit(op, request, key)
    with factory() as db:
        r = db.scalar(
            select(PowercodeProvision)
            .where(PowercodeProvision.id == rid)
            .with_for_update()
        )
        attempt = db.get(PowercodeAttempt, aid)
        # Another worker may have quarantined an in-flight request after restart.
        # Record verified acceptance, but never advance a quarantined record automatically.
        quarantined = r.status != "submitted"
        if attempt.ended_at is None:
            attempt.outcome = result.outcome
            attempt.external_id = result.external_id
            attempt.error_code = result.error_code
            attempt.ended_at = now()
        else:
            event(
                db,
                r,
                None,
                "late_provider_evidence",
                operation=op,
                outcome=result.outcome,
                external_id=result.external_id,
            )
        if result.outcome == "accepted":
            if op == "account":
                r.external_account_id = result.external_id
            else:
                r.external_service_id = result.external_id
            r.status = (
                "reconciliation_required"
                if quarantined
                else ("account_created" if op == "account" else "activation_pending")
            )
        elif result.outcome == "not_accepted":
            r.status = "reconciliation_required" if quarantined else "failed"
            r.error_code = result.error_code or "not_accepted"
        else:
            r.status = "reconciliation_required"
            r.error_code = "ambiguous_acceptance"
        if op == "service" and result.outcome == "accepted" and not quarantined:
            r.status = "service_assigned"
            event(db, r, None, "service_assignment_verified")
            r.status = "activation_pending"
        event(db, r, None, "provider_result", operation=op, outcome=result.outcome)
        db.commit()
        return {"status": r.status, "provision_id": r.id}
