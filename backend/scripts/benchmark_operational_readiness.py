"""Reproducible measurements; injected probes perform ZERO network I/O."""
import json
import os
import statistics
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import Session
from app.core.config import settings
from app.services.synthetic_wisp import seed_network
from app.services.monitoring import run_due_checks
from app.services.operational_readiness import snapshot
from app.services.incident_simulation import reconcile


def benchmark(size):
    url=os.environ.get('RF_INTEGRATION_TEST_DATABASE_URL')
    if not url or not settings.offline_mode or settings.app_env!='staging':
        raise RuntimeError('Requires explicit disposable test database and offline staging')
    engine=create_engine(url)
    queries=[]
    def count(conn,cursor,statement,*args):
        if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
    with engine.connect() as conn:
        if conn.scalar(text('SELECT current_database()'))!='nwo_noc':raise RuntimeError('Disposable database must be named nwo_noc')
        conn.rollback();transaction=conn.begin()
        try:
            with Session(conn,join_transaction_mode='create_savepoint') as db:
                now=datetime.now(timezone.utc)
                net=seed_network(db,devices=size,now=now)
                event.listen(conn,'before_cursor_execute',count)
                timings=[]
                for _ in range(5):
                    start=time.perf_counter();view=snapshot(db,now=now);timings.append((time.perf_counter()-start)*1000)
                snapshot_queries=len(queries)//5;queries.clear()
                def failed(*args):
                    time.sleep(.001) # Simulated probe duration; never a socket call.
                    return False,None,'Synthetic timeout'
                start=time.perf_counter()
                first=run_due_checks(db,now=now+timedelta(seconds=15),probe=failed)
                second=run_due_checks(db,now=now+timedelta(seconds=30),probe=failed)
                polling_ms=(time.perf_counter()-start)*1000;polling_selects=len(queries);queries.clear()
                start=time.perf_counter();created=reconcile(db,now=now+timedelta(seconds=30));duplicate=reconcile(db,now=now+timedelta(seconds=30));alert_ms=(time.perf_counter()-start)*1000
                run_due_checks(db,now=now+timedelta(seconds=45),probe=lambda *a:(True,2,None))
                closed=reconcile(db,now=now+timedelta(seconds=45))
                isolated_hosts={d.monitor_host for d in net['devices'][-(size//10):]}
                for offset in [60,75]:
                    run_due_checks(db,now=now+timedelta(seconds=offset),probe=lambda host,*a:(False,None,'Synthetic isolated loss') if host in isolated_hosts else (True,2,None))
                start=time.perf_counter();isolated=reconcile(db,now=now+timedelta(seconds=75));isolated_ms=(time.perf_counter()-start)*1000
                assert isolated['created']==size//10
                assert duplicate['created']==0 and closed['resolved']==created['created']
                assert first['checked']==second['checked']==size
                return dict(devices=size,sites=40,snapshot_median_ms=round(statistics.median(timings),2),
                    snapshot_p95_ms=round(max(timings),2),snapshot_selects=snapshot_queries,
                    two_poll_batches_ms=round(polling_ms,2),polling_selects=polling_selects,
                    incident_reconcile_twice_ms=round(alert_ms,2),root_incidents=created['created'],
                    isolated_incidents=isolated["created"],isolated_incident_ms=round(isolated_ms,2),
                    duplicate_incidents=duplicate['created'],resolved_incidents=closed['resolved'],
                    virtual_detection_seconds=30,virtual_recovery_seconds=15,
                    probe_delay_ms=1,probe_threads=16,real_network_calls=0)
        finally:
            event.remove(conn,'before_cursor_execute',count)
            transaction.rollback()
    engine.dispose()


if __name__=='__main__':
    for size in [250,500,1000]:print(json.dumps(benchmark(size),sort_keys=True),flush=True)
