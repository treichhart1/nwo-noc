# NOC113 approved Ubuntu host packaging

## Candidate and boundaries

`feature/noc-113-clean-installer` stacks directly on NOC112 draft PR116 at
`b85d1af7ca1e2de1d449dac6ab1c40131012b308`; CI581/582 passed all eight jobs.
The working tree started clean. No AGENTS.md or additional contribution instructions
were found in the repository, tracked subdirectories, `/workspace`, or filesystem root.
NOC110's manifest/dependency graph and NOC111/112 findings were reviewed. They preserve
112 upstream ancestors and the equivalent PR86 replay; main does not contain this stack.
The review/integration order remains the manifest order, then PR114,115,116 and this PR.
Nothing was merged, closed, retargeted or deployed.

The complete application images/modules and28 historical migrations are retained.
No competing database, customer login, inventory, work-order, monitoring adapter or
installer is introduced. AI Fiber Designer and MikroTik Management remain separate.
No live equipment, production Powercode/database, customer message or public incident
was accessed/modified/published. RF remediation remains simulation-only.

## Architecture and changes

The existing acceptance installer is intentionally staging-only. Host packaging now
adds the requested root `install.sh`, `upgrade.sh`, `verify.sh` around the same Compose
services, immutable images, complete migration chain, permission layout and reusable
runtime-role SQL. Existing `scripts/release/` commands remain supported for their
isolated acceptance purpose; they are not used to bypass host approval or seed a demo.

A privileged, external root-owned approval pins repository/commit/tree, purpose,
complete image digests and OCI revision labels, supported upgrade sources and schema.
Unapproved/dirty/incomplete branches, mutable artifacts, incompatible OS/architecture,
resource shortages, existing managed paths/accounts/units and invalid TLS/configuration
are rejected. Production-mode installation requires registry digests. Local image IDs
and alternate roots require explicit controlled-test/rehearsal context; OS/root checks
are never bypassed. No production approval or published release images are provided
by this draft. Independent artifact authenticity review remains mandatory.

Application code, private runtime/migration/bootstrap secrets, persistent data, bounded
logs and encrypted backups have separate locations. Backend/worker use the existing
UID10001 identity and least-privilege noc_app role; migrations use non-superuser
noc_owner; bootstrap credentials stay solely with the dedicated PostgreSQL container.
The SQL role-grant helper was factored without changing its existing acceptance API.
Owner URLs are validated against the dedicated internal database before any migration.
No database is dropped, stamped or silently restored.

The private core retains one internal-only Docker network and **no published Docker
ports**. Docker29 internal networks did not publish the attempted loopback mapping in
local testing, so the package uses a private Nginx Unix listener and a bounded stream
ingress instead of adding an outbound-capable bridge. The host ingress is hardcoded
to127.0.0.1, runs as nwo-noc under systemd, has no capabilities/Docker access, and carries
raw TLS to Nginx without parsing credentials or replaying HTTP requests. Limits:32
connections,64KiB per direction,30s idle and300s lifetime. Half-close/backpressure tests
verify large streams are neither truncated nor replayed. The Unix directory is root-owned
and restricted to dedicated GID10001; the frontend receives that supplementary group,
without trusting the host's potentially unrelated UID101 account. Systemd's ingress
policy denies non-loopback IP traffic. Native Nginx sites/firewall/DNS are not changed.

The first administrator is initialized from a hidden prompt/protected stdin, transactionally
and once, with an audit event. No default password, public setup endpoint or retained
setup credential exists; token expiry is inapplicable because no setup token is issued.
Services do not call app.seed. Signup/notification/Fiber vault and public-status rate keys
are generated independently and mounted read-only to the proper UID. Live integrations,
providers, public gateways, synthetic fixtures and optional network schedulers stay disabled. Bounded local planning and maintenance processing are enabled, using the existing workers rather than leaving those installed modules without processors.

Upgrades verify the installed candidate, approved compatibility, target images/config,
Nginx and migration heads, then quiesce writers/ingress. PostgreSQL dump listing, encrypted
full-state backup, ciphertext hash and disk headroom are required. New source is staged;
migrations and internal candidate health precede the atomic code switch and full ingress
verification. The persistent recovery guard blocks unattended startup after any uncertain
migration/post-switch failure. Bounded on-failure container policies avoid bypassing this guard through Docker daemon auto-start. No automatic database downgrade/overwrite/old-app startup
is claimed. Explicit new-database restoration is tested independently.

## Operator documentation

- [INSTALL](../INSTALL.md): trust, resources, layout, administration, VPN access and services.
- [CONFIGURATION](../CONFIGURATION.md): identities, secrets, ownership and disabled integrations.
- [UPGRADE](../UPGRADE.md): compatibility, quiescence, atomic switch and recovery guard.
- [VERIFY](../VERIFY.md): passed/warning/failed/not-configured semantics and evidence limits.
- [BACKUP_RESTORE](../BACKUP_RESTORE.md): age key custody, encryption, checksums and isolated restore.
- [TROUBLESHOOTING](../TROUBLESHOOTING.md): actionable failure/recovery and release gates.

## Validation evidence and corrections

The final-commit PR ledger records exact CI counts/results. The new ninth Actions job
executes the real installer on a disposable Ubuntu24.04 systemd host: complete services,
admin setup, private HTTPS, account/unit checks, service restart, encrypted backup and
isolated restore, compatible upgrade and approved synthetic startup-failure recovery.
It is part of aggregate acceptance; failure cannot be hidden by the existing eight jobs.
Existing backend/frontend, migrations, security, RF and Ubuntu acceptance gates remain.

Local Ubuntu24.04 userspace testing actually ran. It established clean core installation,
private HTTPS verification, full schema, restricted runtime role, administrator audit and
one-time enforcement, configuration preservation and repeated-install rejection. It did
not establish systemd/host boot. The long local upgrade harness was interrupted by limited
disk and a missing Buildx plugin causing legacy-builder copies; those results are not
reported as passed. Failed test databases/configuration were retained; only task-owned
container writable layers were removed without deleting database volumes. A smaller
Ubuntu userspace test container and an explicit Buildx requirement corrected the harness.
No broad image/volume prune or backup/data cleanup was used.

Corrections found through tests include explicit Nginx file permissions under a private
umask, bootstrap script import path, complete readable frontend checks, private Unix ingress
instead of relying on unpublished internal-network ports, explicit service port configuration,
source-directory execute permission for the unprivileged ingress, explicit profile-worker shutdown and replacement of only owned stale Unix sockets after stopping the frontend, strict internal owner URL
validation and preservation of the post-migration guard after a later service-start error.
The corresponding focused tests cover invalid approvals, missing resources, archive traversal,
permissions, no public ports, role separation, single-use setup, stream reliability and failures.

An initial regression run used a pre-existing stopped test database; another was affected
by the local filesystem filling during a legacy build. Neither is a valid green regression
result. The final local rerun uses a new disposable PostgreSQL16 database with a128MiB WAL
budget/30s checkpoints solely for environment capacity, preserving the original fixture and
all application assertions. Fresh full migration and regression results are recorded in the PR.

Backend Bandit review retains83 exact reviewed findings. Host packaging adds a separate
nine-fingerprint review: fixed argv/no-shell subprocesses, deliberate source-only0755 and
private socket0770 permissions, and the container-only0.0.0.0 listener with zero host Docker
ports. No high/new/stale result is allowed; both gates remain fail-closed. Undefined-name,
workflow syntax, hash-lock consistency and all existing dependency vulnerability checks run.

## Decision and remaining production gates

**Controlled packaging acceptance only; no production/live-pilot authorization.** Until
final CI passes, this remains a draft candidate. There is no approved production release
manifest, trusted image publication/signing workflow or automatic promotion in this PR.

High operational NOC111/112 gates remain: OS/native image vulnerability assessment;
Redis7.4 server licensing; full minimal-host boot/firewall/VPN/MFA acceptance; deployed
malware scanning; off-host key/file recovery; actual firmware/read-only permissions,
provider contracts/mappings/consent and WordPress/Grafana/operator acceptance. The hosted
Ubuntu VM is disposable but preprovisioned, not proof of every minimal-server package
combination. A userspace container is not a boot test. No physical reboot/power-loss
acceptance or sustained production soak is claimed.

The medium1,000-device native polling interval/long-history sizing finding remains open.
Backup encryption/dump/hash checks do not prove an operator has a usable off-host identity.
No automatic backup purge is installed. Direct VPN-interface publishing, ARM packaging,
legacy acceptance-to-host migration, changed systemd-unit protocols and automated repair
of a partially initialized installation require separately reviewed procedures. These
limitations are explicit; they are not waived to label this release production-ready.
