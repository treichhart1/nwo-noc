import json, hashlib
from fastapi import APIRouter, Depends, Query, Request, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.core.database import get_db
from app.core.config import settings
from app.field.routes import access, run as guarded
from app.field import service as field
from app.field.contracts import Mutation
from . import service as s, security
from .contracts import *
from .models import Source, Binding, Run, Version, Change

router = APIRouter(
    prefix="/api/fiber-designer/v1",
    tags=["Private approved fiber integration"],
    dependencies=[Depends(access)],
)
# Machine endpoint is separate: authenticated HMAC staging only, never operator approval.
machine_router = APIRouter(
    prefix="/api/fiber-designer-ingest/v1", tags=["Private signed fiber staging"]
)


def read(db, user, fn):
    return guarded(db, user, lambda: (s.reader(user), fn())[1])


def mutate(db, user, b, action, fn):
    def call():
        field.admin(user)
        return field.mutation(db, user, "fiber_sync_" + action, b, fn)

    return guarded(db, user, call, True)


@router.get("/contract")
def contract(db=Depends(get_db), user=Depends(access)):
    return read(
        db,
        user,
        lambda: {
            "schema_version": "nwo.fiber-operational.v1",
            "json_schema": Envelope.model_json_schema(),
            "live_adapter_available": False,
            "automatic_apply": False,
            "owner_boundary": {
                "designer": [
                    "engineering",
                    "as_built",
                    "construction",
                    "811",
                    "splice_design",
                    "cost_estimation",
                ],
                "noc": [
                    "observed_health",
                    "operational_topology",
                    "customer_impact",
                    "inventory",
                    "ipam",
                    "maintenance",
                ],
            },
        },
    )


@router.get("/sources")
def sources(db=Depends(get_db), user=Depends(access)):
    return read(db, user, lambda: s.sources(db, user))


@router.post("/sources")
def source(b: SourceInput, db=Depends(get_db), user=Depends(access)):
    return mutate(db, user, b, "create_source", lambda: s.source_create(db, user, b))


@router.post("/sources/{id}/configuration")
def configure(id: int, b: ConfigureInput, db=Depends(get_db), user=Depends(access)):
    return mutate(
        db,
        user,
        b,
        f"configure:{id}",
        lambda: s.configure(db, user, s.get(db, Source, id), b),
    )


@router.post("/sources/{id}/credential")
def credential(id: int, b: CredentialInput, db=Depends(get_db), user=Depends(access)):
    # Masked SecretStr is never used as an idempotency digest. Bind the receipt to a nonreversible digest.
    class CredentialReceipt(Mutation):
        credential_digest: str

    value = b.signing_secret.get_secret_value()
    receipt = CredentialReceipt(
        operation_key=b.operation_key,
        reason=b.reason,
        credential_digest=hashlib.sha256(value.encode()).hexdigest(),
    )

    def call():
        source = s.get(db, Source, id)
        security.set_secret(source, value)
        return {
            "id": id,
            "credential_configured": True,
            "scope": "stage approved export only; no apply or equipment writes",
        }

    return mutate(db, user, receipt, f"credential:{id}", call)


@router.get("/sources/{id}/bindings")
def bindings(
    id: int,
    offset: int = Query(0, ge=0, le=20000),
    db=Depends(get_db),
    user=Depends(access),
):
    return read(
        db,
        user,
        lambda: {
            "items": [
                s.view(x)
                for x in db.scalars(
                    select(Binding)
                    .where(Binding.source_id == id)
                    .order_by(Binding.id)
                    .offset(offset)
                    .limit(50)
                )
            ]
        },
    )


@router.post("/sources/{id}/bindings")
def binding(id: int, b: BindingInput, db=Depends(get_db), user=Depends(access)):
    return mutate(
        db, user, b, f"bind:{id}", lambda: s.bind(db, user, s.get(db, Source, id), b)
    )


@router.post("/sources/{id}/preview")
def preview(id: int, b: PreviewInput, db=Depends(get_db), user=Depends(access)):
    return mutate(
        db,
        user,
        b,
        f"preview:{id}",
        lambda: s.preview(db, user, s.get(db, Source, id), b.envelope, b.reason),
    )


@router.post("/sources/{id}/mock-envelope")
def fixture(id: int, b: FixtureInput, db=Depends(get_db), user=Depends(access)):
    def call():
        if not s.isolated():
            raise s.Conflict("mock_requires_offline_synthetic_staging")
        source = s.get(db, Source, id)
        if (
            source.instance_id != b.envelope.instance_id
            or source.project_id != b.envelope.project_id
        ):
            raise s.Conflict("source_project_mismatch")
        source.fixture = b.envelope.model_dump(mode="json")
        source.revision += 1
        return {"id": id, "fixture_configured": True, "live_connection": False}

    return mutate(db, user, b, f"mock_fixture:{id}", call)


@router.post("/sources/{id}/synchronize")
def synchronize(id: int, b: Mutation, db=Depends(get_db), user=Depends(access)):
    def call():
        from .adapters import MockAdapter

        source = s.get(db, Source, id)
        if source.adapter != "mock" or not s.isolated():
            raise s.Conflict("no_verified_live_adapter_use_manual_export")
        envelope = MockAdapter(source.fixture).fetch_approved(
            instance_id=source.instance_id,
            project_id=source.project_id,
            after_cursor=source.applied_cursor,
        )
        return s.preview(db, user, source, envelope, b.reason, origin="mock")

    return mutate(db, user, b, f"synchronize_preview_only:{id}", call)


@router.get("/runs")
def runs(
    source_id: int | None = None,
    offset: int = Query(0, ge=0, le=20000),
    db=Depends(get_db),
    user=Depends(access),
):
    stmt = select(Run).order_by(Run.id.desc()).offset(offset).limit(50)
    if source_id:
        stmt = stmt.where(Run.source_id == source_id)
    return read(db, user, lambda: {"items": [s.run_view(x) for x in db.scalars(stmt)]})


@router.get("/runs/{id}")
def details(
    id: int,
    change_offset: int = Query(0, ge=0, le=50000),
    db=Depends(get_db),
    user=Depends(access),
):
    return read(
        db,
        user,
        lambda: {
            **s.run_view(s.get(db, Run, id)),
            "change_offset": change_offset,
            "change_next_offset": (
                change_offset + 100
                if db.scalar(
                    select(Change.id)
                    .where(Change.run_id == id)
                    .order_by(Change.id)
                    .offset(change_offset + 100)
                    .limit(1)
                )
                else None
            ),
            "changes": [
                s.view(x)
                for x in db.scalars(
                    select(Change)
                    .where(Change.run_id == id)
                    .order_by(Change.id)
                    .offset(change_offset)
                    .limit(100)
                )
            ],
        },
    )


@router.post("/runs/{id}/repreview")
def repreview(id: int, b: Mutation, db=Depends(get_db), user=Depends(access)):
    def call():
        old = s.get(db, Run, id)
        return s.preview(
            db,
            user,
            s.get(db, Source, old.source_id),
            Envelope.model_validate(old.payload),
            b.reason,
            origin="manual",
            new_attempt=True,
        )

    return mutate(db, user, b, f"repreview:{id}", call)


@router.post("/runs/{id}/approve")
def approve(id: int, b: ApprovalInput, db=Depends(get_db), user=Depends(access)):
    return mutate(
        db, user, b, f"approve:{id}", lambda: s.approve(db, user, s.get(db, Run, id), b)
    )


@router.get("/runs/{id}/rollback-preview")
def rollback_preview(id: int, db=Depends(get_db), user=Depends(access)):
    return read(db, user, lambda: s.rollback_preview(db, user, s.get(db, Run, id)))


@router.post("/runs/{id}/rollback")
def rollback(id: int, b: ApprovalInput, db=Depends(get_db), user=Depends(access)):
    return mutate(
        db,
        user,
        b,
        f"rollback:{id}",
        lambda: s.rollback(db, user, s.get(db, Run, id), b),
    )


@router.get("/versions")
def versions(
    source_id: int,
    offset: int = Query(0, ge=0, le=20000),
    db=Depends(get_db),
    user=Depends(access),
):
    return read(
        db,
        user,
        lambda: {
            "items": [
                s.view(x)
                for x in db.scalars(
                    select(Version)
                    .where(Version.source_id == source_id)
                    .order_by(Version.id.desc())
                    .offset(offset)
                    .limit(50)
                )
            ]
        },
    )


@router.get("/nodes/{id}/provenance")
def provenance(id: int, db=Depends(get_db), user=Depends(access)):
    return read(db, user, lambda: s.provenance(db, user, node_id=id))


@machine_router.post("/sources/{id}/exports")
async def ingest(id: int, request: Request, db=Depends(get_db)):
    from uuid import uuid5, NAMESPACE_OID
    from app.equipment.service import lock
    from app.models.entities import User

    if request.url.scheme != "https":
        raise HTTPException(403, "HTTPS integration transport required")
    if not settings.fiber_designer_ingest_enabled:
        raise HTTPException(
            401, "Source authentication unavailable or integration disabled"
        )
    raw = await request.body()
    if len(raw) > 2097152:
        raise HTTPException(413, "Export exceeds bounded integration limit")
    source = None
    authenticated = False
    try:
        lock(db)
        source = db.get(Source, id)
        if source is None:
            raise PermissionError()
        security.verify(
            db,
            source,
            request.headers.get("x-fiber-timestamp", ""),
            request.headers.get("x-fiber-nonce", ""),
            request.headers.get("x-fiber-signature", ""),
            raw,
        )
        # Commit the valid nonce even if the signed export schema is invalid.
        # A valid staging credential must not bypass replay/rate limits via malformed data.
        db.commit()
        authenticated = True
        try:
            envelope = Envelope.model_validate_json(raw)
        except ValueError:
            lock(db)
            source = s.get(db, Source, id)
            sha = hashlib.sha256(raw).hexdigest()
            eid = str(uuid5(NAMESPACE_OID, f"{id}:{sha}"))
            if not db.scalar(
                select(Run.id).where(Run.source_id == id, Run.envelope_id == eid)
            ):
                db.add(
                    Run(
                        source_id=id,
                        envelope_id=eid,
                        attempt=1,
                        envelope_digest=sha,
                        payload={},
                        baseline_digest="0" * 64,
                        plan_digest=sha,
                        report={
                            "failure_code": "signed_contract_rejected",
                            "operational_changes": False,
                        },
                        status="failed",
                        actor_id=source.created_by,
                        origin="signed_source",
                    )
                )
            source.last_error_code = "signed_contract_rejected"
            source.last_attempt_at = s.now()
            field.audit(
                db,
                None,
                "fiber_sync.signed_contract_rejected",
                "fiber_integration",
                id,
                {"body_digest": sha, "operational_changes": False},
            )
            db.commit()
            raise HTTPException(
                409,
                "Approved versioned export contract required; no operational changes",
            )
        lock(db)
        source = s.get(db, Source, id)
        user = s.get(db, User, source.created_by)
        result = s.preview(
            db,
            user,
            source,
            envelope,
            "Authenticated source export staged; administrator approval required",
            origin="signed_source",
        )
        db.commit()
        return result
    except PermissionError:
        db.rollback()
        field.audit(
            db,
            None,
            "fiber_sync.authentication_denied",
            "fiber_integration",
            "private",
            {"operational_changes": False},
        )
        db.commit()
        raise HTTPException(
            401, "Source authentication unavailable or integration disabled"
        ) from None
    except (ValueError, LookupError, IntegrityError):
        db.rollback()
        if authenticated:
            field.audit(
                db,
                None,
                "fiber_sync.export_conflict",
                "fiber_integration",
                id,
                {"operational_changes": False},
            )
            db.commit()
        raise HTTPException(
            409,
            "Source contract, cursor, approval or replay conflict; no operational data changed",
        ) from None


@router.get("/approved-routes")
def approved_routes(
    source_id: int | None = None,
    offset: int = Query(0, ge=0, le=20000),
    db=Depends(get_db),
    user=Depends(access),
):
    def call():
        stmt = (
            select(Version)
            .join(
                Binding,
                (Version.source_id == Binding.source_id)
                & (Version.kind == Binding.kind)
                & (Version.external_id == Binding.external_id)
                & (Version.revision == Binding.operational_revision),
            )
            .where(Version.kind == "route", Binding.active.is_(True))
            .order_by(Version.id.desc())
            .offset(offset)
            .limit(50)
        )
        if source_id:
            stmt = stmt.where(Version.source_id == source_id)
        return {
            "items": [s.view(v) for v in db.scalars(stmt)],
            "predictions": False,
            "observed_health": "Not inferred from installed as-built records",
        }

    return read(db, user, call)


@router.get("/approved-nodes")
def approved_nodes(
    source_id: int | None = None,
    offset: int = Query(0, ge=0, le=20000),
    db=Depends(get_db),
    user=Depends(access),
):
    def call():
        stmt = (
            select(Version, Binding)
            .join(
                Binding,
                (Version.source_id == Binding.source_id)
                & (Version.kind == Binding.kind)
                & (Version.external_id == Binding.external_id)
                & (Version.revision == Binding.operational_revision),
            )
            .where(Version.kind == "node", Binding.active.is_(True))
            .order_by(Version.id.desc())
            .offset(offset)
            .limit(50)
        )
        if source_id:
            stmt = stmt.where(Version.source_id == source_id)
        return {
            "items": [
                {
                    "source_id": v.source_id,
                    "external_id": v.external_id,
                    "approved_revision": v.revision,
                    "noc_node_id": b.node_id,
                    "payload": v.payload,
                    "coordinate_basis": "Approved source documentation, not an observed GPS measurement",
                }
                for v, b in db.execute(stmt)
            ],
            "inventory_coordinates_modified": False,
        }

    return read(db, user, call)
