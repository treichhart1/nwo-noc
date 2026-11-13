"""Credential-free integration readiness view; does not initiate connections."""
from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from app.models.entities import Integration
from app.core.config import settings
from app.integrations.read_only import ReadPolicy,UISP_PATHS,MIKROTIK_PATHS,IntegrationFailure,ERROR_CODES,secret


def integration_status(db,*,now=None):
    now=now or datetime.now(timezone.utc)
    rows={row.name:row for row in db.scalars(select(Integration))}
    result=[]
    for name,paths in [('uisp',UISP_PATHS),('mikrotik',MIKROTIK_PATHS)]:
        row=rows.get(name);flag=getattr(settings,name+'_enabled')
        credential=secret(settings.uisp_token if name=='uisp' else settings.mikrotik_password)
        base=getattr(settings,name+'_base_url');allowed=getattr(settings,name+'_allowed_origins')
        configured=bool(credential and base and allowed and (name!='mikrotik' or (settings.mikrotik_username and settings.mikrotik_device_id)))
        validation=None
        if configured:
            try:ReadPolicy(base,tuple(allowed.split(',')),paths,True,True).validate()
            except IntegrationFailure as exc:configured=False;validation=exc.code
        enabled=bool(flag and configured and settings.integrations_admin_approved and not settings.offline_mode)
        success=row.last_success_at if row else None
        interval=max(getattr(settings,name+'_sync_interval_seconds'),60)
        stale=bool(enabled and (success is None or success>now or success<now-timedelta(seconds=2*interval)))
        error=row.last_error if row and row.last_error in ERROR_CODES else 'sync_failed' if row and row.last_error else None
        result.append(dict(name=name,configured=configured,enabled=enabled,read_only=True,
            state='disabled' if not enabled else 'stale' if stale and row and row.status=='connected' else row.status if row else 'not_synchronized',
            last_attempt_at=row.last_attempt_at if row else None,last_success_at=success,
            imported_count=row.last_item_count if row else 0,received_count=row.last_received_count if row else 0,
            skipped_count=row.last_skipped_count if row else 0,error_code=error,validation_error=validation,
            stale=stale,activation_requires_administrator=True))
    return result
