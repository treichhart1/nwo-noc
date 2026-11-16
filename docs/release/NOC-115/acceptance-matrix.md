# Release acceptance matrix

**Decision: NO-GO for operational release/live pilot.** Continued isolated installation
testing is possible with independent controlled-test source/image approval.
Statuses are exactly PASS, FAIL, NOT TESTED or BLOCKED. PASS is limited to the stated
executed scope on the pinned baseline. Final NOC115 CI results remain separately
recorded in the draft PR; they do not promote untested or high-risk rows.

| Criterion | Status | Evidence / limit |
|---|---|---|
| Repository integrity | **PASS** | Exact baseline311 commits/118 PRs;117 direct tips and PR86 replay; main unchanged; [release-manifest.json](release-manifest.json) |
| Feature implementation inventory | **PASS** | 20 mapped backend/UI/migration/test implementations; later per-file provenance retained; synthetic scope only; [feature-manifest.md](feature-manifest.md) |
| Actual vendor/provider compatibility | **BLOCKED** | A111-15: real firmware/contracts/consent/operator acceptance not authorized or verified; [README.md](README.md) |
| Clean application install on disposable Ubuntu systemd VM | **PASS** | Actual root wrappers,77 host assertions plus26 failure assertions and OS rejection on NOC114; rerun required for final NOC115; [evidence.json](evidence.json) |
| Minimal ISO VM Docker bootstrap and physical boot/power-loss | **NOT TESTED** | Hosted VM preprovisioned; container cannot substitute for complete VM boot; [deployment-runbook.md](deployment-runbook.md) |
| Migrations/schema integrity | **PASS** | 28 unchanged revisions; clean/idempotent upgrade and drift; local370FK/102unique, zero unvalidated constraints/invalid indexes; [release-manifest.json](release-manifest.json) |
| Backend health | **PASS** | Actual FastAPI health/auth through private TLS and restored DB; [evidence.json](evidence.json) |
| Frontend health/build | **PASS** | Actual private/status/signup builds and68 browser tests; host frontend TLS startup; [installation-evidence.md](installation-evidence.md) |
| Worker health/recovery | **PASS** | Actual systemd/core/planning workers and DB/Redis/worker interruptions; durable synthetic queue tests; [evidence.json](evidence.json) |
| Authentication | **PASS** | One-time administrator, role login, logout/rate-limit regressions; no default password; [installation-evidence.md](installation-evidence.md) |
| Authorization/privacy | **PASS** | Authenticated private endpoints; denied anonymous requests; financial/document/customer RBAC regressions; [installation-evidence.md](installation-evidence.md) |
| Same-host isolated backup/restore | **PASS** | Encrypted DB/config/keys/documents/Redis/release archive; isolated target and restored application history; [evidence.json](evidence.json) |
| Separate-host complete disaster recovery | **NOT TESTED** | Custody/offsite files/keys and matching application on separate host not executed; [deployment-runbook.md](deployment-runbook.md) |
| Upgrade safety | **PASS** | Approved compatible upgrade, history/config preserved, failed target startup guard, no automatic irreversible rollback; [evidence.json](evidence.json) |
| Private access/isolation | **PASS** | Loopback Unix ingress, no Docker host ports, one internal-only network; no public gateway; [evidence.json](evidence.json) |
| Production VPN/MFA/firewall | **BLOCKED** | A111-16: real deployment access controls/egress not independently accepted; [README.md](README.md) |
| Synthetic end-to-end workflows | **PASS** | Tower/sector/backhaul, replacement, signup, approved fiber import, metrics/Grafana and ROI;10 PostgreSQL workflows; [installation-evidence.md](installation-evidence.md) |
| Production security/supply chain | **BLOCKED** | A111-15/16: native-image scan, Redis licensing, trusted artifacts, deployed AV and real permissions remain; dependency/static checks do not waive these; [README.md](README.md) |
| 1000-device15-second polling target | **FAIL** | Observed synthetic rounds18.391–20.185s on baseline; sizing/soak/long history remain open; [installation-evidence.md](installation-evidence.md) |
| Operator documentation and configuration | **PASS** | Reviewed scripts, existing generated templates and inert review worksheet; tested commands/runbook and explicit untested procedures; [deployment-runbook.md](deployment-runbook.md) |
