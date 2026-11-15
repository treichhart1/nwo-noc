"""Rollback-only synthetic encrypted applications: no provider/device/message IO."""

import json, time, resource, tempfile, os
from pathlib import Path
from uuid import uuid4
from cryptography.fernet import Fernet
from sqlalchemy import select, func, event
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Site, User
from .models import Plan, Application
from . import crypto, service as s


def measure(count):
    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Explicit isolated offline synthetic staging required")
    with tempfile.TemporaryDirectory() as root:
        key = Path(root) / "pii.key"
        key.write_bytes(Fernet.generate_key())
        key.chmod(0o600)
        previous = settings.signup_pii_key_file
        settings.signup_pii_key_file = str(key)
        try:
            with SessionLocal() as db:
                if db.scalar(select(Application.id)):
                    raise ValueError("Empty isolated application fixture required")
                user = User(
                    username="signup-scale",
                    email="signup-scale@example.invalid",
                    password_hash="synthetic-unusable",
                    role="admin",
                    is_active=True,
                )
                db.add(user)
                db.flush()
                sites = [
                    Site(
                        name=f"NOC103 Synthetic POP {i}",
                        latitude=41.4 + i * 0.001,
                        longitude=-84.2,
                    )
                    for i in range(40)
                ]
                db.add_all(sites)
                p = Plan(
                    name="Synthetic scale plan",
                    audience="both",
                    technology="wireless",
                    eligibility="Review required",
                    equipment=[],
                )
                db.add(p)
                db.flush()
                begin = time.perf_counter()
                rows = []
                for i in range(count):
                    a = Application(
                        reference=str(uuid4()),
                        submission_key=str(uuid4()),
                        digest="0" * 64,
                        audience="residential",
                        request_kind="new",
                        technology="wireless",
                        plan_id=p.id,
                        plan_snapshot={"name": p.name},
                        proposed_site_id=sites[i % 40].id,
                        state="submitted",
                        email_hash=crypto.digest(
                            "email", f"synthetic{i}@example.invalid"
                        ),
                    )
                    crypto.initialize(
                        a,
                        {
                            "customer_name": "Synthetic Scale Applicant",
                            "email": f"synthetic{i}@example.invalid",
                            "phone": "+15555550103",
                            "service_address": {
                                "street": f"{i} Synthetic Road",
                                "city": "Synthetic Town",
                                "state": "OH",
                                "postal_code": "43500",
                            },
                            "installation_preferences": "Synthetic only",
                        },
                    )
                    rows.append(a)
                db.add_all(rows)
                db.flush()
                insert_ms = (time.perf_counter() - begin) * 1000
                statements = []

                def before(*args):
                    statements.append(1)

                event.listen(db.get_bind(), "before_cursor_execute", before)
                try:
                    begin = time.perf_counter()
                    page = db.scalars(
                        select(Application)
                        .order_by(Application.created_at.desc(), Application.id.desc())
                        .limit(50)
                    ).all()
                    page_ms = (time.perf_counter() - begin) * 1000
                    begin = time.perf_counter()
                    counts = db.execute(
                        select(Application.state, func.count()).group_by(
                            Application.state
                        )
                    ).all()
                    counts_ms = (time.perf_counter() - begin) * 1000
                    begin = time.perf_counter()
                    found = db.scalar(
                        select(Application.id).where(
                            Application.email_hash
                            == crypto.digest(
                                "email", f"synthetic{count-1}@example.invalid"
                            )
                        )
                    )
                    search_ms = (time.perf_counter() - begin) * 1000
                    begin = time.perf_counter()
                    detail = s.detail(db, user, page[0])
                    detail_ms = (time.perf_counter() - begin) * 1000
                    return {
                        "pops": 40,
                        "applications": count,
                        "encrypted_insert_ms": round(insert_ms, 2),
                        "page_50_ms": round(page_ms, 2),
                        "grouped_counts_ms": round(counts_ms, 2),
                        "indexed_email_ms": round(search_ms, 2),
                        "authorized_detail_ms": round(detail_ms, 2),
                        "measured_queries": len(statements),
                        "peak_process_rss_kib": resource.getrusage(
                            resource.RUSAGE_SELF
                        ).ru_maxrss,
                        "external_calls": 0,
                        "scope": "Single-process synthetic PostgreSQL; not concurrent public ingress, geocoding or production sizing",
                    }
                finally:
                    event.remove(db.get_bind(), "before_cursor_execute", before)
                    db.rollback()
        finally:
            settings.signup_pii_key_file = previous


if __name__ == "__main__":
    for n in (1000, 10000):
        print(json.dumps(measure(n)), flush=True)
