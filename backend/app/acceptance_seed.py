"""Explicit synthetic fixture seed, restricted to isolated offline acceptance."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import Device, Site, User, TelemetrySample


def main():
    if not settings.offline_mode or settings.app_env != 'staging':
        raise SystemExit('Synthetic seed requires offline staging')
    with SessionLocal() as db:
        if db.scalar(select(Device).where(Device.external_id == 'noc84-synthetic')):
            return
        site = Site(name='NOC84 Synthetic Tower', latitude=41, longitude=-84)
        db.add(site); db.flush()
        device = Device(source='synthetic', external_id='noc84-synthetic', name='NOC84 Synthetic Radio',
            wisp_site_id=site.id, enabled=True, monitor_enabled=True, monitor_host='redis',
            monitor_port=6379, monitor_interval_seconds=15, status='active')
        db.add(device)
        db.add(User(username='acceptance-viewer', password_hash=hash_password(settings.admin_password),
                    email='viewer@example.invalid', role='viewer', is_active=True))
        db.flush()
        now = datetime.now(timezone.utc)
        for minute in [1, 2, 3]:
            db.add(TelemetrySample(device_id=device.id, sampled_at=now-timedelta(minutes=minute),
                raw_data={'rf_metrics_v1': {'rssi_dbm': -65, 'snr_db': 10, 'ccq_percent': 45}}))
        db.commit()


if __name__ == '__main__':
    main()
