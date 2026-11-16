# Installation evidence audit

PASS means **executed and passed in the stated scope**. FAIL means executed and
failed, even if later corrected. NOT TESTED means not executed. BLOCKED means an
explicit environment/authorization/prerequisite prevents that validation. A mock
device response cannot prove installation or firmware compatibility.

The [pinned evidence](evidence.json) contains actual checkpoint names, safe verification
JSON, measurements and immutable GitHub job IDs. NOC113 job133349061372/run588 at
`d501979c53837754c9ec0145b2207a8a0c5b44b7` passed37 host checks. NOC114 job133412406722/
run593 at `d46ace336bce97ce52dffdcc4be0abbc203e3f84` passed77 host checks plus26 fresh
failure assertions and unsupported-OS rejection. Both use a disposable GitHub
Ubuntu24.04 **systemd VM**, not a container pretending to run systemd. NOC114 runner
image was20261111.127.1. Application architecture is amd64/CPython3.12/PostgreSQL16/
Redis7/Nginx; Node22 builds the immutable React frontend before installation.

| Test | Status | Actual execution and practical limit |
|---|---|---|
| Clean application installation | PASS | Root install.sh on fresh application paths in hosted Ubuntu systemd VM; no demonstration seed |
| System dependencies | PASS | Compatible runner Docker respected; required missing packages installed; fresh pinned Ubuntu failure-control image installs packages from official noble repositories |
| Installer installs Docker on a minimal VM without Docker | NOT TESTED | Hosted VM already has Docker; container apt build and mocked missing-package unit test are not this VM scenario |
| Python backend | PASS | Full backend image built with hash-locked requirements; root installer verifies immutable identity/runtime UID and starts FastAPI |
| Frontend build/startup | PASS | Node22 builds private/status/signup outputs; approved Nginx image starts; private TLS login/API pass; browser68 checks run separately in isolated acceptance stack |
| PostgreSQL initialization/Alembic | PASS | Dedicated new DB/owner/runtime roles; all28 migrations; head0028_customer_status; idempotency and schema drift checks |
| Worker startup/recovery | PASS | Core/planning workers healthy; real DB/Redis/core-worker stops detected, then recovery verified |
| Nginx/systemd | PASS | Unit syntax, private TLS Unix ingress, real systemd restart and nonroot account without Docker group |
| Administrator | PASS | Hidden stdin initialization, one audit, real TLS authentication, one-time repeat rejected; no default password or setup token |
| Permissions/secrets/storage/logs | PASS | Verification plus changed secret mode/missing secret refusal, private data directories, journald and bounded container logs |
| Private dashboard | PASS | Loopback ingress, no Docker-published ports, one internal-only application network; public gateways absent |
| Verification | PASS | Real verify.sh exit0 with safe JSON; intentionally disabled integrations are NOT_CONFIGURED, not a false health failure |
| Upgrade/backup/restore | PASS | Compatible approved target, preserved configuration/history/documents, encrypted full-state backup/hash/decryption; new isolated DB and application health/history |
| Upgrade failure | PASS | Approved synthetic startup failure after migration preserves guarded state; no automatic old-app start, downgrade or source-DB overwrite |
| Fresh failures | PASS | Actual64MiB tmpfs and absent package/candidate with denied network; injected DB/migration/startup exits and SIGTERM during deliberately paused migration;26 assertions |
| Unsupported OS | PASS | Actual manager CLI in read-only/network-none Debian container refuses before state changes |
| Initial NOC114 dependency assertion | FAIL | Run590/job133406894044 failed after77 passing host checks; expected apt-install error only. Actual candidate refusal was safe. Corrected atd46ace; final full matrix passed |
| Local native Ubuntu/systemd VM | BLOCKED | Codex is Debian13/no systemd and has limited disk; no local VM result claimed |
| Minimal Ubuntu ISO VM reboot/power-loss | NOT TESTED | Hosted runner is preprovisioned and not rebooted; see remaining manual procedure |
| Off-host entire DB/files/keys restoration | NOT TESTED | Same-host isolated restore is real but not separate-host disaster recovery |
| Live vendor/provider/VPN/MFA production acceptance | BLOCKED | No live access authorized; A111-15/16 remain |

## Actual commands and outputs

The Actions host command was:

```sh
sudo python3 tests/installer/test_ubuntu_host.py --workspace /var/tmp/noc113-acceptance --extended
```

The harness executes the real root wrappers from a disposable clone, with an external
root-owned controlled-test approval, immutable complete image IDs and synthetic
password on protected stdin. Actual install options include `--controlled-test`,
`--approval`, `--password-stdin`, public backup recipient and supplied TLS cert/key/CA.
It invokes verify.sh, a compatible upgrade.sh, verify.sh again and a deliberately
failing upgrade.sh. The expected healthy exit statuses are0; expected failures must
be nonzero and preserve data. No command substitutes a mocked install function.

Safe observed output includes77 PASSED checkpoint records, verification JSON,
`resources_actual_preflight_failure`, `dependencies_actual_preflight_failure`, and
`NOC packaging: Required Ubuntu24.04 package candidate unavailable`. Full raw logs
remain at the pinned Actions URLs; secrets and arbitrary provider exceptions are not
copied into the evidence report. Containers use Ubuntu24.04 pinned digest
`73b999393d07f2bf65a40373778a44dc9a6d54ad7718d29247d8ff9413c4d6a8`.

Observed NOC114 final-code install:63.880s push /62.248s PR, using cached prebuilt
artifacts. PR-run single API calls10.673–189.232ms; empty-device query EXPLAIN ANALYZE
0.098ms. Recovery-time snapshot: backend152.8MiB/0.11%CPU, PostgreSQL29.71MiB/4.92%,
Redis4.336MiB/0.57%, core worker114.3MiB/80.00%, planning85.03MiB/0.00%, frontend
4.469MiB/0.00%. Source release5,413,594bytes and host persistent directory296bytes
exclude images and PostgreSQL/Redis Docker volumes. These are snapshots/single
requests, not peaks, load percentiles, total disk sizing or production guarantees.

Representative40-POP benchmark on the same final NOC114 commit:10,000-asset page100
1.91ms;10,000-IPAM assignment page100 3.19ms;1000-device tower assessment1853.57ms;
outage correlation765.78ms. Native mock polling18.391–20.185seconds/1000-device round
fails the15-second target. Source history11,337,728bytes and polling peak RSS139,876KiB
exclude PostgreSQL/other containers. Final NOC115 rerun values are separately recorded
in its draft PR; do not relabel these baseline timings as new executions.

## Installer source review

All three root scripts are Git mode100755, use `#!/usr/bin/env bash`,
`set -Eeuo pipefail`, a quoted script-relative ROOT and `exec python3 ... "$@"`.
No eval, curl-to-shell, default credential or destructive database operation is added.
The manager requires Ubuntu24.04/root/amd64/systemd, exact clean approved commit/tree,
preserved integration graph, image identity/runtime UID, resource floor, protected
configuration and disabled live flags. Fresh install refuses existing managed
paths/accounts/units/volumes; it never silently upgrades or drops a database.

The approval is an independently prepared root0600 file outside the checkout.
Production mode requires registry digests; local immutable image IDs are explicitly
controlled-test only. Secret generation, one-time administrator audit, owner/runtime
role separation, encrypted backup and guarded atomic release switching are retained.
Errors are sanitized and refer to protected logs/runbooks. A partial install is not
automatically resumed or deleted. Token expiry is inapplicable: no setup token exists.

No production-approved manifest or published/signed application artifact currently
exists. The documented bare sudo install.sh therefore deliberately refuses an
unapproved development checkout. This is a release prerequisite, not a claim that
the ordinary command works without trusted inputs. Reboot, off-host recovery and
real operational credentials require the remaining procedures in the runbook.
