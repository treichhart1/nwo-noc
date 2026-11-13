"""Disposable PostgreSQL monitoring summary regression with no live probes."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.api.routes import monitoring_summary
from app.models.entities import Device, MonitorEvent
from test_rf_postgres_integration import postgres_db


def test_monitoring_summary_postgres_states_latency_and_query_budget(postgres_db):
    db = postgres_db
    now = datetime.now(timezone.utc)
    devices = [
        Device(source="synthetic", external_id=f"noc-77j-{i}", name=f"Monitor {i}",
               monitor_enabled=True, monitor_status=status, monitor_latency_ms=latency,
               monitor_last_checked_at=now - timedelta(minutes=i))
        for i, (status, latency) in enumerate([
            ("up", 10.0), ("up", 20.0), ("down", None), ("unknown", None)
        ])
    ]
    devices[3].monitor_last_checked_at = now - timedelta(minutes=30)
    devices.append(Device(source="synthetic", external_id="noc-77j-disabled",
                          name="Disabled monitor", monitor_enabled=False,
                          monitor_status="down", monitor_latency_ms=999.0))
    db.add_all(devices)
    db.flush()
    db.add_all([
        MonitorEvent(device_id=devices[0].id, event_type="recovered",
                     to_status="up", message="Synthetic recovery", acknowledged=True),
        MonitorEvent(device_id=devices[2].id, event_type="down",
                     to_status="down", message="Synthetic outage", acknowledged=False),
    ])
    db.flush()
    statements = []

    def track(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", track)
    try:
        summary = monitoring_summary(db=db, _=None)
    finally:
        event.remove(bind, "before_cursor_execute", track)

    assert summary.enabled_devices == 4
    assert summary.stale_devices == 1
    assert summary.fresh_up_devices == 2
    assert summary.fresh_down_devices == 1
    assert summary.up_devices == 2
    assert summary.down_devices == 1
    assert summary.unknown_devices == 1
    assert summary.average_latency_ms == 15.0
    assert summary.unacknowledged_events == 1
    assert summary.last_check_at is not None
    assert len(statements) == 2, f"Expected two SELECTs, got {len(statements)}"
