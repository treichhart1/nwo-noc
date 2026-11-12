# NOC-74F — Controlled RF staging rollout (operator-run only)

**Status:** Preparation only. This document does not authorize deployment,
production UISP access, or enabling RF collection.

## Before touching a staging host

1. Obtain explicit operator approval and an isolated Ubuntu 24.04 host/VM.
   Use a staging-only database and credentials. Keep PRs draft and unmerged
   until reviewed; choose an approved immutable commit to test.
2. Review all stacked RF PRs, database migrations, and the final audit.
   Verify the chosen commit's full CI is green.
3. Restrict dashboard access to the authenticated VPN/reverse proxy.
   Confirm host firewall and bind address; never expose Docker ports publicly.
4. Copy `.env.example` to `.env` **on the staging host only**; supply
   unique staging secrets outside Git. Set `APP_ENV=staging`,
   `RF_COLLECTION_ENABLED=false`,
   `RF_HISTORY_PRUNING_ENABLED=false`,
   `UISP_VERIFY_TLS=true`, `BIND_ADDRESS=127.0.0.1`.
   **Leave `UISP_TOKEN` empty and `UISP_BASE_URL` unset** for the
   initial offline staging pass; the worker may otherwise attempt UISP sync.
5. From `backend/` on the staging checkout, run
   `python scripts/rf_staging_preflight.py` using the intended environment
   variables. Require exit code 0 and review the printed JSON.
6. Before starting any containers, inspect `docker compose config` and
   verify frontend/backend published host IPs are `127.0.0.1`.
   Do not use `docker compose down -v` on a database with data.

## Isolated validation (requires operator execution)

- Back up the staging PostgreSQL volume/database before migrations.
- Run migration checks against staging and record schema revision; confirm
  no production database hostname, password or IP is configured.
- Start only the approved staging stack after confirming the worker cannot
  reach a production UISP instance.
- Confirm authenticated RF diagnostics, health, history, reliability routes;
  verify anonymous requests return 401.
- Confirm the RF dashboard handles no-data, stale and missing measurements.
- Verify RF collection and pruning remain disabled, and inspect logs for
  unexpected UISP requests or secrets.
- Only after separate authorization, use a sanitized UISP fixture or a
  dedicated read-only staging UISP endpoint to compare unit-bearing metrics.
- Record collection freshness, database load, restart recovery, and rollback
  evidence. Do not issue any radio configuration commands.

## Rollback and exit criteria

- Keep RF collection and pruning disabled; stop the staging worker if needed.
- Revert application to the previously approved commit/image after backup.
- Preserve RF history and database state for diagnosis. Never blindly
  downgrade a database or remove volumes.
- Exit only after operator signoff with evidence of VPN isolation, safe
  migration, correct units, stable inventory sync and successful recovery.

## Explicit limits

CI tests use simulated UISP responses and disposable PostgreSQL. They do not
prove that actual radio models expose the same fields, or that this release
has been installed and tested on the user's server.
