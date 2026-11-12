# NOC-75H — RF integration review

## Verified GitHub checks

NOC-75G head `22286c8`: CI run #233 passed, including backend, rf-postgres-integration, database-migrations, compose-validation, and frontend jobs.

## Integrated feature coverage

- Opt-in UISP RF ingestion and normalized persisted RF samples
- Read-only device RF diagnostics: freshness, degradation, trends
- Authenticated collection health and history
- Dashboard RF overview, device drawer, and site/POP summary
- Site-level bulk window-ranked telemetry reads, 24 samples per device over 24 hours
- Site summary max 250 assigned enabled UISP devices; partial result indicator
- PostgreSQL regression coverage for stale/fresh/missing/future/degraded, 260-device cap, query budget and device/site parity

## Safety invariants

RF measurements are informational, not evidence of reachability failure. The site aggregation does not write to the database, change radio configuration, trigger collection, or create incidents. UI data is authenticated and the radio inventory remains source-of-truth for reachability.

## Remaining release gates

1. Draft stacked PRs must undergo human review and consolidation; they remain unmerged.
2. CI verifies synthetic/disposable database behavior, not a real UISP environment.
3. Live RF sample validity, device-to-site assignment accuracy, dashboard latency, permissions, and rollback must be validated before production use.
4. The site summary intentionally returns only the first 250 assigned enabled UISP devices; it is not a complete network-wide summary beyond that cap.
5. Re-run full CI after any consolidation or merge conflict resolution.

**Status: code integration checks passed; production readiness not established.**
