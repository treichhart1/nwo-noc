"""Public projection contracts and canonical approval regressions; synthetic only."""

import copy
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from app.customer_status.public_v2 import projected, AreaDetails, Announcement, display
from app.customer_status.contracts import AreaInput
from test_customer_network_status import (
    x,
    network,
    postgres_db,
    draft,
    publish,
    b,
    client,
)
from app.customer_status import service as s
from test_powercode_customers import imported, inventory

NOW = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class ProjectionDB:
    def __init__(self):
        self.policy = {
            "enabled": True,
            "baseline_approved_at": NOW,
            "baseline_status": "all_systems_operational",
            "last_public_update": NOW,
        }
        self.rows = []
        self.areas = []

    def execute(self, statement):
        sql = str(statement)
        if "configuration" in sql:
            return Result([self.policy])
        if "announcements" in sql:
            return Result(self.rows)
        if "noc_public_status.areas" in sql:
            return Result(self.areas)
        return Result([])


def payload(**kw):
    return dict(
        title="Approved synthetic interruption",
        description="An approved synthetic service interruption for contract testing.",
        status="service_disruption",
        areas=["Synthetic community"],
        impact="partial",
        **kw,
    )


def test_v2_missing_and_stale_are_not_healthy():
    db = ProjectionDB()
    assert (
        json.loads(projected(db, 0, 20, NOW))["overall_status"]
        == "all_systems_operational"
    )
    stale = json.loads(projected(db, 0, 20, NOW + timedelta(days=2)))
    assert stale["overall_status"] == "unknown" and stale["freshness"] == "stale"
    db.policy["enabled"] = False
    with pytest.raises(ValueError):
        projected(db, 0, 20, NOW)


@pytest.mark.parametrize(
    "impact,status",
    [
        ("partial", "partial_service_interruption"),
        ("major", "major_service_interruption"),
        ("degraded", "degraded_performance"),
        ("general", "service_disruption"),
    ],
)
def test_approved_impact(impact, status):
    db = ProjectionDB()
    p = payload()
    p["impact"] = impact
    db.rows = [
        dict(identifier=str(uuid4()), kind="incident", payload=p, updated_at=NOW)
    ]
    feed = json.loads(projected(db, 0, 20, NOW))
    assert feed["overall_status"] == status
    assert feed["incidents"][0]["display_status"] == status


@pytest.mark.parametrize(
    "field,value",
    [
        ("county", "192.168.1.1"),
        ("group_label", "<script>"),
        ("description", "support@example.test"),
    ],
)
def test_public_metadata_rejects_private_fields(field, value):
    with pytest.raises(ValidationError):
        AreaDetails(**{field: value})


def test_projection_rejects_unreviewed_keys_and_oversize():
    db = ProjectionDB()
    db.rows = [
        dict(
            identifier=str(uuid4()),
            kind="incident",
            payload={**payload(), "customer_phone": "private"},
            updated_at=NOW,
        )
    ]
    with pytest.raises(ValidationError):
        projected(db, 0, 20, NOW)
    db.rows *= 201
    with pytest.raises(ValueError):
        projected(db, 0, 20, NOW)


def test_withdrawal_revision_token_and_last_update():
    db = ProjectionDB()
    before = json.loads(projected(db, 0, 20, NOW))
    db.policy["last_public_update"] = NOW + timedelta(seconds=1)
    after = json.loads(projected(db, 0, 20, NOW + timedelta(seconds=1)))
    assert before["revision_token"] != after["revision_token"]
    assert after["last_public_update"] > before["last_public_update"]


def test_reproducible_plugin_zip(tmp_path):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "plugin_build", root / "scripts/wordpress/build.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    a = module.build(root, tmp_path / "one")
    b = module.build(root, tmp_path / "two")
    assert a == b
    assert (tmp_path / "one/nwo-network-status.zip").read_bytes() == (
        tmp_path / "two/nwo-network-status.zip"
    ).read_bytes()
    assert len(a["members"]) == 9


def test_postgres_v2_approved_snapshot_and_v1_compatibility(x):
    c = client(x, True)
    try:
        r = draft(x, impact="major")
        assert c.get("/public/network-status/v2").json()["incidents"] == []
        publish(x, r)
        v2 = c.get("/public/network-status/v2")
        assert v2.status_code == 200
        data = v2.json()
        assert data["overall_status"] == "major_service_interruption"
        assert data["service_areas"][0]["label"] == x.area["label"]
        v1 = c.get("/public/network-status/v1")
        assert v1.status_code == 200 and "impact" not in v1.json()["incidents"][0]
        for value in (
            "device_id",
            "customer_id",
            "management_ip",
            "source_digest",
            "approved_by",
        ):
            assert value not in v2.text
        assert c.post("/api/customer-status/v1/cases").status_code == 405
        assert c.get("/api/customer-status/v1/cases").status_code == 404
    finally:
        from app.customer_status.public_app import app

        app.dependency_overrides.clear()


def test_postgres_related_slack_is_draft_and_deduplicated(x):
    from app.notifications.slack_service import prepare
    from app.notifications.models import NotificationPlan

    first = prepare(x.db, "incident", x.incident.id, x.admin.id)
    again = prepare(x.db, "incident", x.incident.id, x.admin.id)
    assert first.id == again.id and first.status == "draft"
    assert x.db.get(NotificationPlan, first.id).incident_id == x.incident.id


def test_canonical_maintenance_slack_draft_invalidates_on_status_change():
    from app.notifications.slack_sources import snapshot

    row = SimpleNamespace(
        id=7,
        revision=1,
        status="scheduled",
        target={"site_id": 4},
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=1),
    )
    db = SimpleNamespace(get=lambda model, identifier: row)
    first = snapshot(db, "notification_maintenance", 7, NOW)
    row.revision = 2
    row.status = "cancelled"
    later = snapshot(db, "notification_maintenance", 7, NOW)
    assert first["source_revision"] != later["source_revision"]
    assert first["event"] == "maintenance" and first["site_id"] == 4
    assert "title" not in first and "description" not in first


def test_postgres_legacy_stale_feed_fails_closed(x):
    from app.customer_status.models import Policy

    x.db.get(Policy, 1).baseline_approved_at = s.clock() - timedelta(days=2)
    x.db.flush()
    c = client(x, True)
    try:
        assert c.get("/public/network-status/v1").status_code == 503
        response = c.get("/public/network-status/v2")
        assert (
            response.status_code == 200
            and response.json()["overall_status"] == "unknown"
        )
    finally:
        from app.customer_status.public_app import app

        app.dependency_overrides.clear()


def test_postgres_slack_draft_endpoint_is_idempotent_and_private(x):
    from app.customer_status.contracts import Mutation
    from app.customer_status.models import Event
    from sqlalchemy import select, func

    c = client(x)
    body = b(Mutation).model_dump(mode="json")
    try:
        url = f"/api/customer-status/v1/cases/{x.p.id}/slack-draft"
        first = c.post(url, json=body)
        assert first.status_code == 200, first.text
        assert (
            first.json()["status"] == "draft"
            and first.json()["delivery_authorized"] is False
        )
        assert c.post(url, json=body).json() == first.json()
        assert (
            x.db.scalar(
                select(func.count())
                .select_from(Event)
                .where(
                    Event.publication_id == x.p.id,
                    Event.action == "slack_draft_prepared",
                )
            )
            == 1
        )
    finally:
        from app.main import app

        app.dependency_overrides.clear()
