"""Real worker process + disposable PostgreSQL/Redis; no live device probes.

Creates new uniquely named test databases and retains them. Never drops data.
"""
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from redis import Redis
from sqlalchemy import create_engine, text, select, func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from app.models.entities import Device
from app.services.synthetic_wisp import seed_network
from app.core.config import settings


def run(size):
    url=os.environ.get('RF_INTEGRATION_TEST_DATABASE_URL')
    if not url or not settings.offline_mode or settings.app_env!='staging':
        raise RuntimeError('Requires explicit disposable PostgreSQL and offline staging')
    parsed=make_url(url)
    if parsed.database!='nwo_noc' or parsed.host not in {'127.0.0.1','localhost'}:
        raise RuntimeError('Worker rehearsal requires a loopback disposable nwo_noc database')
    name=f'noc85_worker_{size}_{secrets.token_hex(4)}'
    admin=create_engine(url,isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    target=parsed.set(database=name).render_as_string(hide_password=False)
    redis_url=os.environ.get('NOC85_TEST_REDIS_URL','redis://127.0.0.1:6379/0')
    redis_parsed=make_url(redis_url)
    if redis_parsed.host not in {'127.0.0.1','localhost'}:raise RuntimeError('Disposable Redis must be loopback')
    env=dict(os.environ,DATABASE_URL=target,REDIS_URL=redis_url,SYNTHETIC_MONITORING_ENABLED='true',
        UISP_BASE_URL='',UISP_TOKEN='',APP_ENV='staging',OFFLINE_MODE='true')
    subprocess.run([sys.executable,'-m','alembic','upgrade','head'],env=env,check=True,stdout=subprocess.DEVNULL)
    engine=create_engine(target,pool_pre_ping=True)
    with Session(engine) as db:
        seed_network(db,devices=size,now=datetime.now(timezone.utc)-timedelta(seconds=20));db.commit()
    redis=Redis.from_url(redis_url,socket_timeout=3)
    durations=[]
    for cycle in range(2):
        started=datetime.now(timezone.utc);wall=time.perf_counter()
        process=subprocess.Popen([sys.executable,'-m','app.worker'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for attempt in range(80):
                if process.poll() is not None:raise RuntimeError('Worker exited unexpectedly')
                with Session(engine) as db:
                    count=db.scalar(select(func.count()).select_from(Device).where(Device.monitor_status=='up',Device.monitor_last_checked_at>=started))
                heartbeat=redis.get('noc:worker:heartbeat')
                if count==size and heartbeat is not None and float(heartbeat)>=started.timestamp():break
                time.sleep(.5)
            else:raise RuntimeError(f'Worker did not poll {size} devices after start/restart')
            durations.append(round((time.perf_counter()-wall)*1000,2))
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    with Session(engine) as db:
        last=db.scalar(select(func.max(Device.monitor_last_up_at)))
        assert last>=started
    engine.dispose()
    return dict(devices=size,worker_initial_poll_ms=durations[0],worker_restart_poll_ms=durations[1],
        synthetic_probe=True,real_device_network_calls=0,database=name)


if __name__=='__main__':
    for size in [250,500,1000]:print(json.dumps(run(size),sort_keys=True),flush=True)
