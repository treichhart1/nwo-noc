# NOC-82 — AI NOC release consolidation and clean-install readiness

Status: **BLOCKED / AUDIT ONLY**. This document is not release authorization.
Scope: `treichhart1/nwo-noc` only. AI Fiber Designer and MikroTik Management remain separate applications.

## Baseline and ownership
- Audit branch: `feature/noc-82-release-readiness-audit`, stacked on `feature/rf-81-validated-self-healing-telemetry`; this is **not** an independently deployable branch.
- RF-81 PR #85 is upstream; RF-80 PR #84, RF-79 PR #83, RF-78 PR #82 are earlier stacked work.
- Do not assume development changes exist on `main`. Compare main/develop/feature ancestry and each open PR before any merge or release tag.
- Preserve existing AI NOC server, database, .env secrets, backups, historical monitoring data and integration configurations.
- Never merge PRs, deploy, change network equipment, or enable self-healing without explicit operator authorization.

## Verified source inventory
- Backend: FastAPI, SQLAlchemy, Alembic, PostgreSQL, JWT authentication.
- Frontend: React/Vite with dashboard, operations, incidents, map, topology, sites, links, sectors, devices, alerts, RF diagnostics, settings.
- Worker: scheduled monitoring and UISP synchronization.
- Deployment: Docker Compose services `db`, `redis`, `backend`, `worker`, `frontend`; backend/frontend bind to loopback by default.
- Models: User, Integration, Site, Device, NetworkLink, Sector, AuditLog, Incident, MaintenanceWindow, TelemetrySample, MonitorCheck, MonitorEvent, RFCollectionRun.
- RF self-healing is **simulation only**, with no live device execution allowed.
- README says version 3.1.0; FastAPI main/health report 3.0.0. Resolve release version mismatch.

## CI evidence and open blockers
- RF-81 prior SHA `5ea1276753699a58d147d528209fa7f8fc22f172`: CI #415 FAILED.
  - backend: 1 failed, 203 passed, 20 skipped; `test_preview_blocks_without_validated_rf_samples` used a device fixture without `monitor_enabled`.
  - RF offline release acceptance also failed; inspect its logs separately.
  - frontend, database-migrations, rf-postgres-integration, compose-validation passed on that run.
- RF-81 fixture correction committed as `b27e53a5437d04612f6adbf41605e22501d1bbb5`; **fresh CI not yet verified**. Re-run/verify required before release.
- RF-80 earlier CI #411 passed, but that does not validate RF-81 or the complete stack.
- Confirm every required workflow is green at the **exact release-candidate SHA**.
- Review RF-81 semantics: invalid intervening samples must break a consecutive degradation streak; reconcile degradation thresholds with simulation proposal thresholds.
- Validate role authorization, absence of live actions, replay prevention and auditability before any RF-82 approval workflow.
- Confirm source tree and migration history on main vs feature branch; document safe ancestry and conflicts.
- Confirm all skipped tests are justified and operationally relevant acceptance tests run in staging.

## Feature coverage and gaps
| Area | Verified in inspected branch | Not yet verified / next scope |
|---|---|---|
| Operations | Dashboard, incidents, device monitoring, event acknowledgment, site/link/sector records, topology, map | Real-world device and alert accuracy |
| RF | Normalization, diagnostics, history, site summaries, simulation-only preview | RF-81 passing CI, hardware validation, persistent simulated approvals |
| Integrations | UISP sync | Powercode customer sync, Slack/SMS, Grafana |
| Customers | Site counts, incident impact numbers | Customer entity, signup, plan, service-to-AP relationship |
| Inventory | Device records | Stock, serials, spares, assignment and transfer |
| Field | Maintenance windows | QR tower workflow, documents and work orders |
| Intelligence | RF diagnostic rules | AI IPAM, predictive capacity, ROI |
| External apps | API boundary planned | AI Fiber Designer/MikroTik Management integrations; separate deployments |

## Clean-install release gates
- [ ] Audit open PRs and construct a reviewed dependency graph; record exact base/head SHAs.
- [ ] Resolve RF-81 failures and obtain green CI for backend, frontend, migrations, RF PostgreSQL, Compose, offline acceptance.
- [ ] Identify a single candidate SHA with **no unreviewed or accidental stacked features**.
- [ ] Validate production secrets, CORS, TLS, network binding, authorization and backup/restore.
- [ ] On disposable Ubuntu 24.04 staging, install Docker/Compose and verify services start and health checks pass.
- [ ] On disposable PostgreSQL, run `alembic upgrade head`, `alembic check`, repeat upgrade and test rollback/restore using a backup.
- [ ] Verify frontend/API version consistency and login/role permissions.
- [ ] Run UISP integration with RF collection disabled first; verify fail-closed behavior on missing credentials and unreachable services.
- [ ] Validate representative device/AP/tower/backhaul metrics against trusted read-only source data; ensure no production device commands.
- [ ] Check monitoring/incident/alert accuracy, freshness, and failure isolation at expected scale.
- [ ] Record acceptance evidence and receive explicit approval before promoting or switching traffic.

## Suggested acceptance sequence
1. Offline CI and disposable database.
2. Isolated Ubuntu 24.04 staging with synthetic data.
3. Authorized read-only UISP/device data with bounded polling and no write credentials.
4. Operator acceptance of alerts and incident/customer impact.
5. Separate change-controlled production rollout with verified backups and rollback.

## Explicit exclusions
No customer signup, billing mutations, 811, LiDAR, heat-map implementation, RF device execution, or production deployment in NOC-82. Do not fold the separate AI Fiber Designer or MikroTik Management applications into this repository.
