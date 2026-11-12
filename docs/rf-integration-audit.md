# NOC-74E — Cumulative RF integration audit

**Audit scope:** compare `audit/noc-foundation-2026-11` against
`feature/noc-74e-rf-integration-audit`. GitHub compare reports **83 commits
ahead, 0 behind**, with 46 changed files before this audit document.

## Cross-layer inventory verified in the cumulative comparison

- Data path: UISP adapter -> normalized metrics -> bounded collection ->
  telemetry persistence -> diagnostics/freshness/trends -> authenticated API ->
  RF dashboard.
- Collection: opt-in settings default disabled; nested savepoint isolates
  RF failure from UISP inventory; run history, reliability and optional pruning.
- Schema: `0005_rf_collection_runs.py` and `0006_rf_query_index.py`.
- CI: backend/frontend, migration drift, Compose validation, migrated disposable
  PostgreSQL integration, RF health benchmarks, staging preflight.
- Recovery tests: failed sync, interrupted insert, repeated sync without
  duplicate samples; read-only API and invalid-unit regressions.
- Prior successful full CI: #183 (NOC-74D).

## Release blockers and limits

1. **All RF work remains in stacked draft PRs**. The cumulative comparison
   verifies ancestry but does not replace human review or safe merge planning.
2. **No real UISP payload validation yet**: simulated explicit-unit keys are
   not proof that deployed radios expose those exact field names or units.
3. **No isolated staging deployment**: no operator-verified credentials,
   VPN-only access, migrations/backup, resource use, or rollback drill.
4. **No production approval**: do not enable RF collection or pruning,
   configure radios, or change network routing as part of this audit.

## Required acceptance

- Latest full CI green on the audit head, including migrated PostgreSQL RF tests.
- Human code/security review of the entire cumulative diff and all migrations.
- Operator approval for a separate staging deployment and actual UISP payload
  comparison; confirm collection remains off until explicitly authorized.
- Stage and observe first; record signoff and rollback evidence before any
  production observation.

**Conclusion:** Cumulative RF branch ancestry and coverage are documented;
the module is a candidate for controlled staging, **not production-ready**.
