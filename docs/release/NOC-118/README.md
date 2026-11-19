# NOC118 — Backend container security and release hardening

## Decision and exact starting point

**NO-GO for operational release or live pilot.** Start from verified NOC117 PR121
HEAD `752939ade1ae3ffd167bf484d7b84c6b90b244b4`, run626 all9jobs successful.
New stacked branch `feature/noc-118-backend-security`; clean initial working tree;
no AGENTS.md found in applicable parents/tracked paths. NOC117 reports, complete
native scan and trusted-artifact procedures were reviewed. All upstream history
and28 historical migrations preserved; no PR merged/closed. Exact final SHA/tree,
job/test/scan/SPDX/provenance/installer/performance results are recorded in this
milestone's draft PR final ledger. Pending checks are not reported as passed.

## Baseline and exact-image reproducibility limitation

NOC117 backend configID:
`sha256:ac811a4a7eb107b595abf6e3bce43f37335bccce42442704e77962108593915d`,
Debian13.7/Python3.12.16,0CRITICAL/51HIGH/240total. Worker shares this image;
do not double-count it. Full historical HIGH/CRITICAL rows are retained in
historical-baseline-high-critical.json. Full raw archive: run626/artifact14493524003,
SHA256 `d17bb1fca4ec161ce300a04982973b5587c8102ffe0b152c5f42ba0b1214e555`.
This unpublished CI image is absent locally (docker inspect: No such image).
Its exact bytes cannot be freshly rescanned here: **BLOCKED**, not fabricated.

CI archives exact source752939a/backend+frontend with historical immutable bases,
freshly rebuilds and scans resolved immutable IDs. Repository package revisions
can change: record actual new IDs/versions/counts; do not call this byte-identical
reproduction of an unpublished old image. All raw Trivy JSON, installed packages,
CVEs/severity/FixedVersion/status/vendor metadata are retained. Scanner/identity
errors fail. --require-no-high remains strict. Local Docker VFS has<200MiB free;
existing work/images/data retained, not pruned. Builds/tests use disposable CI.

## Supported correction and review

51historical HIGH rows represent15distinct CVEs;40rows repeat4util-linux CVEs
across10binary packages. No historical HIGH row listed a stable FixedVersion.
Debian tracker says Bookworm is not affected/fixed for76642/78408/78409, but
still vulnerable for78410. Older versions are **not** generally assumed safe.

Candidate is an explicit OS rebase Debian13→official Debian12 LTS, not described
as a same-distribution patch. Python stays **3.12.16**, same upstream checksum.
Base `python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df`.
Official registry/config/source history: base-review.json. Debian12 LTS supports
amd64/arm64 through2028-06-30; installer targets Ubuntu24.04/amd64. Package/arch LTS
coverage and a supported successor must be reviewed before that date.
See https://www.debian.org/releases/bookworm/ .

All Python locks/hashes unchanged. Multistage build installs only hashed binary
wheels and copies their prefix into the identical final base. No compiler added;
runtime pip uninstalled as unnecessary. Required GDAL Expat/zlib and Python SQLite/
readline support retained. Final-base imports check GDAL/rasterio/PROJ/psycopg/
crypto/SQLite/Expat; actual runtime evidence records versions/UID/GID and pip/compiler/
homed/module presence. ABI/import/full-stack failures must fail acceptance, not be
hidden by removing GIS features. Reject Alpine/musl wheel incompatibility, unstable
Debian/libc mixing, essential-package stripping, or private ABI replacements just
to reduce CVE counts. Vendor-unfixed stable findings remain blockers.

## Every HIGH finding: runtime exposure and vendor disposition

vulnerability-dispositions.json retains each historical row, responsible upstream/
Debian vendor, official URL, exposure and next action. vendor-advisory-lookups.json
preserves primary lookups including failures. Bulk vendor JSON and some individual
pages were unavailable; do not invent Debian fix status. CNA/upstream sources are
used separately, without treating RHEL package fixes as Debian fixes.

| CVE group | Runtime exposure / mitigation / required action |
|---|---|
| util-linux76642/78408/78409/78410 | Local mount/nsenter privilege paths, not app functions. Nonroot/cap_dropALL/no-new-privileges/private namespaces mitigate. Vendor says first3notaffected/fixed on Bookworm;78410still vulnerable. No suppression. Upstream2.42.3/2.42.4 fixes do not authorize unstable package mixing. |
| ACL54369 | Privileged pathname/symlink ACL manipulation. NOC uses protected file ownership, not privileged ACL helpers. Read-only/nonroot limits exposure, not proof of safety. Debian maintainer backport/triage required; CNA https://access.redhat.com/security/cve/CVE-2026-54369 . |
| Expat113351/115506/117610/118370 | Required GDAL runtime library; authenticated GIS/XML/sidecar imports may reach parsers. Bounded subprocesses do not prove malformed-input safety. Stable releases tracked vulnerable; upstream2.10.0 fix needs supported stable backport. |
| ncurses2025-69720 | infocmp CLI overflow; no application invocation. Required Python/readline libraries retained. Debian stable no-dsa; later upstream snapshot fixed. Scanner source-package/library rows stay blockers pending review. |
| SQLite119373/119374/119379 | FTS5/session malformed database/query paths. Main NOC DB is PostgreSQL but Python/GDAL SQLite is present. Transitive GIS paths cannot be declared unreachable; stable vendor backport or separately reviewed ABI migration required. Upstream3.54.x fixed, not stable-package approval. |
| systemd16742 | homed signature/authentication flaw. Python worker containers do not use homed-managed accounts; actual evidence checks daemon presence. Keep source-package library findings blocked; CNA https://access.redhat.com/security/cve/CVE-2026-16742 . |
| Perl9538 | Archive::Tar memory exhaustion. NOC uses Python archive handling, not Perl; record module presence and resource bounds. Absence of a call does not clear scanner findings. Upstream module3.10 fixed; Debian package review required. CNA https://access.redhat.com/security/cve/CVE-2026-9538 . |

All IDs have prefixCVE-2026- except ncurses. Primary Debian URL for each is
https://security-tracker.debian.org/tracker/CVE-<year>-<number> . Three util-linux
source exclusions must be distinguished from package patches. No-fix is never
safe-by-default. All remaining/new HIGH/CRITICAL findings require full scanner,
vendor/affected-code verification and operator review; no VEX/ignore/downgrade.

## Container hardening and stack compatibility

UID/GID10001 and root-owned/readable app files unchanged; persistent paths0700 and
user-owned. Compose/host preserve cap_dropALL/no-new-privileges, read-only rootfs,
bounded writable/tmp and required data volumes, private bindings/internal networks,
no privileged mode. Shell retained for existing seed/migration launcher; no claim
of shell-free image. New bounded loopback healthcheck ignores proxy settings;
Compose worker checks override it. No embedded secrets or credential logging.

PostgreSQL16 derivative/Go1.27.4/xsys0.44.0, Redis7 and frontend preserved. No API,
migration or app behavior change. Actual rebuilt images must pass Ubuntu systemd
install/verify/start/restart/upgrade/failure/encrypted backup+isolated restore and
full RF/tower/IPAM/inventory/signup/topology/customer/maintenance/Grafana/fiber/auth
regressions and69browser cases. External devices/providers remain mocked/disabled.

## Trusted artifacts, complete SBOM and fail-closed release

Production approvals now require all4runtime image digests (backend/frontend/
PostgreSQL/Redis) and4hash-bound evidence envelopes next to the private
root-owned0600 manifest, in a protected non-writable directory. Example updated:
deploy/host/approved-release.example.json. Old production approvals without this
evidence must be reissued/re-signed; saved state not rewritten. Explicit controlled
test fixtures remain test-only, never production authorization.

Each manifest evidence entry: `{file:<simple-local-name>,sha256:<64hex>}`. Each
envelope: `{format:1,kind:<sbom|dependencies|containers|provenance>,commit:<sameSHA>,
tree:<sameTree>,images:<same4registrydigests>,payload:<data>}`.
Traversal/absolute names, symlinks, public/wrong-owner files, size>64MiB, missing
artifacts or hash/source/image mismatches fail before image acquisition/migrations.

Payload schemas:
- sbom: `{source:<SPDX2.3>,images:{backend:<SPDX2.3>,frontend:<SPDX2.3>,postgres:<SPDX2.3>,redis:<SPDX2.3>}}`; each nonempty inventory.
- dependencies: `{pip:<fullpip-auditJSON>,npm:<fullauditReportVersion2JSON>}`; versions/vuln lists, explicit development dependency count, every severity counter0. No dev omission.
- containers:4role→TrivyJSON, SchemaVersion2/ImageID, matching approved registryRepoDigest/nonempty results/package inventory. Any HIGH/CRITICAL blocks production approval. Unpublished local IDs are insufficient.
- provenance:source_commit/source_tree/subjects(sameimages)/builder/invocation_id/materials(immutable registrydigests), all checked.

Detached Ed25519 verification still requires independently provisioned protected
`/etc/nwo-noc-release.pub`, with no network-key fallback. Sign exact reviewed
manifest bytes via existing NOC116 trusted-artifacts procedure after installing
bound envelopes with secure permissions. Never put production private keys on NOC.
No production key, signed publication or authorization is fabricated. New tests
use disposable keys and real OpenSSL verification/tamper rejection.

CI now retains complete raw image scans and SPDX conversions for every image,
each bound by raw-report/imageID/SHA256 index. Pinned Trivy conversion has no network
and fails incomplete inventories. Source-lock SPDX is separate, not falsely called
a full native SBOM. Unsigned build-provenance.json binds CI SHA/tree/runURL/input
digests/Dockerfile+lock+report hashes/local IDs. It is not a signed SLSA attestation.
Publisher must publish/pull immutable reviewed images and regenerate evidence with
their actual registryRepoDigests, then obtain independent approval/signature.
Unresolved HIGH findings prevent production release even with a valid signature.

## Executed tests and measurements

Focused security/host/hardening/SPDX regressions:80passed/0.84s. Undefined-name
checks passed; installer Bandit11reviewed/0unreviewed/0HIGH. Initial local static-tool
path was unavailable; corrected to existing audit venv. Preliminary full run:
875passed/955DBskips/1failure because old signed positive fixture lacked the newly
mandatory evidence; fixture updated to full signed reports, not bypassed. Original
tamper/authorization assertions remain and a real-signature positive test is added.
Final full local and CI results belong to executed final ledger, not projection.

Existing250/500/1000 benchmarks run6real PostgreSQL rounds at8threads/synthetic2ms
on both final CI triggers. Preserve all rounds/median/min/max, CPU/RSS/query latency
and6000unique samples/receipts, not only favorable runs. Previous15s inconsistency
remains a blocker until repeated measurements on representative resources support
acceptance. Synthetic transport is not live-device performance or a production SLA.

## Final results, blockers and next action

blocker-matrix.json and per-row dispositions accompany this report. Full fresh
baseline/candidate counts, exact image identities, new/fixed/residual CVEs, final
SHA/tree/test/job/Ubuntu results, all repeated performance/resource measurements
and artifact archive digests are appended to the draft PR final execution ledger.
Not-executed/blocked checks are not converted to PASS. No package removal or stable
version safety is claimed from a missing scanner row alone.

Remaining gates: supported fixes/triage for native residuals, full licenses/Redis
terms, trusted production publishing/key custody, minimumISO independent bootstrap/
reboot, offhost DR/key recovery, deployedAV/VPN/MFA/egress and live vendor/provider/
Powercode/consent acceptance. Pursue supported stable backports, review measured
evidence, then complete independent operator acceptance. No deployment/live pilot
authorization, PR merge, live equipment/Powercode, real messages/public incidents,
automatic RF remediation, other-repository changes or migration deletion.

Redis approval must exactly match the source image lock; no caller-provided
alternate image is silently substituted. This preserves Redis7 while including
its native SBOM/report in the complete signed release evidence.
