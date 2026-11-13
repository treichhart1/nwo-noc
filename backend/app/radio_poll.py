"""Explicit single-device RF poll. Not scheduled by the worker or API."""
import asyncio
import json
import os
import stat
from pydantic import BaseModel, ConfigDict, Field
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import Device, RFCollectionRun
from app.integrations.read_only import IntegrationFailure
from app.integrations.radio_snmp import MIBMapping, SNMPPolicy, SNMPCredentials, RadioCollector
from app.services.radio_history import stage_radio


def private_json(path,*,max_bytes=65536):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid() or info.st_size > max_bytes:
                raise ValueError()
            return json.loads(handle.read(max_bytes+1))
    except Exception: raise IntegrationFailure('configuration_invalid') from None


class PollConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    device_id: int = Field(ge=1, strict=True)
    mapping: MIBMapping
    policy: SNMPPolicy
    credentials_file: str = Field(repr=False, exclude=True)


async def poll(db, config, credentials, *, transport=None):
    if settings.offline_mode or not settings.rf_snmp_enabled or not settings.integrations_admin_approved:
        raise IntegrationFailure('disabled')
    device = db.get(Device, config.device_id)
    if device is None or not device.enabled or not device.authorized or device.ip_address != config.policy.address:
        raise IntegrationFailure('endpoint_denied')
    vendor = (device.vendor or '').casefold()
    from app.services.radio_observation import PROFILES
    aliases = {'ubiquiti': {'ubiquiti', 'ubiquiti networks', 'ubnt'}, 'cambium': {'cambium', 'cambium networks'}}
    if vendor not in aliases[PROFILES[config.mapping.profile]]: raise IntegrationFailure('configuration_invalid')
    config.policy.validate_connection()
    # Cross-process pacing is persisted even on failed polls. A per-device lock
    # prevents overlapping invocations; transaction is owned by this CLI.
    from sqlalchemy import select, text
    from datetime import datetime, timezone
    from app.models.entities import AuditLog
    if db.get_bind().dialect.name == 'postgresql':
        if not db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': 871000 + device.id}):
            raise IntegrationFailure('overlap_skipped')
    last = db.scalar(select(AuditLog).where(AuditLog.action == 'rf.read_only_poll', AuditLog.entity_id == str(device.id))
                     .order_by(AuditLog.created_at.desc()).limit(1))
    from app.models.entities import MonitoringAttempt,MonitoringJob
    scheduled=db.scalar(select(MonitoringAttempt.started_at).join(MonitoringJob,MonitoringJob.key==MonitoringAttempt.job_key)
        .where(MonitoringJob.device_id==device.id,MonitoringJob.kind.in_(('radio','microwave'))).order_by(MonitoringAttempt.started_at.desc()).limit(1))
    now = datetime.now(timezone.utc)
    if scheduled and (now-(scheduled.replace(tzinfo=timezone.utc) if scheduled.tzinfo is None else scheduled)).total_seconds()<config.policy.min_interval_seconds:raise IntegrationFailure('rate_limited')
    if last:
        when = last.created_at.replace(tzinfo=timezone.utc) if last.created_at.tzinfo is None else last.created_at
        if (now-when).total_seconds() < config.policy.min_interval_seconds: raise IntegrationFailure('rate_limited')
    run = RFCollectionRun(started_at=now, status='failed', received=0, staged=0, skipped=0)
    db.add(run)
    code = None
    try:
        observation = await RadioCollector(config.mapping, config.policy, transport=transport).collect(credentials)
        with db.begin_nested(): sample = stage_radio(db, device, observation)
        run.received, run.staged, run.skipped = 1, int(sample is not None), int(sample is None)
        code = 'success' if sample is not None else 'invalid_response'
        run.status = 'success' if sample is not None else 'failed'
    except IntegrationFailure as error: code = error.code
    except Exception: code = 'sync_failed'
    db.add(AuditLog(action='rf.read_only_poll', entity_type='device', entity_id=str(device.id),
        created_at=now, details={'result': code, 'profile': config.mapping.profile,
                               'mapping_digest': config.mapping.digest, 'read_only': True}))
    db.flush()
    return {'status': run.status, 'result': code, 'staged': run.staged, 'device_id': device.id, 'read_only': True}


def main():
    try:
        if settings.offline_mode or not settings.rf_snmp_enabled or not settings.integrations_admin_approved:
            raise IntegrationFailure('disabled')
        config = PollConfig.model_validate(private_json(settings.rf_snmp_config_file))
        config.policy.validate_connection()
        credentials = SNMPCredentials.model_validate(private_json(config.credentials_file))
        with SessionLocal() as db:
            result = asyncio.run(poll(db, config, credentials)); db.commit()
        print(json.dumps(result))
        return 0 if result['status'] == 'success' else 1
    except Exception as error:
        print(json.dumps({'result': error.code if isinstance(error, IntegrationFailure) else 'configuration_invalid', 'read_only': True}))
        return 1


if __name__ == '__main__': raise SystemExit(main())
