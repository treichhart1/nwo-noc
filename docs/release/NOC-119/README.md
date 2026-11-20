# NOC-119 — independent pilot security validation

**NO-GO for operational release or live equipment pilot.** This work prepares an
isolated installation candidate; it grants no production or device authorization.

## Source and dependency provenance

Base PR122, exact HEAD2370f4eec42c8198e197c0cc96e5b570b26a3239, tree6a24c067e2bb2d19a0be0b1de44a29397cd80211.
PR122 is draft/open/unmerged. Run638 (44534576236) completed SUCCESS with9/9jobs;
this was independently checked before implementation. The initial local working
tree was clean. No applicable AGENTS.md was found. Existing worktrees, Docker
data, historical migrations and upstream draft PRs are preserved. New branch:
feature/noc-119-pilot-security. Exact final source/tree, final checks, artifact
hashes and terminal acceptance matrix are in this milestone's draft PR ledger.
No pending execution is represented as successful.

The inherited stack is NOC110 PR114 → NOC111 PR115 → NOC112 PR116 → NOC113 PR117
→ NOC114 PR118 → NOC115 PR119 → NOC116 PR120 → NOC117 PR121 → NOC118 PR122.
The existing integration manifest verifies NOC83–109, shared identifiers and the
complete28-migration chain. dependency-manifest.json records exact open PR heads
and actual ancestry rather than assuming any draft was merged into main.
37of38 reviewed PR85–122 heads are ancestors. PR86's current34e54e2 is a
documentation-only NOC82 audit side commit; its implementation baseb27e53a is
included. Its68-line report was reviewed; no application code/migration is missing
from that side commit. It is not silently merged/cherry-picked or called included.

## Verified correction: preserve release trust through backup/recovery

NOC118 retained a parsed approval in release.json, but stage() did not preserve
the original detached signature, independently trusted public key or bound
evidence files. A database/configuration backup alone therefore could not prove
the original release's authorization after losing the operator's separate files.

Production staging now reverifies exact captured signed bytes and all four
evidence envelopes and saves them under root-only release-trust/. SHA256 index
and SPKI public-key fingerprint accompany the original approval/signature/public
key. No private signing key is archived. Existing release inventory hashes bind
these files, and existing encrypted backups already include the staged release.
Production backup preflight verifies this capsule against the independently
trusted local release key. Missing, changed or wrong-key legacy capsules fail
before backup/migration; operators must prepare/review trust material rather than
silently constructing authorization. Key rotation requires an approved recovery
trust plan before replacing the independently trusted key. Test-only approvals
remain explicitly controlled-test and do not acquire production authorization.

Six local real-Ed25519 regression tests passed. They cover exact preservation,
private permissions/no private key, independent-key mismatch, missing signature,
changed evidence/index/manifest. Existing missing artifacts, image digests,
source mismatch, invalid signatures and unresolved-risk gates remain unchanged.

A second verified defect appeared in actual Redis Trivy metadata: Docker records
RepoDigests as redis@sha256:7f7d… while the unchanged source lock pins
redis:7-alpine@sha256:7f7d…. Literal comparison incorrectly rejected the same
repository and digest. The verifier now uses Docker-style canonical repository
identity, preserving registry ports and exact SHA256 while removing an optional
tag and normalizing Docker Hub's explicit library namespace. It still rejects
different repositories, different hashes and tag-only inputs. Six regression
cases include the actual observed Redis metadata shape. All signed source/image
bindings, four-role completeness, risk review and HIGH/CRITICAL gates remain.

APT refreshes in backend builds and installer dependency acquisition now use
APT::Update::Error-Mode=any. Partial repository failure cannot quietly rely on
incomplete/stale indexes. Python3.12, PostgreSQL16, Redis7 and app locks remain.
Native image evidence adds exact dpkg versions and SQLite compile options. FTS5
or session capability must not be treated as absent without checking the image.

## Vendor security findings

Historical NOC118 final scan: backend0CRITICAL/0HIGH/28MEDIUM/5LOW,33rows. Original
NOC11751HIGHrows/15CVEs remain reviewed; changing the distro's severity does not
patch a CVE. Current source package must be distinguished from similarly named
packages (notably sqlite3 versus obsolete sqlite). Current primary Canonical
lookups still show sqlite3CVE2026-119373, Expat113351 and util-linux76642 vulnerable;
ACL54369 ignored/backport too intrusive; Expat115506/117610/118370, SQLite119379 and
util-linux78410 need evaluation. Failed lookups remain unknown, not safe. Full
review rows and primary sources are in vendor-dispositions.json.
current-native-dispositions.json additionally retains every one of33current rows
and21distinct CVEs, including dash/GnuPG/GnuTLS/Python/systemd/shadow/tar, actual
source packages, historical severity, runtime exposure and lookup failures. Source
package exclusions for a different Python/GnuTLS package cannot clear a finding.
No unsupported
SQLite/Expat replacement, unstable repository, blanket ignore or VEX waiver is used.

Canonical ncurses2025-69720, systemd16742 and Perl9538 have previously verified
supported fixes; final installed versions must still be compared to vendor fix
versions. Util78408/78409 source exclusions are not package patches. Upstream
Expat2.10.0/SQLite3.54.x and util-linux advisories do not authorize replacing Noble
ABI packages. Images already apply available supported signed-repository updates;
final raw package inventories and full Trivy/SPDX reports remain authoritative
execution evidence. Original runtime exposure analysis remains in NOC118.

## Independent Ubuntu installation and recovery harness

tests/installer/pilot_vms.py requires explicit disposable GitHub CI execution.
It downloads only the pinned Canonical amd64 QCOW2 in deploy/pilot-vm-image.json;
checksum mismatch fails with no mutable fallback. The URL is a locator, not an
approval of future bytes. OS-image OpenPGP verification is not claimed. Two fresh
cloud-init instances have separate32GiB roots, machine IDs, SSH host identities,
kernels and Docker daemons, each2vCPU/5120MiB. KVM versus TCG acceleration and the
actual image checksum are recorded. Source is powered off before receiver
recovery. No shared application volumes or host filesystem mounts enter guests.
Guests share the CI hypervisor: this is independent-host software recovery, not
independent physical/off-site disaster acceptance. Local cloud storage has only
155MiB free and no QEMU/KVM; all large tests must execute on disposable CI hosts.

The source uses actual install/verify/upgrade scripts and existing systemd harness
through a healthy checkpoint after compatible upgrade and encrypted backup/restore.
It reboots its kernel and verifies installed services. A signed synthetic recovery
manifest binds backup ciphertext, checksum metadata, controlled-test release,
source Git bundle, exact image archive and synthetic test-side authentication.
The disposable Ed25519 private key stays on the source; public fingerprint goes
to the receiver through the separately authenticated controller channel. Synthetic
age recovery identity and test credentials travel privately and are never CI
artifacts. This is not a signed production release or fabricated registry evidence.

The empty second host verifies signature, independent fingerprint, all hashes,
required paths, source/image relationships and missing/wrong recovery keys before
writing application state. It restores PostgreSQL/configuration/encryption keys/
documents/Redis into its own empty installation, verifies migration compatibility,
original user authentication, audit and telemetry history, private TLS, services
and reboot. Existing installations are refused, not overwritten. Damaged backup
and incomplete artifacts must fail without destroying valid data. Source records
and backup are retained. private keys, decrypted backups and VM disks are excluded
from CI evidence uploads. Only sanitized summaries/checks are uploaded.

## Host security and operational limitations

Only the disposable guests receive firewall changes: default-deny inbound and
outbound; key-only SSH via the controller's loopback-forwarded address; narrowly
allowed DHCP; internal-only app networking and loopback private TLS dashboard.
No production SSH/firewall/VPN settings are touched. Guest SSH host keys are
provisioned and pinned before connecting, not accepted with StrictHostKeyChecking=no.
VPN identity is represented by an authenticated SSH control tunnel, not a tested
VPN or MFA product. Real VPN/MFA, approved NTP, unattended-update policy, independent
access review and off-site failure-domain acceptance remain BLOCKED. Actual UFW,
sshd, kernel/systemd and time-sync evidence is reported without assuming success.

## Actual malware scanner validation

The RF PostgreSQL job installs clamd and runs tests/test_pilot_security.py with
NOC119_REAL_CLAMAV=1. The actual unprivileged daemon uses a Unix socket and a small
safe EICAR test-signature database, with no live malware. Tests exercise clean and
infected streams, real socket timeout, scanner stop/restart, upload quarantine,
rejected approval while infected/unavailable, rescan/approval and document audit
events. Normal local runs skip these tests unless real deployment is requested;
requested-but-missing scanner fails. Synthetic scanner fixtures are not substituted.
The pilot VM's production scanner/definition-update deployment still requires
separate acceptance; a test-signature database is not current production protection.

## Licensing, compatibility and signing approvals

Redis7.4.12 remains exactly pinned. Its LICENSE.txt offers RSALv2 or SSPLv1; do not
apply Redis8's licensing choices retroactively. See licensing.md for primary terms,
distribution/service-review questions and unknown/custom dependency obligations.
Automated license labels cannot establish legal approval. See operator-acceptance.md
for production key custody, publication/digest/provenance/SBOM review, native vendor
contracts, actual firmware/OIDs/permissions, unsupported RouterOS6 REST paths and
disabled integration/notification policies. No live compatibility is claimed.

## Performance, tests and decision

NOC118 repeated1000device container medians16.115/13.317s, maxima17.493/13.765s;
four of twelve container rounds exceeded15s. This blocker is preserved. The new
pilot_benchmark.py repeats250/500/1000device workloads three times (six rounds per
size/repeat), explicitly limits polling to2CPU/768MiB and PostgreSQL to2CPU/1GiB,
uses an internal Docker network with no published ports, and records all rounds,
CPU/RSS/query latency, image IDs, samples and unique receipts. Actual core count
and host memory are reported; this is not live radio capacity or an SLA. Functional
success never converts a failed15second target to PASS.
The final profile uses a disposable named Docker volume for PostgreSQL data and
records fsync/synchronous_commit/full_page_writes. Initial source9545ecf used512MiB
RAM-backed data and measured18rounds10.018–11.883s (median10.9245s) on4cores/
16078424KiB host memory. This is retained as a diagnostic, not durable-storage
acceptance. Final named-volume measurements are required before evaluating target.

Initial full local suite:883passed/958skipped/2warnings; actual ClamAV/DB/VM checks
were not executed locally. Focused security/installer tests82passed/3scanner skips.
Ruff undefined-name checks passed. Installer Bandit11reviewed/0unreviewed/0HIGH.
Historical migrations and existing tests are retained; CI now requires the original
nine jobs plus the independent VM job. Terminal results, exact source SHA, package
versions, scanner findings and performance are appended to the draft PR ledger
after execution, never inferred from this pre-execution source snapshot.

Until supported vendor risk, real host/DR/scanner evidence, independent trust/legal
and operational approvals are complete: **NO-GO for operational release/live pilot**.

## Intermediate execution evidence (not final-source acceptance)

Source9545ecf/run639 freshly rebuilt backend image
sha256:1f4c6cc321e144e91392c6949e5341676443119fb457b9d1718fb1a28b1993b2:
0CRITICAL/0HIGH/33total. PostgreSQL derivative/frontend0findings; Redis1lower-severity.
Artifact14594172967, archiveSHA256463b66504d0422317a3ae69faedfa724f1819a469ea4f6487d346831017a70e9.
libsystemd0 is now255.4-1ubuntu8.19 (previously8.18); supported apt updates were
applied, but this does not resolve deferred121876 or all historical CVEs. Actual
SQLite compile options include ENABLE_FTS5 and ENABLE_SESSION, reinforcing rather
than waiving the native risk. Real ClamAV deploy/test steps completed SUCCESS.

The first independent VM attempt downloaded and checked the pinned image but
aborted on an individual8second SSH-readiness timeout before the600second boot
deadline. A bounded retry now handles that expected transport state while retaining
pinned host keys; no boot/install/recovery acceptance is claimed for the failed run.
The next source also adds strict flat/bounded recovery transport extraction with
traversal/link/duplicate/missing/overwrite regression tests, and records loaded
native libraries, capabilities and executable presence. Final source must rerun.

The next independent guest booted and installed its supported host packages, then
the actual installer failed closed at its complete dependency audit. HEAD-only
offline Git transport omitted PR86's non-ancestor original, which the unchanged
audit compares against its reviewed replay. The VM transport now includes all
existing refs and explicitly fetches their objects into an isolated audit namespace.
No missing-history check is bypassed. This failed attempt is not installation PASS.

Independent restoration now leaves a recovery-required marker and stops application
services on failure, retaining recovered state for investigation. Historical audit
and telemetry preservation checks use exact unaligned SQL counts; a psql `(1 row)`
footer cannot falsely satisfy a zero-count result. Success and interrupted-restore
guard tests cover preservation and service stopping without destructive cleanup.
