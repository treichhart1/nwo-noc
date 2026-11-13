"""Dry-run default. Applying retention is restricted to offline synthetic staging."""
import argparse,json
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,func,delete,text
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device,RFAnalyticsPoint,AuditLog


def retention(db,*,apply=False,now=None):
    now=now or datetime.now(timezone.utc);cutoff=now-timedelta(days=settings.rf_analytics_retention_days)
    ids=list(db.scalars(select(RFAnalyticsPoint.id).where(RFAnalyticsPoint.sampled_at<cutoff).order_by(RFAnalyticsPoint.sampled_at,RFAnalyticsPoint.id).limit(settings.rf_analytics_retention_batch)))
    if apply:
        if not settings.rf_analytics_retention_enabled or not settings.offline_mode or settings.app_env!='staging':raise ValueError('Retention apply requires explicit offline synthetic staging')
        if db.scalar(select(Device.id).where(Device.source!='synthetic').limit(1)):raise ValueError('Retention refuses nonsynthetic inventory')
        if not db.scalar(text('SELECT pg_try_advisory_xact_lock(890001)')):raise ValueError('Projection/retention overlap')
        if ids:db.execute(delete(RFAnalyticsPoint).where(RFAnalyticsPoint.id.in_(ids)))
        db.add(AuditLog(action='rf.analytics_retention',entity_type='rf_analytics_points',created_at=now,
            details={'cutoff':cutoff.isoformat(),'deleted':len(ids),'scope':'analytics_only','synthetic_only':True}))
        db.flush()
    return {'dry_run':not apply,'eligible_batch':len(ids),'deleted':len(ids) if apply else 0,'cutoff':cutoff.isoformat(),
        'scope':'analytics_projection_only','source_telemetry_deleted':False,'batch_limit':settings.rf_analytics_retention_batch}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    try:
        with SessionLocal() as db:
            result=retention(db,apply=args.apply)
            if args.apply:db.commit()
        print(json.dumps(result));return 0
    except Exception:
        print(json.dumps({'status':'retention_unavailable_or_not_authorized','source_telemetry_deleted':False}));return 1


if __name__=='__main__':raise SystemExit(main())
