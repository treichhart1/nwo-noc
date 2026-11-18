# NOC117 — rebuilt container security remediation

**NO-GO for operational release or live pilot until security and operational gates
have accepted evidence.** This is a new reconstruction from verified NOC116 commit
`008f54e41cb874d2d6eecebf65bb278ddb2ae25a`, draft PR120. None of the missing original
NOC117's alleged results are reused. Final source SHA, exact CI/job/artifact results
and measured findings are recorded in this milestone's draft PR acceptance ledger.

The baseline worktree was clean. No AGENTS.md was found at root/workspace/repository
or tracked subdirectories. NOC116 report, complete native-image evidence and trusted
artifact procedures were reviewed. All319 baseline commits, upstream draft dependencies
and28 historical migrations are preserved. No existing PR is merged/closed/retargeted.

## Investigation and implementation

NOC116's original PostgreSQL16 image is pinned to index
`sha256:81e77a4833487b8132fe4b00bedd39dc3f74b8641e387f19312a48f7de218c2e`.
Fresh registry inspection still resolves the current tag to that digest. An official
PostgreSQL16 Alpine3.23 alternative is pinned separately in deploy/image-candidates.json
for investigation only, never as an approved installation default. Its findings must
be read from actual scans, not inferred from the tag/distro.

The original critical Go stdlib finding is in gosu, not evidence of that CVE in the
SQL engine. Upstream gosu1.20 release notes still specify Go1.24.6; replacing the
binary with the upstream download does not demonstrate patched stdlib. Instead:

- deploy/Dockerfile.postgres keeps the exact PostgreSQL16 vendor engine, entrypoint,
  data layout and UID70/GID70. It replaces only the privilege-drop helper with a
  statically compiled gosu1.20 from exact upstream commit
  29b939aa5bd4dc588af765b8248f6e39150d2565, using digest-pinned Go1.27.3.
- Vendored source, module checksums, Apache2 license and SHA256 provenance are in
  deploy/vendor/gosu. No upstream source code was modified. GOTOOLCHAIN=local prevents
  silent compiler downloads; -mod=readonly and go mod verify protect dependency locks.
  Build output retains Go module/toolchain metadata; no stripping, VEX or ignore
  files are used to hide scanner findings. The Go compiler is not shipped at runtime.
- Supported same-distribution apt/apk security updates apply to backend/frontend and
  the PostgreSQL derivative. Python3.12/Node22/PostgreSQL16/Redis7 remain the same major
  families; no distro migration or framework/library major update was introduced.
  Package repositories can change patch revisions: immutable output digests/SBOM,
  rather than a false bit-identical-build guarantee, identify the exact tested release.
- Development/isolated defaults build/load noc117-postgres:rc, then pin its immutable
  ID in isolated state. Mutable development tags are not accepted as release approvals.

This is a **NOC-maintained derivative**, not a vendor-signed PostgreSQL image. Its
ongoing security maintenance, publication, provenance and trust require operator review.
No actual published/signed production artifact or key infrastructure is fabricated.

## Baseline and rescan procedure

Compose CI archives the exact NOC116 source commit and freshly rebuilds its backend/
frontend, then scans resolved immutable image IDs plus the exact historical PostgreSQL/
Redis digests and the separate official PostgreSQL16 candidate. Historical app-image
IDs were local unpublished build IDs: a fresh source rebuild is **not** claimed to be
byte-identical to those old images. Base digests and each new output identity are kept.

It then builds/scans NOC117 runtime images using the existing pinned Trivy tool,
retaining complete JSON, every HIGH/CRITICAL finding, licenses, image IDs and summaries
for both baseline and candidate. Scanner failure/malformed identity fails the job;
remaining native findings are FAIL evidence even if evidence collection completes.
--require-no-high remains available as a strict release gate. Nothing is ignored or
waived merely to obtain green development CI. Final counts belong to actual artifacts.

The local Docker VFS filesystem has less than1GiB free; building additional compiler/
application images here would reproduce NOC116's disk failure. Images/installations
are therefore built/tested on disposable Ubuntu CI runners. Local pure regressions,
source hashes, lock/static checks execute here; no local full VM or rescan is invented.
Existing images, containers, databases, backups and unfinished ignored work are retained.

## Signed release/installer safety

Signed release approvals must now include **backend, frontend and postgres** registry
image digests. PostgreSQL must have the same approved NOC source-revision label as
application images; its root initialization entrypoint still drops privileges through
gosu to the vendor database user. The installer preserves the explicitly approved
PostgreSQL image and never silently substitutes the vulnerable base image.

Historical two-image approvals are accepted only in explicitly controlled-test mode
for compatibility tests; a production-purpose two-image approval fails closed. Saved
release state and historical migrations are not rewritten. Old installations need a
new separately signed three-image target approval and supported upgrade_from entry.
See deploy/host/approved-release.example.json and NOC116's trusted-artifacts procedure.
Signature checks, protected key ownership, exact commit/tree/digests, private bindings,
nonroot application/ingress, disabled live integrations and recovery guards remain.

Release authority must publish all three immutable images with matching OCI revision
labels, retain source/helper/module/build/SBOM evidence, independently provision the
trusted Ed25519 public key and sign the exact reviewed manifest bytes. Test fixture
keys/signatures are not production authorization. Do not disable verification or
fall back to mutable tags. No publication or live installation is authorized here.

## Redis licensing and compatibility

Redis server remains the exact existing7-alpine digest, identified by NOC116 as7.4.12;
CI freshly records redis-server/redis-cli versions without network access and runs
existing worker/Redis restart and durable notification queue regressions. Redis client
and server are distinct components; no silent Redis8/Valkey substitution occurs.

The exact upstream redis/redis tag7.4.12 LICENSE.txt was retrieved anew and retained as
redis-7.4.12-LICENSE.txt, SHA256
abffee3703161c0a6a0cd7f635b2c1ebb3b81bd199e9d18ea89652d678ae4287.
It states a choice of RSALv2 or SSPLv1. This is license identification, **not legal
approval** of NWO's distribution, hosted use or commercial terms. Operator/legal must
select/approve compliant terms and obligations before release. Scanner license labels
alone cannot approve that use. General unknown/copy-left license findings remain open.

## Validation and performance

Existing CI reruns backend/frontend, migrations/drift/idempotency, RF/security and
cross-module suites,69 inherited browser cases, actual Ubuntu systemd install/verify/
restart/upgrade, synthetic encrypted backup/same-host isolated restore, failure handling
and1,000-device performance. The Ubuntu host harness now uses the patched PostgreSQL
image for actual installation and upgrades, rather than only scanning an unused image.

New tests check helper source hashes, unchanged vendor engine/entrypoint boundaries,
three-image approval, source revision rejection and preservation of the approved DB
digest. Existing real Ed25519 positive/tamper tests now include PostgreSQL in the signed
manifest. An early local full run exposed the deliberate immutable-image count increase
from6 to7 (new compiler); the strict expected count is updated to7, not relaxed.

Local initial focused run79 passed/3 DB-skipped/1 warning in2.31s. A preliminary full
run had854 passed/955 DB-skipped and1 failure due to that old count assertion; it is
not labeled passing. Subsequent exact results, measured benchmark and scan counts
are recorded in the final draft PR ledger after execution. Counts across suites overlap.

No earlier NOC117 benchmark or security result is accepted. A fresh final-commit
250/500/1,000-device synthetic benchmark must establish performance; a passing hosted
profile is not a real-device SLA. No collection interval, threshold, test or security
assertion is weakened. Minimum-ISO bootstrap, physical reboot/power-loss, off-host key/
file/DB disaster recovery and actual vendor/provider compatibility remain unverified.

## Acceptance and remaining blockers

Read security-acceptance.json with this report and actual CI artifacts. Code-level
checks can PASS only in their executed scope. Live firmware/OID/permissions, Powercode/
consent/provider contracts, published trusted artifacts, image/host CVE and license
approval, deployed AV, VPN/MFA/egress and independent disaster recovery still block
operational release. Do not mark GO because a build/scan job is green.

No production deployment/access, equipment/Powercode connection, real notification,
public incident, configuration write, PR merge or other application repository changes.
RF self-healing stays simulation-only. The other applications remain separate.
