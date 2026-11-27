"""Create an explicitly approved synthetic quote for actual public response UX tests."""

import os, json
from uuid import uuid4
from datetime import timedelta
from sqlalchemy import select
from app.core.config import settings

if (
    os.environ.get("WORDPRESS_ACCEPTANCE") != "1"
    or not settings.offline_mode
    or settings.app_env != "staging"
):
    raise RuntimeError("Disposable synthetic fixture only")
settings.signup_pii_key_file = "/tmp/noc129-pii.key"
from app.core.database import SessionLocal
from app.signup import service as s
from app.signup.acquisition import prepare_quote_access, quote_token
from app.signup.models import Application
from app.signup.contracts import QuoteInput, Control, QuoteAccessInput
from app.models.entities import User
from app.field.models import now

with SessionLocal() as db:
    a = db.scalar(
        select(Application).where(
            Application.reference == os.environ["NOC129_REFERENCE"]
        )
    )
    if not a or not s.pii(a)["email"].endswith("@example.invalid"):
        raise ValueError("Synthetic applicant required")
    u = User(
        username="fixture-" + uuid4().hex,
        email=uuid4().hex + "@example.invalid",
        password_hash="disabled-synthetic-login",
        role="admin",
        is_active=True,
    )
    db.add(u)
    db.flush()
    a.state = "qualified"
    q = s.quote_draft(
        db,
        u,
        a,
        QuoteInput(
            operation_key=uuid4(),
            reason="Synthetic approved quote fixture",
            expected_revision=a.revision,
            plan_id=a.plan_id,
            currency="USD",
            monthly="65.00",
            installation="100.00",
            equipment="30.00",
            construction="0.00",
            discount="10.00",
            expires_at=now() + timedelta(days=2),
        ),
    )
    for action in ["approve_quote", "issue_quote"]:
        s.control(
            db,
            u,
            a,
            Control(
                operation_key=uuid4(),
                reason="Synthetic explicit administrator review",
                expected_revision=a.revision,
                quote_id=q["id"],
                action=action,
                evidence="Synthetic quote reviewed; no real customer message",
            ),
        )
    prepare_quote_access(
        db,
        u,
        a,
        QuoteAccessInput(
            operation_key=uuid4(),
            reason="Synthetic private quote access",
            expected_revision=a.revision,
            quote_id=q["id"],
        ),
    )
    token = quote_token(db, u, a.id, q["id"])["token"]
    db.commit()
    print(json.dumps({"token": token}))
