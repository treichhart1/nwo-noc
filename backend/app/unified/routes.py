from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from app.core.database import get_db
from app.field.routes import access, run
from app.field import service as field
from app.field.contracts import Mutation
from app.equipment import service as inv
from . import service as s
from .models import *
from .contracts import *

router = APIRouter(
    prefix="/api/unified",
    tags=["Private unified network"],
    dependencies=[Depends(access)],
)


def read(db, u, fn):
    return run(db, u, lambda: (s.reader(u), fn())[1])


def change(db, u, b, name, fn):
    def call():
        field.admin(u)
        return field.mutation(db, u, "unified_" + name, b, fn)

    return run(db, u, call, True)


@router.get("/me")
def me(u=Depends(access)):
    s.reader(u)
    return {
        "id": u.id,
        "role": u.role,
        "documentation_only": True,
        "live_gpon_collection": False,
    }


@router.get("/graph")
def graph(
    technology: str | None = Query(None, max_length=30),
    site_id: int | None = None,
    state: str | None = Query(None, max_length=30),
    health: str | None = Query(None, max_length=30),
    capacity_filter: str | None = Query(None, pattern="^(available|exhausted)$"),
    offset: int = Query(0, ge=0, le=50000),
    db=Depends(get_db),
    u=Depends(access),
):
    return read(
        db,
        u,
        lambda: s.graph_view(
            db, technology, site_id, state, health, capacity_filter, offset
        ),
    )


@router.get("/nodes/{id}")
def node_context(id: int, db=Depends(get_db), u=Depends(access)):
    def call():
        from app.fiber_sync.service import provenance
        result=s.node_context(db,u,id);result["fiber_designer"]=provenance(db,u,node_id=id);return result
    return read(db, u, call)


@router.post("/impact")
def impact(b: ImpactInput, db=Depends(get_db), u=Depends(access)):
    return read(db, u, lambda: s.impact(db, b.failed_node_ids, b.failed_edge_ids))


@router.post("/qualify")
def qualify(b: QualificationInput, db=Depends(get_db), u=Depends(access)):
    def call():
        r = s.qualification(db, b.latitude, b.longitude, b.technology)
        if b.application_id:
            from app.signup.models import Application

            a = s.get(db, Application, b.application_id)
            if a.technology != b.technology or not a.wrapped_key:
                raise ValueError("Active application technology mismatch")
            from app.signup import service as signup

            signup.event(
                db,
                a,
                u,
                "unified_qualification_review",
                "Operator requested preliminary unified network qualification",
                {"status": r["status"], "advisory_only": True},
            )
        return r

    return run(db, u, lambda: (s.reader(u), call())[1], True)


@router.get("/strands")
def strands(
    cable_id: int,
    offset: int = Query(0, ge=0, le=2000),
    db=Depends(get_db),
    u=Depends(access),
):
    def call():
        s.get(db, Cable, cable_id)
        rows = db.scalars(
            select(Strand)
            .where(Strand.cable_id == cable_id)
            .order_by(Strand.number)
            .offset(offset)
            .limit(100)
        ).all()
        ids = [r.id for r in rows]
        return {
            "items": [inv.view(r) for r in rows],
            "ends": [
                inv.view(e)
                for e in db.scalars(
                    select(End).where(End.strand_id.in_(ids)).order_by(End.id)
                )
            ],
            "next_offset": offset + 100 if len(rows) == 100 else None,
        }

    return read(db, u, call)


@router.get("/strands/{id}/trace")
def trace(id: int, db=Depends(get_db), u=Depends(access)):
    return read(db, u, lambda: s.trace_strand(db, id))


@router.get("/circuits")
def circuits(
    offset: int = Query(0, ge=0, le=10000), db=Depends(get_db), u=Depends(access)
):
    def call():
        c = db.scalars(
            select(Circuit).order_by(Circuit.id).offset(offset).limit(100)
        ).all()
        ids = [r.id for r in c]
        paths = db.scalars(select(Path).where(Path.circuit_id.in_(ids))).all()
        return {
            "items": [inv.view(r) for r in c],
            "paths": [inv.view(p) for p in paths],
            "next_offset": offset + 100 if len(c) == 100 else None,
        }

    return read(db, u, call)


@router.get("/recommendations")
def recommendations(db=Depends(get_db), u=Depends(access)):
    return read(db, u, lambda: s.recommendations(db))


@router.get("/events")
def events(
    offset: int = Query(0, ge=0, le=100000), db=Depends(get_db), u=Depends(access)
):
    return read(
        db,
        u,
        lambda: [
            inv.view(e)
            for e in db.scalars(
                select(Event).order_by(Event.id.desc()).offset(offset).limit(50)
            )
        ],
    )


@router.post("/nodes")
def create_node(b: NodeInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "create_node", lambda: s.create_node(db, u, b))


@router.post("/ports")
def create_port(b: PortInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "create_port", lambda: s.create_port(db, u, b))


@router.post("/edges")
def create_edge(b: EdgeInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "create_edge", lambda: s.create_edge(db, u, b))


@router.post("/cables")
def create_cable(b: CableInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "create_cable", lambda: s.create_cable(db, u, b))


@router.post("/pairs")
def create_pair(b: PairInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "create_pair", lambda: s.create_pair(db, u, b))


@router.post("/joins")
def create_join(b: JoinInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "create_join", lambda: s.create_join(db, u, b))


@router.post("/reservations")
def reserve(b: StrandInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "reserve", lambda: s.reserve(db, u, b))


@router.post("/circuits")
def circuit(b: CircuitInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "circuit", lambda: s.circuit(db, u, b))


@router.post("/paths")
def create_path(b: PathInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "create_path", lambda: s.create_path(db, u, b))


@router.post("/state")
def state_change(b: StateInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "state_change", lambda: s.state_change(db, u, b))


@router.post("/associations")
def association(b: AssociationInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "association", lambda: s.association(db, u, b))


@router.post("/observations")
def observe(b: ObservationInput, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "observe", lambda: s.observe(db, u, b))


@router.post("/import-existing")
def import_existing(b: Mutation, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "import_existing", lambda: s.import_existing(db, u, b))


@router.post("/circuits/{id}/review")
def circuit_review(id: int, b: CircuitReview, db=Depends(get_db), u=Depends(access)):
    return change(
        db, u, b, "review_circuit_" + str(id), lambda: s.review_circuit(db, u, id, b)
    )


@router.get("/incidents/{id}/impact")
def incident_impact(id: int, db=Depends(get_db), u=Depends(access)):
    return run(db, u, lambda: (s.reader(u), s.incident_impact(db, u, id))[1], True)


@router.post("/terminations")
def map_end(b: EndMapping, db=Depends(get_db), u=Depends(access)):
    return change(db, u, b, "map_end", lambda: s.map_end(db, u, b))
