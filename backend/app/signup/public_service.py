import json, re, unicodedata, base64, hmac, time, secrets, ipaddress
from uuid import uuid4
from sqlalchemy import select, func, text
from sqlalchemy.dialects.postgresql import insert
from fastapi import HTTPException
from app.core.config import settings
from . import crypto, providers
from .models import Plan, Application, PublicBudget, CaptchaUse, Event
from app.field.models import now
from app.equipment.service import view


def normalized_address(address):
    parts = []
    for k in ["street", "unit", "city", "state", "postal_code", "country"]:
        v = unicodedata.normalize("NFKC", str(address.get(k, ""))).upper()
        v = re.sub(r"[^\w\s-]", " ", v)
        v = re.sub(
            r"\b(STREET|ROAD|AVENUE|APARTMENT|SUITE)\b",
            lambda m: {
                "STREET": "ST",
                "ROAD": "RD",
                "AVENUE": "AVE",
                "APARTMENT": "UNIT",
                "SUITE": "UNIT",
            }[m[0]],
            v,
        )
        parts.append(" ".join(v.split()))
    return "|".join(parts)


def public_plan(p):
    return view(p, exclude={"powercode_plan_ref", "revision"})


def enabled():
    if not settings.signup_public_enabled:
        raise HTTPException(
            503, "Service applications are not configured; contact the office"
        )


def csrf_issue():
    stamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    value = stamp + "." + nonce
    return value + "." + crypto.digest("csrf", value)


def csrf_check(origin, cookie, header):
    enabled()
    if (
        origin != settings.signup_public_origin
        or not cookie
        or not header
        or len(header) > 200
        or not hmac.compare_digest(cookie, header)
    ):
        raise HTTPException(403, "Signup security check failed; reload the form")
    try:
        stamp, nonce, signature = header.split(".")
        if not 0 <= time.time() - int(stamp) <= 900 or not hmac.compare_digest(
            signature, crypto.digest("csrf", stamp + "." + nonce)
        ):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(
            403, "Signup security check failed; reload the form"
        ) from None


def client_ip(request):
    peer = request.client.host if request.client else "unknown"
    try:
        trusted = [
            ipaddress.ip_network(v.strip())
            for v in settings.signup_trusted_proxy_cidrs.split(",")
            if v.strip()
        ]
        if any(ipaddress.ip_address(peer) in n for n in trusted):
            supplied = request.headers.get("x-forwarded-for", "")
            if "," not in supplied:
                peer = str(ipaddress.ip_address(supplied.strip()))
    except ValueError:
        pass
    return peer


def budget(db, request, kind):
    instant = now()
    window = instant.replace(
        minute=(instant.minute // 15) * 15, second=0, microsecond=0
    )
    keys = [
        (
            crypto.digest("rate:" + kind, client_ip(request)),
            {"csrf": 30, "submit": 8, "lookup": 15}[kind],
        ),
        (crypto.digest("rate:global", kind), 2000),
    ]
    try:
        for key, limit in keys:
            stmt = insert(PublicBudget).values(key=key, window=window, count=1)
            count = db.scalar(
                stmt.on_conflict_do_update(
                    index_elements=[PublicBudget.key, PublicBudget.window],
                    set_={"count": func.least(PublicBudget.count + 1, limit + 1)},
                ).returning(PublicBudget.count)
            )
            if count > limit:
                db.commit()
                raise HTTPException(
                    429, "Too many requests; retry later or contact the office"
                )
        db.commit()
    except Exception:
        db.rollback()
        raise


def lookup_token(key):
    return crypto.digest("lookup", str(key))


def receipt(a):
    return {
        "received": True,
        "reference": a.reference,
        "lookup_token": lookup_token(a.submission_key),
        "message": "Request received for review. Availability and pricing are not guaranteed.",
    }


def submit(db, b, verify=providers.turnstile):
    payload = b.model_dump(mode="json", exclude={"turnstile_token", "website"})
    signature = crypto.digest(
        "submission", json.dumps(payload, sort_keys=True, separators=(",", ":"))
    )
    db.execute(text("SET LOCAL statement_timeout='5000ms'"))
    db.execute(text("SELECT pg_advisory_xact_lock(1000103)"))
    old = db.execute(
        select(
            Application.reference, Application.submission_key, Application.digest
        ).where(Application.submission_key == str(b.submission_key))
    ).first()
    if old:
        if old.digest != signature:
            raise ValueError("Submission conflict")
        return receipt(old)
    if b.website:
        return {
            "received": True,
            "reference": "APP-" + str(uuid4()),
            "lookup_token": secrets.token_hex(32),
            "message": "Request received for review. Availability and pricing are not guaranteed.",
        }
    if not verify(b.turnstile_token, b.submission_key):
        raise PermissionError("Security verification unavailable")
    token_hash = crypto.digest("captcha", b.turnstile_token)
    used = db.get(CaptchaUse, token_hash)
    if used and used.submission_key != str(b.submission_key):
        raise PermissionError("Verification token already used")
    plan = db.get(Plan, b.plan_id)
    if (
        not plan
        or not plan.active
        or not plan.public
        or plan.technology != b.technology
        or plan.audience not in {"both", b.audience}
    ):
        raise ValueError("Plan unavailable")
    a = Application(
        reference="APP-" + str(uuid4()),
        submission_key=str(b.submission_key),
        digest=signature,
        lookup_hash=crypto.digest("lookup-hash", lookup_token(b.submission_key)),
        email_hash=crypto.digest("email", str(b.email).lower()),
        phone_hash=crypto.digest("phone", b.phone),
        address_hash=crypto.digest(
            "address", normalized_address(payload["service_address"])
        ),
        audience=b.audience,
        request_kind=b.request_kind,
        technology=b.technology,
        plan_id=plan.id,
        plan_snapshot=public_plan(plan),
    )
    crypto.initialize(
        a,
        payload
        | {"consent_recorded_at": now().isoformat(), "consent_version": "signup-v1"},
    )
    db.add(a)
    db.flush()
    db.add(
        Event(
            application_id=a.id,
            action="submitted",
            data={"origin": "public_signup", "external_delivery": False},
        )
    )
    db.add(CaptchaUse(digest=token_hash, submission_key=str(b.submission_key)))
    db.flush()
    return receipt(a)


def lookup(db, b, verify=providers.turnstile):
    # The lookup token itself is high-entropy; captcha is still mandatory.
    if settings.signup_turnstile_mode == "fixture":
        valid = (
            providers.isolated()
            and settings.signup_public_test_mode
            and b.turnstile_token == "synthetic-lookup"
        )
    else:
        valid = verify(b.turnstile_token, uuid4())
    if not valid:
        raise PermissionError("Verification failed")
    a = db.execute(
        select(Application.state, Application.redacted_at).where(
            Application.lookup_hash == crypto.digest("lookup-hash", b.token)
        )
    ).first()
    if not a or a.redacted_at:
        return {
            "status": "not_available",
            "message": "Use your private receipt or contact the office.",
        }
    states = {
        "submitted": "received",
        "under_review": "reviewing",
        "qualification_pending": "reviewing",
        "site_survey_required": "survey_review",
        "survey_scheduled": "survey_review",
        "qualified": "reviewing",
        "quote_pending": "quote_review",
        "quote_sent": "quote_review",
        "customer_accepted": "installation_review",
        "installation_pending": "installation_review",
        "installation_scheduled": "installation_review",
        "installed": "installation_review",
        "activation_pending": "installation_review",
        "completed": "completed",
        "rejected": "closed",
        "cancelled": "closed",
    }
    return {
        "status": states[a.state],
        "message": "Contact the office using your private receipt for details. No service or construction date is guaranteed.",
    }
