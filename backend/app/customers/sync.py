"""Atomic, bounded export reconciliation; never writes to the provider."""

from datetime import datetime, timezone
from sqlalchemy import select, text
from app.customers.models import (
    CustomerAccount,
    CustomerService,
    CustomerSyncState,
    CustomerSyncRun,
)
from app.customers.providers import ProviderFailure
from app.models.entities import AuditLog


def utc(value):
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def unique(rows):
    found = {}
    for row in rows:
        if row.external_id in found and row != found[row.external_id]:
            raise ProviderFailure("conflicting_duplicate")
        found[row.external_id] = row
    return found


def synchronize(db, provider, *, user_id=None):
    """Caller owns transaction. Provider failures retain old data/cursor atomically.

    Includes a SAVEPOINT so failed imports can still commit static health/audit.
    PostgreSQL transaction advisory lock covers fetch and reconciliation.
    """
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(950001)")):
        raise ProviderFailure("overlap_skipped")
    now = datetime.now(timezone.utc)
    state = db.get(CustomerSyncState, "powercode")
    if state is None:
        state = CustomerSyncState(provider="powercode")
        db.add(state)
        db.flush()
    state.last_attempt_at = now
    mode = "unknown"
    received = 0
    reconciled = 0
    try:
        with db.begin_nested():
            batch = provider.fetch(cursor=state.cursor)
            mode = batch.mode
            if not batch.complete:
                raise ProviderFailure("incomplete_snapshot")
            # No cursor rewind, skipping changes or destructive stale full snapshots.
            if (
                state.last_snapshot_id == batch.snapshot_id
                and state.cursor == batch.cursor
            ):
                return {"status": "already_applied", "received": 0, "reconciled": 0}
            if batch.mode == "incremental" and (
                not state.cursor or batch.previous_cursor != state.cursor
            ):
                raise ProviderFailure("cursor_conflict")
            if state.watermark and batch.as_of <= utc(state.watermark):
                raise ProviderFailure("stale_snapshot")
            accounts = unique(batch.accounts)
            services = unique(batch.services)
            received = len(accounts) + len(services)
            existing_accounts = {
                a.external_id: a
                for a in db.scalars(select(CustomerAccount).limit(50001))
            }
            existing_services = {
                s.external_id: s
                for s in db.scalars(select(CustomerService).limit(100001))
            }
            if (
                len(set(existing_accounts) | set(accounts)) > 50000
                or len(set(existing_services) | set(services)) > 100000
            ):
                raise ProviderFailure("unavailable")
            for key, record in accounts.items():
                row = existing_accounts.get(key)
                if row is None:
                    row = CustomerAccount(
                        external_id=key,
                        source_updated_at=record.updated_at,
                        last_synced_at=now,
                    )
                    db.add(row)
                    existing_accounts[key] = row
                elif (
                    row.source_deleted_at
                    and state.watermark
                    and record.updated_at <= utc(state.watermark)
                ):
                    continue
                elif record.updated_at < utc(row.source_updated_at):
                    continue
                elif (
                    record.updated_at == utc(row.source_updated_at)
                    and row.source_deleted_at is not None
                    and not record.deleted
                ):
                    continue
                if (
                    row.id is not None
                    and row.source_deleted_at is None
                    and not record.deleted
                    and record.updated_at == utc(row.source_updated_at)
                    and any(
                        getattr(row, k) != getattr(record, k)
                        for k in ("name", "phone", "email")
                    )
                ):
                    raise ProviderFailure("conflicting_duplicate")
                if row.phone != record.phone:
                    row.sms_consent = "unknown"
                    row.consent_record = None
                row.name = None if record.deleted else record.name
                row.phone = None if record.deleted else record.phone
                row.email = None if record.deleted else record.email
                row.source_updated_at = record.updated_at
                row.last_synced_at = now
                row.source_deleted_at = now if record.deleted else None
                if record.deleted:
                    row.sms_consent = "unknown"
                    row.sms_opt_out = True
                    row.consent_record = None
            db.flush()
            for key, record in services.items():
                account = existing_accounts.get(record.account_id)
                if (
                    account is None
                    or (batch.mode == "full" and record.account_id not in accounts)
                    or account.source_deleted_at
                    and not record.deleted
                ):
                    raise ProviderFailure("invalid_reference")
                row = existing_services.get(key)
                if row is None:
                    row = CustomerService(
                        external_id=key,
                        customer_id=account.id,
                        source_updated_at=record.updated_at,
                        last_synced_at=now,
                    )
                    db.add(row)
                    existing_services[key] = row
                elif row.customer_id != account.id:
                    # Never silently move one service ID between accounts.
                    raise ProviderFailure("invalid_reference")
                elif (
                    row.source_deleted_at
                    and state.watermark
                    and record.updated_at <= utc(state.watermark)
                ):
                    continue
                elif record.updated_at < utc(row.source_updated_at):
                    continue
                elif (
                    record.updated_at == utc(row.source_updated_at)
                    and row.source_deleted_at
                    and not record.deleted
                ):
                    continue
                if (
                    row.id is not None
                    and row.source_deleted_at is None
                    and not record.deleted
                    and record.updated_at == utc(row.source_updated_at)
                    and any(
                        getattr(row, k) != getattr(record, k)
                        for k in (
                            "status",
                            "service_type",
                            "plan",
                            "download_mbps",
                            "upload_mbps",
                            "address",
                            "equipment_id",
                            "source_refs",
                        )
                    )
                ):
                    raise ProviderFailure("conflicting_duplicate")
                if row.mapping_state == "verified" and (
                    row.source_refs != record.source_refs
                    or row.service_type != record.service_type
                ):
                    row.mapping_state = "review_required"
                for field in (
                    "service_type",
                    "plan",
                    "download_mbps",
                    "upload_mbps",
                    "address",
                    "equipment_id",
                    "source_refs",
                ):
                    setattr(row, field, getattr(record, field))
                row.status = "disconnected" if record.deleted else record.status
                row.source_deleted_at = now if record.deleted else None
                row.source_updated_at = record.updated_at
                row.last_synced_at = now
                if record.deleted:
                    row.address = None
                    row.equipment_id = None
                    row.source_refs = {}
            # Full only means all accounts AND services. Missing is a tombstone,
            # not physical deletion. Partial/cursor failures cannot reach this point.
            deleted_account_ids = {
                a.id for a in existing_accounts.values() if a.source_deleted_at
            }
            for key, row in existing_services.items():
                if (
                    batch.mode == "full" and key not in services
                ) or row.customer_id in deleted_account_ids:
                    if row.source_deleted_at is None:
                        reconciled += 1
                    row.status = "disconnected"
                    row.source_deleted_at = now
                    row.address = None
                    row.equipment_id = None
                    row.source_refs = {}
            if batch.mode == "full":
                for key, row in existing_accounts.items():
                    if key not in accounts:
                        row.source_deleted_at = now
                        row.name = None
                        row.phone = None
                        row.email = None
                        row.sms_consent = "unknown"
                        row.sms_opt_out = True
                        row.consent_record = None
            state.status = "healthy"
            state.cursor = batch.cursor
            state.watermark = batch.as_of
            state.last_success_at = now
            state.last_error = None
            state.last_count = received
            state.last_snapshot_id = batch.snapshot_id
            db.flush()
    except ProviderFailure as error:
        state.status = "failed"
        state.last_error = error.code
        db.add(
            CustomerSyncRun(
                mode=mode,
                status="failed",
                error_code=error.code,
                received=0,
                reconciled=0,
                started_at=now,
                ended_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            AuditLog(
                user_id=user_id,
                action="customer.sync_failed",
                entity_type="customer_sync",
                entity_id="powercode",
                details={"error_code": error.code},
            )
        )
        db.flush()
        return {
            "status": "failed",
            "error_code": error.code,
            "received": 0,
            "reconciled": 0,
        }
    db.add(
        CustomerSyncRun(
            mode=mode,
            status="healthy",
            received=received,
            reconciled=reconciled,
            started_at=now,
            ended_at=datetime.now(timezone.utc),
        )
    )
    db.add(
        AuditLog(
            user_id=user_id,
            action="customer.synchronized",
            entity_type="customer_sync",
            entity_id="powercode",
            details={
                "mode": mode,
                "received": received,
                "reconciled": reconciled,
                "read_only": True,
            },
        )
    )
    db.flush()
    return {"status": "healthy", "received": received, "reconciled": reconciled}
