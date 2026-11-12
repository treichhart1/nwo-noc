# NOC-73X — Non-production RF staging validation

**No production deployment is included.** All CI RF integration tests use
simulated UISP HTTP responses and a disposable migrated PostgreSQL service.

## Automated staging acceptance

- Full simulated UISP -> inventory -> normalized RF sample -> diagnostics API
  is tested on PostgreSQL with authenticated and unauthenticated requests.
- A second simulated UISP synchronization within the minimum collection
  interval must not duplicate RF telemetry samples.
- Ambiguous, unitless RF values are skipped; RF collection is disabled by
  default and its batch size is bounded.
- RF staging failures do not abort inventory sync or claim rolled-back samples
  were successfully stored.
- The existing CI runs database migrations, backend/frontend checks, and
  local-only Compose port validation.

## Required before any real staged deployment

1. Obtain operator authorization for a dedicated staging instance and
   environment-specific, read-only UISP credentials.
2. Confirm all stacked PRs have been reviewed and that staging database
   migrations are backed up and recoverable.
3. Verify VPN-only access, TLS validation, secret handling and least privilege.
4. Start with RF_COLLECTION_ENABLED=false and RF_HISTORY_PRUNING_ENABLED=false.
5. Compare a small approved set of read-only UISP RF measurements against
   stored normalized values, and verify freshness, retention and error logs.
6. Explicitly authorize enabling collection and set conservative batch/interval
   bounds. Confirm no radio configuration or remediation commands are present.
7. Record performance and rollback results before considering production.

CI success is necessary but **not** sufficient for deployment approval.
