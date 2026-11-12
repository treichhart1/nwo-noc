"""Read-only RF site SQL query budget, using disposable PostgreSQL."""
from sqlalchemy import event

from app.models.entities import Device, Site
from app.services.rf_site_health import get_rf_site_health
from test_rf_postgres_integration import postgres_db


def test_site_summary_query_count_is_bounded_and_explicit(postgres_db):
    db = postgres_db
    site = Site(name="RF Query Budget CI")
    db.add(site)
    db.flush()
    db.add_all([
        Device(source="uisp", external_id=f"rf-query-{i}", name=f"Radio {i}",
               enabled=True, wisp_site_id=site.id)
        for i in range(40)
    ])
    db.flush()
    statements = []
    engine = db.get_bind().engine

    def count(_conn, _cursor, statement, _params, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        report = get_rf_site_health(db)
    finally:
        event.remove(engine, "before_cursor_execute", count)

    assert report["processed_devices"] == 40
    assert report["sites"][0]["missing"] == 40
    # Current implementation: 2 inventory queries, 40 get_db/device lookups,
    # 40 bounded diagnostics history queries, 1 grouped future lookup.
    # Explicitly capture the remaining N+1 cost for the bulk rewrite.
    assert len(statements) <= 83
    assert len(statements) >= 80
