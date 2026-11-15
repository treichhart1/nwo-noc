"""Normalized SYNTHETIC producer, not Fiber Designer endpoints or a native API claim."""

from datetime import timedelta
from uuid import uuid4
from .contracts import Envelope
from app.field.models import now


def record(kind, id, **kw):
    return {
        "kind": kind,
        "external_id": id,
        "revision": 1,
        "previous_revision": None,
        "publication": "approved",
        "infrastructure_status": (
            "completed" if kind == "construction" else "commissioned"
        ),
        "approval_ref": "SYNTHETIC-ASBUILT-APPROVAL",
        "approved_at": (now() - timedelta(seconds=1)).isoformat(),
        **kw,
    }


def manifest(source, records, from_cursor=0, to_cursor=1):
    return Envelope(
        schema_version="nwo.fiber-operational.v1",
        envelope_id=uuid4(),
        instance_id=source.instance_id,
        project_id=source.project_id,
        from_cursor=from_cursor,
        to_cursor=to_cursor,
        generated_at=now() - timedelta(seconds=1),
        records=records,
    )
