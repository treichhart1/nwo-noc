"""Seed a fresh, explicitly selected local disposable WISP database."""
import argparse
import json
import os
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from app.core.config import settings
from app.services.synthetic_wisp import seed_network

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--devices',type=int,default=400)
    args=parser.parse_args()
    url=os.environ.get('RF_INTEGRATION_TEST_DATABASE_URL')
    if not url or not settings.offline_mode or settings.app_env!='staging':
        raise SystemExit('Requires explicit disposable test database and offline staging')
    parsed=make_url(url)
    if parsed.host not in {'127.0.0.1','localhost'} or not (parsed.database=='nwo_noc' or parsed.database.startswith('noc85_')):
        raise SystemExit('Only a loopback disposable nwo_noc/noc85_* database is allowed')
    engine=create_engine(url)
    with Session(engine) as db:
        net=seed_network(db,devices=args.devices);db.commit()
    print(json.dumps(dict(sites=40,pops=4,towers=36,devices=args.devices,backhauls=39,vendors=5,simulation_only=True)))
