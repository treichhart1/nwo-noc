"""NOC111 rollback-only40-POP site-summary profile; no device/provider connections."""

import json, time, resource
from uuid import uuid4
from sqlalchemy import event
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Site, Device
from app.api.routes import sites


def main():
    if not (
        settings.app_env == "staging"
        and settings.offline_mode
        and settings.synthetic_monitoring_enabled
    ):
        raise ValueError("Explicit offline synthetic staging required")
    for size in [250, 1000, 10000]:
        with SessionLocal() as db:
            marker = uuid4().hex
            rows = [Site(name=f"Synthetic audit {marker} POP {i}") for i in range(40)]
            db.add_all(rows)
            db.flush()
            db.add_all(
                [
                    Device(
                        source="synthetic",
                        external_id=marker + str(i),
                        name="Synthetic site benchmark",
                        wisp_site_id=rows[i % 40].id,
                        site_name="Unrelated legacy name",
                        status="active",
                        authorized=True,
                    )
                    for i in range(size)
                ]
            )
            db.flush()
            db.expire_all()
            ids = {s.id for s in rows}
            timings = []
            counts = []
            connection = db.connection()
            for _ in range(5):
                selects = []

                def query(conn, cursor, statement, parameters, context, executemany):
                    if statement.lstrip().upper().startswith("SELECT"):
                        selects.append(1)

                event.listen(connection, "before_cursor_execute", query)
                try:
                    at = time.perf_counter()
                    result = sites(db=db, _=None)
                    timings.append(round((time.perf_counter() - at) * 1000, 2))
                    counts.append(len(selects))
                finally:
                    event.remove(connection, "before_cursor_execute", query)
                chosen = [s for s in result if s.id in ids]
                assert (
                    len(chosen) == 40
                    and sum(s.device_count for s in chosen) == size
                    and all(s.health_score == 100 for s in chosen)
                )
            print(
                json.dumps(
                    dict(
                        sites=40,
                        devices=size,
                        site_summary_ms=timings,
                        selects=counts,
                        max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        transaction="rolled_back",
                        external_calls=0,
                        scope="Complete ORM query plus summary serialization; warm five-run process, excludes PostgreSQL RSS",
                    )
                )
            )
            db.rollback()


if __name__ == "__main__":
    main()
