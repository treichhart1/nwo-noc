"""Explicit microwave poll CLI; no worker scheduler or activation API."""
import asyncio
import json
from datetime import datetime,timezone
from pydantic import Field
from sqlalchemy import select,text
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device,NetworkLink,AuditLog,RFCollectionRun
from app.radio_poll import private_json
from app.integrations.radio_snmp import SNMPPolicy,SNMPCredentials
from app.integrations.microwave_snmp import MicrowaveMapping,MicrowaveCollector
from app.integrations.read_only import IntegrationFailure
from app.services.microwave_model import Closed
from app.services.microwave_history import validate_association,stage_microwave


class MicrowavePollConfig(Closed):
    device_id: int = Field(ge=1,strict=True)
    network_link_id: int = Field(ge=1,strict=True)
    mapping: MicrowaveMapping
    policy: SNMPPolicy
    credentials_file: str = Field(repr=False,exclude=True)


async def poll_microwave(db,config,credentials,*,transport=None):
    if settings.offline_mode or not settings.rf_microwave_enabled or not settings.integrations_admin_approved:raise IntegrationFailure('disabled')
    device=db.get(Device,config.device_id);link=db.get(NetworkLink,config.network_link_id)
    if device is None or not device.enabled or not device.authorized or device.ip_address!=config.policy.address:raise IntegrationFailure('endpoint_denied')
    try:validate_association(device,link,config.mapping.profile)
    except ValueError:raise IntegrationFailure('configuration_invalid') from None
    config.policy.validate_connection()
    # Same lock namespace as radio_poll prevents concurrent SNMP polling of an endpoint.
    if db.get_bind().dialect.name=='postgresql' and not db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':871000+device.id}):raise IntegrationFailure('overlap_skipped')
    last=db.scalar(select(AuditLog).where(AuditLog.action.in_(['microwave.read_only_poll','rf.read_only_poll']),AuditLog.entity_id==str(device.id))
                   .order_by(AuditLog.created_at.desc()).limit(1))
    now=datetime.now(timezone.utc)
    if last:
        when=last.created_at.replace(tzinfo=timezone.utc) if last.created_at.tzinfo is None else last.created_at
        if (now-when).total_seconds()<config.policy.min_interval_seconds:raise IntegrationFailure('rate_limited')
    run=RFCollectionRun(started_at=now,status='failed',received=0,staged=0,skipped=0);db.add(run)
    try:
        obs=await MicrowaveCollector(config.mapping,config.policy,transport=transport).collect(credentials)
        with db.begin_nested():sample=stage_microwave(db,device,link,obs)
        run.received,run.staged,run.skipped=1,int(sample is not None),int(sample is None)
        code='success' if sample is not None else 'invalid_response';run.status='success' if sample is not None else 'failed'
    except IntegrationFailure as error:code=error.code
    except Exception:code='sync_failed'
    db.add(AuditLog(action='microwave.read_only_poll',entity_type='device',entity_id=str(device.id),created_at=now,
        details={'result':code,'profile':config.mapping.profile,'mapping_digest':config.mapping.digest,'network_link_id':link.id,'read_only':True}))
    db.flush()
    return {'status':run.status,'result':code,'staged':run.staged,'read_only':True,'device_id':device.id}


def main():
    try:
        if settings.offline_mode or not settings.rf_microwave_enabled or not settings.integrations_admin_approved:raise IntegrationFailure('disabled')
        config=MicrowavePollConfig.model_validate(private_json(settings.rf_microwave_config_file));config.policy.validate_connection()
        credentials=SNMPCredentials.model_validate(private_json(config.credentials_file))
        with SessionLocal() as db:
            result=asyncio.run(poll_microwave(db,config,credentials));db.commit()
        print(json.dumps(result));return 0 if result['status']=='success' else 1
    except Exception as error:
        print(json.dumps({'result':error.code if isinstance(error,IntegrationFailure) else 'configuration_invalid','read_only':True}));return 1


if __name__=='__main__':raise SystemExit(main())
