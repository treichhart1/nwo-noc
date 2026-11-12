# NWO NOC Foundation Audit — 2026-11-12

## Scope
Read-only inspection of `main`, `develop`, and `feature/github-actions`, plus selected configuration and application files. This is an initial audit, **not** a certification that all source files or tests have been reviewed.

## Branch comparison
- `develop` is 3 commits ahead of `main`, 0 behind.
- `feature/github-actions` is also 3 commits ahead of `main`, 0 behind.
- Both comparisons show a changed CI workflow and an added frontend package lock. Their exact commit identities and equivalence require verification.
- Continue development from `develop` without merging into `main`.

## Verified implementation
- Backend: FastAPI, SQLAlchemy, Alembic, PostgreSQL configuration.
- Frontend: React, TypeScript, Vite; build includes typechecking.
- Worker: 15-second loop invokes due monitoring checks and optional UISP synchronization.
- Monitoring: TCP connection probes, two-failure down threshold, device events, 30-day check retention.
- CI: frontend install/build and backend compilation/pytest.
- Docker Compose: database, Redis, backend, worker, frontend.

## Risks and follow-up verification
1. **High: TLS validation defaults off for UISP** (`backend/app/core/config.py`, `uisp_verify_tls=False`). Review deployment configuration and make secure validation the default with tests.
2. **High: published service ports** (`docker-compose.yml`): backend and frontend ports bind all interfaces by default. Define VPN/reverse-proxy-only exposure and verify firewall/network controls before deployment.
3. **Medium: CI depth** (`.github/workflows/ci.yml`): no explicit disposable PostgreSQL migration job, integration suite, dependency audit, or Docker Compose validation.
4. **Medium: monitoring scope** (`backend/app/services/monitoring.py`): TCP connectivity is not RF telemetry, SNMP polling, or path correlation; avoid treating it as complete WISP monitoring.
5. **Medium: API/README version drift**: `backend/app/main.py` reports 3.0.0 while README and frontend report 3.1.0.
6. **Review: startup**: backend runs migrations and seed on each start. Verify idempotency and concurrent-start behavior.
7. **Review: security**: inspect authentication, role authorization, audit logging, integration secret handling, SSRF controls for configurable monitoring targets, and API documentation exposure.
8. **Review: test coverage**: inventory backend tests, frontend tests, migration scripts, and existing GitHub Actions run results before claiming a green baseline.

## Next milestone acceptance criteria
- Inventory all backend/frontend/migration/test files and integration boundaries.
- Confirm the branch and CI baseline using GitHub Actions on this PR.
- Add regression tests for the verified risks before changing behavior.
- Fix security defaults and network exposure only with tests and deployment compatibility review.
- Keep this PR unmerged; do not deploy or modify live network equipment.

## Change policy
This audit commit adds documentation only. No production behavior, device configurations, database schemas, or infrastructure were modified.
