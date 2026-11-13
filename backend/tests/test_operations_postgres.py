"""Real PostgreSQL operations summary regression; fixture rolls back all changes."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.api.routes import operations_summary
from app.models.entities import Device, Incident, MaintenanceWindow, NetworkLink, Site
from test_rf_postgres_integration import postgres_db


def test_operations_summary_real_postgres_counts_and_budget(postgres_db):
    db = postgres_db
    now = datetime.now(timezone.utc)
    a = Site(name="noc-77i-site-a")
    b = Site(name="noc-77i-site-b")
    db.add_all([a, b])
    db.flush()
    db.add_all([
        Device(source="synthetic", external_id="noc-77i-1", name="Down A1",
               status="offline", wisp_site_id=a.id, last_seen=now - timedelta(hours=1)),
        Device(source="synthetic", external_id="noc-77i-2", name="Down A2",
               status="unreachable", wisp_site_id=a.id, last_seen=now),
        Device(source="synthetic", external_id="noc-77i-3", name="Down B",
               status="disconnected", wisp_site_id=b.id, last_seen=None),
        Device(source="synthetic", external_id="noc-77i-4", name="Unassigned",
               status="online", last_seen=now),
        Incident(title="NOC-77I critical", severity="critical", status="open", customer_impact=12),
        Incident(title="NOC-77I warning", severity="warning", status="open", customer_impact=5),
        Incident(title="NOC-77I resolved", severity="critical", status="resolved", customer_impact=100),
        MaintenanceWindow(title="NOC-77I planned", starts_at=now + timedelta(hours=1),
                          ends_at=now + timedelta(hours=2), status="scheduled"),
        MaintenanceWindow(title="NOC-77I expired", starts_at=now - timedelta(hours=3),
                          ends_at=now - timedelta(hours=2), status="scheduled"),
        NetworkLink(name="noc-77i-degraded", a_site_id=a.id, z_site_id=b.id, status="degraded"),
        NetworkLink(name="noc-77i-online", a_site_id=a.id, z_site_id=b.id, status="online"),
    ])
    db.flush()
    statements = []

    def track(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", track)
    try:
        summary = operations_summary(db=db, _=None)
    finally:
        event.remove(bind, "before_cursor_execute", track)
    assert summary.open_incidents == 2
    assert summary.critical_incidents == 1
    assert summary.impacted_customers == 17
    assert summary.upcoming_maintenance == 1
    assert summary.offline_sites == 2
    assert summary.degraded_links == 1
    assert summary.unassigned_devices == 1
    assert summary.stale_devices == 2
    assert len(statements) == 4, f"Expected four SELECTs, got {len(statements)}"
