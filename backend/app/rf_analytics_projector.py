"""Explicit projection command; separate transaction/process from collectors."""
import argparse,json
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.rf_analytics_projection import project_batch


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int,default=1000);args=parser.parse_args()
    if not settings.rf_analytics_projection_enabled:
        print(json.dumps({'status':'disabled','collection_affected':False}));return 1
    try:
        with SessionLocal() as db:
            result=project_batch(db,limit=args.limit);db.commit()
        print(json.dumps(result));return 0
    except Exception:
        print(json.dumps({'status':'projection_unavailable','collection_affected':False}));return 1


if __name__=='__main__':raise SystemExit(main())
