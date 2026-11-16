# NOC114 installation acceptance and production readiness

## Provenance and scope

Repository: treichhart1/nwo-noc only. Branch feature/noc-114-pilot-readiness starts
at NOC113 PR117 commit `d501979c53837754c9ec0145b2207a8a0c5b44b7`; Actions587/588
passed all9 jobs. PR117 remains open/draft, targeting NOC112. The working tree
started clean; no AGENTS.md instruction was found at root/workspace/repository or
tracked subdirectories. README, NOC110–113 reports, installer/runbooks and the pinned
integration manifest were reviewed. Preserve the NOC110 graph's112 direct ancestors
and PR86 equivalent replay, followed by PR114→115→116→117→this draft. Main is not the
integrated release; nothing is merged, closed or deployed.

All28 historical migrations remain unchanged, single head0028_customer_status.
No authoritative model, network adapter, notification engine or production deployment
is added. The existing complete installer and cross-module suites are reused.
PilotPlan is offline review only. RF self-healing remains simulation-only.

## Executed versus planned evidence

The final draft PR ledger supplies exact final SHA, run links, test counts and numeric
measurements. Its final-commit Actions logs are the command-output evidence; this
report does not infer execution from a planned test or a previous milestone's green CI.

The host job uses the real `install.sh`, `verify.sh`, `upgrade.sh` wrappers as root
on a disposable GitHub Ubuntu24.04 systemd VM, with external controlled-test approvals,
immutable complete application images, synthetic credentials and no demo inventory.
It records strict private TLS login, no Docker published ports/internal-only network,
systemd restart, migration/role checks, all-module private API smoke, denied anonymous
private APIs, invalid secret permissions/missing secret, real DB/Redis/worker stops
and recovery. Verification JSON is printed without secrets. Startup duration, a
container CPU/memory/I/O snapshot, persistent/source disk bytes, single API timings
and actual PostgreSQL EXPLAIN ANALYZE are emitted. Empty-host metrics are not a load
benchmark; existing40-POP/250/500/1000-device and10,000-asset/IPAM benchmarks supply
representative synthetic workload measurements separately.

The host job tests compatible upgrade, atomic code switch, encrypted SQL/config/key/
document/Redis/release backup, checksum/decryption, isolated database restoration,
restored application health/history, and deliberately failed post-migration upgrade
with a persistent startup guard. It does not automatically restore/downgrade a DB.
Existing populated0011→0028 upgrade and matching-old-application restore remain gates.

Fresh failures run separately inside pinned Ubuntu24.04 containers with private test
roots and one internal-only application network. Real64MiB tmpfs exhaustion and removal
of only the container's logrotate package with denied network exercise actual preflight
failures. Approved synthetic fixtures force PostgreSQL exit42, migration exit42,
backend startup exit42, and SIGTERM during a deliberately paused migration. Each
preserves state/configuration, refuses fresh-install rerun and blocks unattended
startup. Those injected failures validate failure handling, not a naturally observed
PostgreSQL defect. Databases, configuration and backups are retained, never dropped.
An unsupported Debian application container runs the actual CLI read-only/no-network
and must reject the OS before modifying installation state.

The root host and public frontend are built with their existing lockfiles; host Node
is unnecessary for immutable prebuilt frontend deployment. Containers cannot establish
systemd/boot acceptance; the separate disposable VM tests real systemd services. GitHub's
runner is preprovisioned, so it is not certification of every minimal Ubuntu image or
physical boot/power-loss behavior. Local Codex is Debian13/no systemd with limited disk;
it cannot substantiate a full native Ubuntu installation. Local unit/PostgreSQL tests
and hosted VM/container execution are recorded separately in the final PR.

## Integrated operational and security coverage

Existing browser smoke covers core/RF/tower/Down Detector/IPAM/inventory/QR/maintenance/
documents/signup/unified fiber-wireless topology/LiDAR/Fiber Designer/finance/Grafana/
public-only status/notification approvals. External interfaces are mocks; synthetic
compatibility is not actual vendor firmware, provider or WordPress acceptance.

NOC110 workflow A now covers power, backhaul and sector evidence with deduplicated
approved mock outage/restoration delivery and audit history. Missing backhaul endpoint
or unconfirmed customer impact must remain excluded and must not produce a false
restoration; the test explicitly preserves that policy. Existing workflow B exercises
verified alternate paths and maintenance linkage; C–G cover replacement, signup,
approved fiber import, analytics and financial allocation. No public incident or real
message is published. Existing auth/RBAC, customer/document/financial privacy, rate
limits, read-only allowlists, bounded retries and dependency/security gates remain.

## Acceptance criteria

| Criterion | Pass rule / evidence |
|---|---|
| Source | Exact tested commit/tree; complete integration manifest; clean working tree |
| Clean installation | Actual root wrapper exits0, no demo seed, one audited administrator |
| Schema | All28 migrations, current head, no drift, restricted runtime role |
| Runtime | Backend/frontend, Redis, core/planning workers healthy; systemd restart |
| Private access | Loopback ingress, no Docker-published ports, internal-only network |
| Failures | Safe refusal/state retention/guard, actionable sanitized error; no overwrite |
| Upgrade/recovery | Verified encrypted full-state backup, successful isolated restore/application startup, safe failed-upgrade guard |
| Workflows | Synthetic integrated workflows/browser/security/regression jobs green |
| Critical findings | No unresolved reproduced critical security defect; do not infer absence from static tools alone |
| High gates | A111-15/16 remain explicit blockers, not waived by packaging acceptance |
| Pilot | Disabled templates and procedure ready for independent review; no connection |

## Initial observed NOC114 evidence

On commit `a388b6413228e1b00519a08e6c06749460c60874`, runs590/591 actually
passed77 host checks each, including real systemd install/restart,22 authenticated
module endpoints, four anonymous denials, DB/Redis/worker interruptions, full-state
backup, upgraded application and restored-application startup. Run591 installed in
57.274seconds with prebuilt local artifacts; single private API calls were5.743–
114.694ms, device-page EXPLAIN ANALYZE execution0.050ms. A single idle/recovery snapshot
showed backend152.6MiB, PostgreSQL28.93MiB, Redis4.418MiB and core worker75.87MiB;
these are not workload maxima. Source release bytes5,411,186 and host persistent
directory296 exclude Docker-volume database/Redis bytes and image storage.

The real64MiB tmpfs resource refusal passed. The missing-package scenario initially
failed its harness assertion because it expected the apt-install stage only, whereas
an unavailable or untrusted package candidate can be rejected earlier. The corrected
assertion permits only the installer's specific repository/candidate/install failure
messages and still requires nonzero exit and no installation state/configuration.
Sanitized stage diagnostics are now printed. The initial overall jobs are **failed**;
later final-commit CI must validate the complete matrix before acceptance. Local
validation:807 unit passed/951 database-dependent skipped;55 focused tests (overlap);
10 PostgreSQL workflow tests passed. Final counts/results are in draft PR118.

## Go/no-go and unresolved gates

**GO only for continued isolated controlled acceptance after final CI passes. NO-GO
for production or live pilot activation.** The [pilot plan](NOC-114-controlled-pilot.md)
prepares operator review, not authorization. No real endpoints/credentials are provided.

High A111-15: actual RouterOS/radio OIDs/permissions, TLS/egress approvals, real provider
contracts, Grafana authentication, mappings/consent and WordPress deployment acceptance.
High A111-16: OS/native-image vulnerability assessment, Redis server license approval,
off-host keys/files recovery, deployed malware scanning, minimal-host firewall/boot,
VPN/MFA and operational hardening. No approved production artifacts/signatures or
external release manifest are published by this draft. The offline host package has
no reviewed live-integration activation configuration; do not weaken it to start a pilot.

Medium A111-12:1,000-device15-second polling, sustained concurrency/soak, dense long
history/retention sizing. Actual measurements in the final PR retain these limits.
Physical reboot/power-loss, separate-host full-file disaster recovery and operator
acceptance remain unexecuted unless explicitly evidenced. No test result constitutes
production deployment permission.
