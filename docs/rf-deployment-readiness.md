# RF diagnostics deployment-readiness checklist (NOC-73P)

**Status: development-only. No production approval or rollout is implied by passing CI.**

## Prerequisites

1. Review stacked draft PRs #1–#17 in dependency order; do not merge or deploy automatically.
2. On a disposable PostgreSQL database, run `alembic upgrade head` and `alembic check`; verify migration `0005_rf_collection_runs` exists and the schema matches SQLAlchemy models.
3. Confirm existing UISP inventory sync works with `RF_COLLECTION_ENABLED=false`. The RF dashboard is read-only and does not issue radio commands.
4. Keep `UISP_VERIFY_TLS=true`; supply UISP credentials through secrets, never commits or logs. Confirm dashboard/API remain VPN-only and authenticated.
5. Review collection settings before any opt-in: `RF_COLLECTION_MIN_INTERVAL_SECONDS` 60–86400, `RF_COLLECTION_MAX_DEVICES` 1–10000. RF collection remains **disabled by default**.
6. RF history pruning remains **disabled by default**. If enabled later, choose retention 7–365 days and per-run deletion limit 1–1000; ensure backups and database capacity first.

## Staging acceptance tests

- Validate a real, sanitized UISP sample uses explicit RF units; unsupported radio fields must be skipped rather than guessed.
- Confirm inventory sync persists even if RF ingestion fails; verify the savepoint rolls back staged RF rows and records a sanitized failed run.
- Confirm successful RF collection creates a history record with received/staged/skipped counts.
- Verify authenticated endpoints: device diagnostics, collection health, history (limit 1–100), reliability (days 1–30); anonymous calls must be rejected.
- Verify fresh/stale/missing/future states, trend minimum sample requirements, and no-data dashboard messages.
- Test PostgreSQL JSON RF-tag filtering and UTC timestamps against the staging database. SQLite-only or mocked tests do not establish PostgreSQL behavior.
- Test retention in staging with expired and recent records, checking only expired rows are removed and max deletion is honored.
- Inspect UISP error handling for secret-bearing exceptions before enabling any production sync.

## Rollback

Turn `RF_COLLECTION_ENABLED=false` and `RF_HISTORY_PRUNING_ENABLED=false` to stop optional RF ingestion/pruning. Do not downgrade a live database or drop the RF history table without an approved backup and migration plan. Existing UISP inventory collection should continue independently.

## Release gate

Require green backend, frontend, compose-validation and database-migrations jobs **plus** staging PostgreSQL integration evidence and an explicit operator approval. No network or radio changes are part of this module.
