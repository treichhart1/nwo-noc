"""Explicit offline-only 400-device scenario for API/browser acceptance.

Stop the synthetic worker before outage/recovery scenarios so it cannot race the
controlled fixture. No network device action or vendor integration is invoked.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device, TelemetrySample
from app.services.synthetic_wisp import seed_network
from app.services.monitoring import run_due_checks
from app.services.incident_simulation import reconcile
from app.services.operational_readiness import snapshot


def main(action):
    if not settings.offline_mode or settings.app_env!='staging' or not settings.synthetic_monitoring_enabled:
        raise SystemExit('WISP rehearsal requires offline staging and explicit synthetic monitoring')
    if action not in {'seed','outage','recover'}:raise SystemExit('Use seed, outage, or recover')
    with SessionLocal() as db:
        if any(source!='synthetic' for source in db.scalars(select(Device.source))):
            raise SystemExit('WISP rehearsal refuses non-synthetic inventory')
        now=datetime.now(timezone.utc)
        if action=='seed':
            seed_network(db,devices=400,now=now);db.commit()
        else:
            devices=list(db.scalars(select(Device).order_by(Device.id)))
            if len(devices)!=400 or any(not d.external_id.startswith('noc85-') for d in devices):
                raise SystemExit('Expected the fresh NOC-85 400-device fixture')
            root,child=devices[:2]
            failed={d.monitor_host for d in devices if d.id==root.id or d.wisp_site_id==child.wisp_site_id}
            probe=lambda host,*args: (False,None,'Synthetic backhaul outage') if action=='outage' and host in failed else (True,1,None)
            for _ in range(2):
                db.execute(update(Device).values(monitor_last_checked_at=now-timedelta(seconds=16)))
                run_due_checks(db,now=now,probe=probe)
            degraded_rf=devices[2].id;capacity=devices[3].id
            rows=db.scalars(select(TelemetrySample).where(TelemetrySample.device_id.in_([degraded_rf,capacity])))
            for row in rows:
                row.raw_data={'rf_metrics_v1':dict(rssi_dbm=-90 if action=='outage' and row.device_id==degraded_rf else -60,
                    snr_db=8 if action=='outage' and row.device_id==degraded_rf else 30,
                    ccq_percent=40 if action=='outage' and row.device_id==degraded_rf else 95,
                    tx_rate_mbps=50 if action=='outage' and row.device_id==capacity else 400,rx_rate_mbps=400)}
            db.flush();reconcile(db,now=now);db.commit()
        view=snapshot(db,now=now)
        print(json.dumps({k:view[k] for k in ['total_devices','fresh_up','fresh_down','root_outages','suppressed_downstream','active_incidents','rf_degraded']},sort_keys=True))


if __name__=='__main__':main(sys.argv[1] if len(sys.argv)>1 else '')
