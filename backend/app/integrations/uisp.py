from datetime import datetime, timezone
import logging
from sqlalchemy import select, text
from app.core.config import settings
from app.models.entities import Integration, RFCollectionRun, AuditLog
from app.integrations.uisp_rf_collection import stage_uisp_rf_batch
from app.services.rf_collection_reliability import prune_rf_collection_history
from app.integrations.read_only import ReadClient,ReadPolicy,UISP_PATHS,IntegrationFailure,secret
from app.integrations.normalization import parse_dt
from app.integrations.imports import import_inventory
log=logging.getLogger(__name__)


def get_integration(db):
    state=db.scalar(select(Integration).where(Integration.name=='uisp'))
    if state is None:
        state=Integration(name='uisp');db.add(state);db.flush()
    return state


def fetch_snapshot(client):
    result={'devices':client.pages('/nms/api/v2.1/devices'),'errors':[]}
    for resource in ('sites','links','sectors'):
        try:result[resource]=client.pages('/nms/api/v2.1/'+resource)
        except IntegrationFailure as exc:
            if exc.code=='authentication_failed':raise
            result[resource]=[];result['errors'].append(exc.code)
    return result


def sync_uisp(db,*,transport=None,now=None,progress=None):
    now=now or datetime.now(timezone.utc)
    if settings.offline_mode or not settings.uisp_enabled or not settings.integrations_admin_approved:
        raise IntegrationFailure('disabled')
    if not settings.uisp_verify_tls or not secret(settings.uisp_token):raise IntegrationFailure('configuration_invalid')
    policy=ReadPolicy(settings.uisp_base_url,tuple(settings.uisp_allowed_origins.split(',')),UISP_PATHS,True,True)
    policy.validate()
    state=get_integration(db)
    try:
        if db.get_bind().dialect.name=='postgresql':
            if not db.scalar(text('SELECT pg_try_advisory_xact_lock(860001)')):raise IntegrationFailure('overlap_skipped')
        state.last_attempt_at=now
        with ReadClient(policy,headers={'x-auth-token':secret(settings.uisp_token)},transport=transport) as client:
            snapshot=fetch_snapshot(client)
        payload=snapshot['devices']
        if any(snapshot.get(key) for key in ('devices','sites','links','sectors')):
            count,skipped,conflicts=import_inventory(db,snapshot,now,(settings.uisp_token,),progress=progress)
        else:
            count=skipped=conflicts=0
        rf_result = None
        rf_failed = False
        if settings.rf_collection_enabled:
            # Inventory is flushed first so RF work can be rolled back
            # independently using a database savepoint.
            db.flush()
            try:
                with db.begin_nested():
                    rf_result = stage_uisp_rf_batch(
                        db, payload,
                        min_interval_seconds=settings.rf_collection_min_interval_seconds,
                        max_devices=settings.rf_collection_max_devices,
                    )
                    db.add(RFCollectionRun(
                        started_at=datetime.now(timezone.utc), status="success",
                        received=rf_result["received"], staged=rf_result["staged"],
                        skipped=rf_result["skipped"],
                    ))
                    if settings.rf_history_pruning_enabled:
                        prune_rf_collection_history(
                            db,
                            retention_days=settings.rf_history_retention_days,
                            max_delete=settings.rf_history_max_delete_per_run,
                        )
            except Exception:
                # An invalid RF batch/configuration must not abort the
                # independent UISP inventory refresh. No secrets in logs.
                rf_failed = True
                rf_result = None  # The savepoint rolled back any staged RF samples.
                log.warning("RF collection failed; UISP inventory sync continues")
                db.add(RFCollectionRun(
                    started_at=datetime.now(timezone.utc), status="failed",
                    received=0, staged=0, skipped=0,
                ))
        state.status='degraded' if skipped or conflicts or snapshot.get('errors') or rf_failed else 'connected'
        state.last_error='partial_sync' if state.status=='degraded' else None
        state.last_sync_at=now
        if state.status=='connected':state.last_success_at=now
        state.last_item_count=count;state.last_received_count=len(payload);state.last_skipped_count=skipped+conflicts
        db.add(AuditLog(action='integration.uisp.read_sync',entity_type='integration',entity_id='uisp',
            details={'imported':count,'skipped':skipped,'conflicts':conflicts,'read_only':True}))
        db.commit()
        result={'ok':True,'imported':count}
        if rf_result is not None:result['rf_collection']=rf_result
        elif rf_failed:result['rf_collection']={'ok':False,'status':'failed'}
        return result
    except Exception as exc:
        code=exc.code if isinstance(exc,IntegrationFailure) else 'sync_failed'
        db.rollback();state=get_integration(db)
        state.status='error';state.last_attempt_at=now;state.last_error=code
        if settings.rf_collection_enabled:db.add(RFCollectionRun(started_at=now,status='failed',received=0,staged=0,skipped=0))
        db.commit()
        raise IntegrationFailure(code) from None
