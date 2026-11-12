# NOC-74D — RF release-readiness review

**Decision: development complete for staged evaluation, NOT approved for deployment.**

## Evidence reviewed

- NOC-73A through NOC-74C implemented in stacked draft PRs.
- CI #180 for NOC-74C completed successfully, including frontend, backend,
  migration, PostgreSQL RF integration, and Compose checks.
- RF metrics require explicit unit-bearing keys; ambiguous values are skipped.
- Collection and history pruning are disabled by default.
- Authenticated, read-only diagnostics and RF operational endpoints.
- Bounded history queries, indexed device/sample lookups, freshness and trend checks.
- Disposable PostgreSQL tests for ingestion, failure isolation, interrupted writes,
  recovery, and repeat-sync duplicate prevention.
- Informational synthetic RF health benchmark at 250 and 1,000 radios.
- Staging preflight fails closed on production APP_ENV, enabled RF collection,
  enabled pruning, or disabled UISP TLS verification.

## Outstanding gates (NOT satisfied by CI)

1. Human review of the full stacked PR dependency chain, code, migration sequence,
   and secret/logging handling. Keep PRs draft and unmerged until approved.
2. Operator-approved, isolated Ubuntu 24.04 staging deployment and database backup.
3. Validate actual UISP payload shapes and units against observed radio values;
   the synthetic fixture cannot establish real-world accuracy.
4. Verify VPN-only dashboard, least-privilege read-only UISP token, certificate
   trust, firewall boundaries, and authenticated API access in staging.
5. Observe staging synchronization, inventory continuity, RF sample freshness,
   history counts, database growth and restart/rollback behavior.
6. Record explicit operator signoff before any limited production observation.
   Never send configuration changes to radios as part of this module.

## Rollback

Disable RF_COLLECTION_ENABLED and RF_HISTORY_PRUNING_ENABLED, then restart
using the approved change process. Keep data intact for investigation unless
retention/deletion is separately approved. Restore a backed-up application
release if inventory sync or RF accuracy regresses; do not blindly downgrade
database migrations.

## Scope

This review does not deploy software, merge PRs, enable collectors, or validate
real hardware. A passing CI run is evidence of automated test coverage only.
