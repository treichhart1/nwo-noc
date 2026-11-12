# NOC-75L — Consolidated RF security and code review

## Scope and evidence

Reviewed the 68-file main-to-NOC-75K comparison (131 commits ahead, 0 behind) and directly inspected RF API routes, authentication dependency, UISP ingestion, RF persistence, site aggregation, configuration, app initialization, RF validation tests, and migrations 0005–0006. Previous NOC-75K CI #245 passed. This is a focused code review, **not** a complete line-by-line security audit of all 68 files.

## Findings

### 1. Authorization scope — needs operator decision before release

All RF endpoints require `current_user` (active authenticated user), but no role-specific restriction is applied in `rf_routes.py`. Device RF diagnostics, site health, collection history and reliability are therefore available to any active authenticated NOC user. Confirm this matches the intended NOC permission model; if operators and read-only viewers should differ, add role-based checks and regression tests before integration. Authentication is present; fine-grained authorization is not demonstrated.

### 2. RF collection opt-in — guardrails present

`rf_collection_enabled` defaults to false; UISP collection is invoked only after an existing inventory sync and runs in a nested transaction/savepoint. Validation limits payload size and sample interval, stages only recognized enabled inventory devices, and does not commit independently. Exceptions preserve inventory sync while recording sanitized RF failure status. Actual live UISP payload compatibility remains unverified.

### 3. RF data and query safety — bounded, with caveats

Site summary is read-only, processes a maximum of 250 assigned enabled UISP radios, and uses one window-ranked 24-hour history query capped at 24 samples per device. Results are explicitly partial when truncated. An additional grouped latest-RF query is used for future-only classification. RF signal quality does not trigger reachability incidents. The 250-device cap may omit sites or devices in larger inventories; no complete-network aggregate should be inferred.

### 4. Schema migration and deployment risk

Migration 0005 creates RF collection history; 0006 adds a telemetry composite index. Both have explicit downgrade steps. The index migration may require a maintenance window on a large populated PostgreSQL telemetry table because a standard index build can lock writes. Verify table size, lock/statement timeout, rollback and maintenance strategy before production migrations. Successful disposable-database CI is not evidence of zero production impact.

### 5. API exposure and operational risk

The FastAPI app has documentation endpoints enabled at `/docs` and `/redoc` in the inspected code. Their reachability depends on deployment/network controls. Confirm VPN-only exposure and appropriate authentication/reverse-proxy restrictions; do not assume the source code alone enforces VPN-only access. Confirm CORS origins are restricted in deployment settings.

## Required before merge or deployment

- Operator approval of RF endpoint permission scope, with role tests if restrictions are required.
- Review complete diff and all PR review comments; this focused review does not certify the entire 68-file change set.
- Verify production migration locking/rollback plan and deployment environment controls.
- Run final full CI after changes, then perform authorized staging UISP validation before production.
- Keep all PRs draft/unmerged until explicitly approved.

**Disposition:** No verified critical vulnerability established by this review, but authorization scope and production migration/network exposure require resolution. Not production-ready.
