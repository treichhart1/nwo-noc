"""Rollback-only 40-POP operational financial report benchmark, no external I/O."""

import time, json, resource
from datetime import date
from uuid import uuid4
from sqlalchemy import select, func
from app.core.database import SessionLocal
from app.models.entities import User, Site
from . import service as s
from .models import Scope, Entry, Period
from .contracts import *

R = "Synthetic rollback-only finance benchmark; zero production or billing access"


def b(cls, **kw):
    return cls(operation_key=uuid4(), reason=R, **kw)


def main():
    for size in [500, 2000]:
        with SessionLocal() as db:
            u = User(
                username="finance-bench-" + uuid4().hex,
                email="finance@example.invalid",
                password_hash="unusable",
                role="admin",
                is_active=True,
            )
            db.add(u)
            db.flush()
            p = s.create_period(
                db,
                u,
                b(
                    PeriodInput,
                    code="bench-" + uuid4().hex,
                    starts_on=date(2024, 1, 1),
                    ends_on=date(2024, 2, 1),
                    currency="USD",
                ),
            )
            scopes = []
            for i in range(40):
                site = Site(name="Synthetic finance benchmark " + uuid4().hex)
                db.add(site)
                db.flush()
                scopes.append(
                    s.create_scope(
                        db,
                        u,
                        b(
                            ScopeInput,
                            code="bench-" + uuid4().hex,
                            name=f"Synthetic POP {i}",
                            kind="tower",
                            reference_id=site.id,
                        ),
                    )["id"]
                )
            network = s.create_scope(
                db,
                u,
                b(
                    ScopeInput,
                    code="bench-" + uuid4().hex,
                    name="Synthetic network",
                    kind="network",
                ),
            )["id"]
            for i in range(size):
                db.add(
                    Entry(
                        period_id=p["id"],
                        scope_id=scopes[i % 40],
                        kind="revenue" if i % 2 == 0 else "opex",
                        category="subscription" if i % 2 == 0 else "power",
                        basis="contracted" if i % 2 == 0 else "actual",
                        amount="100.00",
                        verified_collection=False,
                        source_type="synthetic",
                        external_key=str(i),
                        evidence=R,
                        dimensions={"site_id": db.get(Scope, scopes[i % 40]).site_id},
                        created_by=u.id,
                    )
                )
            db.flush()
            start = time.perf_counter()
            report = s.calculate(db, p["id"], network)
            network_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            dataset = s.financial_data(db, db.get(Period, p["id"]))
            rows = [s.calculate(db, p["id"], id, _dataset=dataset) for id in scopes]
            tower_ms = (time.perf_counter() - start) * 1000
            count = db.scalar(
                select(func.count())
                .select_from(Entry)
                .where(Entry.period_id == p["id"])
            )
            assert count == size
            assert sum(s.D(r["totals"]["revenue"]) for r in rows) == s.D(
                report["totals"]["revenue"]
            )
            print(
                json.dumps(
                    {
                        "pops": 40,
                        "ledger_entries": count,
                        "network_report_ms": round(network_ms, 2),
                        "forty_tower_reports_ms": round(tower_ms, 2),
                        "peak_rss_kib": resource.getrusage(
                            resource.RUSAGE_SELF
                        ).ru_maxrss,
                        "external_calls": 0,
                        "transaction": "rolled_back",
                        "scope": "single-process synthetic PostgreSQL, not sustained production sizing",
                    }
                ),
                flush=True,
            )
            db.rollback()


if __name__ == "__main__":
    main()
