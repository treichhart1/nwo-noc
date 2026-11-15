"""Rollback-only approved export preview/apply at40 synthetic POPs; no external calls."""

import json, time, resource
from uuid import uuid4
from sqlalchemy import select, func
from app.core.database import SessionLocal
from app.models.entities import Site, User
from app.unified.models import Cable, Strand
from .models import Source, Run
from .contracts import SourceInput, ConfigureInput, BindingInput, ApprovalInput
from .fixtures import record, manifest
from . import service as s

R = "Synthetic rollback-only approved export benchmark, no engineering or network changes"


def body(cls, **kw):
    return cls(operation_key=uuid4(), reason=R, **kw)


def main():
    for segments in [40, 200]:
        with SessionLocal() as db:
            actor = User(
                username="fiber-bench-" + str(uuid4()),
                email="fiber-bench@example.invalid",
                password_hash="unusable",
                role="admin",
                is_active=True,
            )
            db.add(actor)
            db.flush()
            src = s.source_create(
                db,
                actor,
                body(
                    SourceInput,
                    code="BENCH-" + uuid4().hex[:12],
                    name="Synthetic approved fiber benchmark",
                    instance_id="synthetic-benchmark",
                    project_id=str(uuid4()),
                ),
            )
            source = db.get(Source, src["id"])
            s.configure(
                db,
                actor,
                source,
                body(ConfigureInput, expected_revision=source.revision, enabled=True),
            )
            records = []
            for i in range(40):
                site = Site(
                    name="NOC106 Synthetic " + str(uuid4()),
                    latitude=41.4 + (i // 8) * 0.02,
                    longitude=-83.7 + (i % 8) * 0.02,
                )
                db.add(site)
                db.flush()
                s.bind(
                    db,
                    actor,
                    source,
                    body(
                        BindingInput,
                        kind="node",
                        external_id=f"pop-{i}",
                        reference_kind="site",
                        reference_id=site.id,
                    ),
                )
                records.append(
                    record(
                        "node",
                        f"pop-{i}",
                        role="core" if i == 0 else "pop",
                        label=f"Synthetic POP {i}",
                    )
                )
            for i in range(segments):
                a = i % 40
                z = (i + 1) % 40
                records.append(
                    record(
                        "connection",
                        f"link-{i}",
                        a_ref=f"pop-{a}",
                        z_ref=f"pop-{z}",
                        label=f"Synthetic approved parallel segment {i}",
                    )
                )
                records.append(
                    record(
                        "cable",
                        f"cable-{i}",
                        connection_ref=f"link-{i}",
                        code=f"SYN-CABLE-{i}",
                        strand_count=24,
                        geometry=[
                            [-83.7 + (a % 8) * 0.02, 41.4 + (a // 8) * 0.02],
                            [-83.7 + (z % 8) * 0.02, 41.4 + (z // 8) * 0.02],
                        ],
                    )
                )
            env = manifest(source, records)
            start = time.perf_counter()
            preview = s.preview(db, actor, source, env, R)
            preview_ms = (time.perf_counter() - start) * 1000
            assert preview["status"] == "preview", preview["report"]
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Cable)
                    .where(Cable.code.like(f"fd{source.id}:%"))
                )
                == 0
            )
            start = time.perf_counter()
            run = db.get(Run, preview["id"])
            result = s.approve(
                db,
                actor,
                run,
                body(
                    ApprovalInput,
                    plan_digest=preview["plan_digest"],
                    approve_documentation_only=True,
                ),
            )
            apply_ms = (time.perf_counter() - start) * 1000
            assert result["status"] == "applied", result
            print(
                json.dumps(
                    {
                        "pops": 40,
                        "approved_records": len(records),
                        "segments": segments,
                        "strands": db.scalar(
                            select(func.count())
                            .select_from(Strand)
                            .where(
                                Strand.cable_id.in_(
                                    select(Cable.id).where(
                                        Cable.code.like(f"fd{source.id}:%")
                                    )
                                )
                            )
                        ),
                        "preview_ms": round(preview_ms, 2),
                        "apply_ms": round(apply_ms, 2),
                        "peak_rss_kib": resource.getrusage(
                            resource.RUSAGE_SELF
                        ).ru_maxrss,
                        "external_calls": 0,
                        "transaction": "rolled_back_after_measurement",
                        "scope": "Single-process synthetic PostgreSQL reconciliation; not live Designer compatibility or concurrent production sizing",
                    }
                )
            )
            db.rollback()


if __name__ == "__main__":
    main()
