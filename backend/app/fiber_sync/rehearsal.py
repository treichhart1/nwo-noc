"""Guarded isolated staging fixture, preserving existing inventory and upstream data."""

import json
from datetime import timedelta
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.config import settings
from app.core.security import hash_password
from app.models.entities import User, Site
from app.unified.models import Node, Cable
from .models import Source
from .contracts import SourceInput, ConfigureInput, BindingInput
from .fixtures import record, manifest
from . import service as s
from .benchmark import body


def seed():
    if not s.isolated():
        raise ValueError("Offline synthetic staging required")
    with SessionLocal() as db:
        if db.scalar(select(Source.id)):
            raise ValueError("Empty synthetic integration fixture required")
        if not db.scalar(select(User.id).where(User.username == "acceptance")):
            raise ValueError("Completed isolated acceptance setup required")
        core = db.scalar(
            select(Node)
            .where(Node.kind == "core", Node.site_id.is_not(None))
            .order_by(Node.id)
            .limit(1)
        )
        tower = db.scalar(
            select(Node)
            .where(Node.kind.in_(["tower", "pop"]), Node.site_id.is_not(None))
            .order_by(Node.id)
            .limit(1)
        )
        if not core or not tower:
            raise ValueError("Completed NOC104 canonical topology fixture required")
        a = db.get(Site, core.site_id)
        z = db.get(Site, tower.site_id)
        actor = User(
            username="fiber-acceptance",
            email="fiber-acceptance@example.invalid",
            password_hash=hash_password(settings.admin_password),
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
                code="SYN-FIBER",
                name="Synthetic Fiber Designer export",
                instance_id="synthetic-designer",
                project_id="synthetic-approved-project",
            ),
        )
        source = db.get(Source, src["id"])
        s.configure(
            db,
            actor,
            source,
            body(
                ConfigureInput,
                expected_revision=source.revision,
                enabled=True,
                adapter="mock",
                schedule_enabled=True,
            ),
        )
        for external, node in [("core", core), ("tower", tower)]:
            s.bind(
                db,
                actor,
                source,
                body(
                    BindingInput,
                    kind="node",
                    external_id=external,
                    reference_kind="node",
                    reference_id=node.id,
                ),
            )
        coords = (
            [[a.longitude, a.latitude], [z.longitude, z.latitude]]
            if all(
                v is not None
                for v in [a.latitude, a.longitude, z.latitude, z.longitude]
            )
            else [[-83.5, 41.5], [-83.49, 41.51]]
        )  # Explicit synthetic export fixture; never surveyed coordinates or an inventory update.
        records = [
            record("node", "core", role=core.kind, label="Existing synthetic core"),
            record("node", "tower", role=tower.kind, label="Existing synthetic tower"),
            record(
                "connection",
                "feed",
                a_ref="core",
                z_ref="tower",
                label="Synthetic approved additional fiber segment",
            ),
            record(
                "cable",
                "cable",
                connection_ref="feed",
                code="APPROVED-FEED",
                strand_count=8,
                geometry=coords,
            ),
            record(
                "route",
                "route",
                name="Synthetic approved operational route",
                geometry=coords,
                segment_refs=["cable"],
            ),
            record(
                "construction",
                "completed",
                state="completed",
                completed_at=(s.now() - timedelta(seconds=1)).isoformat(),
                component_refs=["cable"],
            ),
            record("as_built", "approved-revision", component_refs=["cable", "route"]),
        ]
        env = manifest(source, records)
        source.fixture = env.model_dump(mode="json")
        before = db.scalar(
            select(__import__("sqlalchemy").func.count()).select_from(Cable)
        )
        preview = s.preview(
            db,
            actor,
            source,
            env,
            "Synthetic administrator reviewed approved export preview",
        )
        assert preview["status"] == "preview", preview
        assert before == db.scalar(
            select(__import__("sqlalchemy").func.count()).select_from(Cable)
        )
        db.commit()
        return {
            "source_id": source.id,
            "preview_id": preview["id"],
            "username": actor.username,
            "envelope": env.model_dump(mode="json"),
            "existing_core_node_id": core.id,
            "existing_tower_node_id": tower.id,
            "installed_changes_before_approval": 0,
            "external_calls": 0,
        }


if __name__ == "__main__":
    print(json.dumps(seed()))
