"""Disposable PostgreSQL dashboard scale and query-budget regression.

Uses the existing rollback-only PostgreSQL fixture; no network integrations.
"""
from time import perf_counter

from sqlalchemy import event

from app.api.routes import dashboard
from app.models.entities import Device
from test_rf_postgres_integration import postgres_db


def test_dashboard_with_1000_synthetic_radios_has_fixed_query_budget(postgres_db):
    db = postgres_db
    prefix = "noc-dashboard-scale-"
    db.add_all([
        Device(
            source="synthetic",
            external_id=f"{prefix}{i}",
            name=f"Synthetic radio {i}",
            status=("online" if i % 4 == 0 else
                    "offline" if i % 4 == 1 else
                    "unknown" if i % 4 == 2 else "connected"),
            authorized=i % 5 != 0,
            enabled=True,
        )
        for i in range(1000)
    ])
    db.flush()
    engine = db.get_bind()
    statements = []

    def count_selects(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        start = perf_counter()
        result = dashboard(db=db, _=None)
        elapsed_ms = (perf_counter() - start) * 1000
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert result.total_devices >= 1000
    assert result.active_devices >= 500
    assert result.offline_devices >= 250
    assert result.unauthorized_devices >= 200
    assert result.warning_devices >= 250
    assert result.health_score == round(result.active_devices / result.total_devices * 100)
    assert len(statements) == 6, f"Dashboard issued {len(statements)} SELECTs"
    print(f"Dashboard synthetic 1000-radio query time: {elapsed_ms:.1f} ms; SELECTs: {len(statements)}")
