# NOC116 release blocker remediation

## Executive decision

**NO-GO for operational release or live-equipment pilot.** Code-level corrections,
synthetic contracts and controlled installation tests cannot satisfy real equipment,
provider, key-custody, licensing, network-access or independent disaster recovery
acceptance. Further independently approved isolated installation testing is supported.
This assignment does not authorize deployment or a pilot.

Exact starting source: NOC115 PR119, commit
`6de7931f94a0b79f3808af84b182492c59fc5d1c`, tree
`75dc6d6e6b4e92ddbf3b95f60e5be2b0031dac91`. Fresh GitHub verification confirmed
open/draft PR119 targets NOC114, CI601/602 each9/9 success on that exact commit.
The working tree was clean. No AGENTS.md/applicable contribution file was found in
workspace/root/tracked subdirectories; existing reports, scripts and instructions
were reviewed. New branch feature/noc-116-release-blockers directly preserves the
315-commit/118-upstream-PR/28-migration stack. No PR is merged/closed/retargeted.
Final source SHA/workflow evidence is recorded in the new draft PR ledger; this
report does not invent its own self-referencing commit hash.

Read with [NOC115 acceptance](../NOC-115/README.md), its matrix/manifest and the
final PR119 ledger (later successful executions are not erased by historical pending
notes in NOC115). Main is not the integrated feature candidate.

## Safe corrections

| ID | Correction | Regression scope / remaining limits |
|---|---|---|
| N116-01 / A111-12 | Lock/query per-device RF alert inventory once per processing transaction instead of a SELECT per rule; reuse only within that call | PostgreSQL query-budget and alert evidence/staleness tests, full monitoring regressions. No cross-poll cache, missing-data, lease, receipt, dedup or recovery thresholds weakened |
| N116-02 / A111-16 | Production-purpose release approval must carry valid detached Ed25519 signature verified with separately root-trusted key before install/upgrade | Real ephemeral test-key valid/tampered/unsigned verification; controlled-test mode unchanged and cannot authorize production. No actual release key/artifact publication supplied |
| N116-03 / A111-16 | ClamAV bounded complete INSTREAM reply parsing, exact clean response; fragmented replies supported, truncated/malformed/ambiguous replies fail closed | Mock fragmentation/error/infected/timeout plus existing real Unix protocol tests. Not proof of an installed/deployed scanner |
| N116-04 / A111-16 | Legacy password argv reset removed; hidden confirmed prompt/protected bounded stdin, explicit recovery approval/reason and transactional audit; old sessions invalidated by recovery audit time | PostgreSQL password/audit/session regression; invalid input and argv non-echo tests. Privileged human identity still requires console logs; not a public recovery endpoint |
| N116-05 / A111-15/16 | Synthetic compatibility fixture, deterministic source-lock SPDX inventory, native image CVE/license evidence collection and retained CI artifacts | Existing and new contracts tested; NOASSERTION/unknown licenses and high native findings remain blockers. Scanning execution is not release approval |

No historical migration or shared authoritative inventory was replaced. No major
runtime dependency or Redis implementation was changed. RF remediation remains
simulation-only; provider/device/publication/messaging flags remain disabled.

## Compatibility and contracts

[Compatibility matrix](compatibility.md) records collection method, firmware/API
uncertainty, capabilities, unsupported features, read permissions, intervals,
timeouts, TLS/SNMP requirements, egress, credentials and failure behavior for every
requested vendor. **ROS6 live REST and certified Wave collection are not implemented**;
ROS6 export normalization is not live monitoring. Profile names do not establish
AF11/Ray3/Aviat/ePMP firmware/OID compatibility.

The Powercode fixture uses3 synthetic accounts/5 services, stable IDs, multi-service
ownership, active/suspended/disconnected/unknown states and tower/sector references.
It supplies no consent. Existing targeting regressions cover duplicates, stale
mapping, inactive services, opt-out, contacts, uncertain impact and alternate paths.
Native Powercode fields/view/API/permissions and consent provenance remain BLOCKED.
Grafana, WordPress, SMS and Fiber Designer contracts are explicitly separated from
unverified deployment/provider interoperability in the same matrix.

## Performance evidence

[Raw before/after measurements](performance.json) are real local Python3.12,
PostgreSQL16,40POP/1,000device synthetic2ms-transport runs,8 threads,6 rounds each.
The baseline processor is loaded from the exact NOC115 Git blob, then the same
benchmark runs against the corrected processor in a separate schema. Background
focused tests ran intermittently; this is not a dedicated production sizing trial.

| Metric | Before | After |
|---|---|---|
| Round range |27.642–31.646s |16.851–19.398s |
| Median round |30.8115s |18.900s |
| Unique receipts/source samples |6,000/6,000 |6,000/6,000 |
| Monitoring-health query |52.44ms |38.84ms |

Median improved about38.7%; **15-second target still FAILS locally**. No interval,
concurrency or correctness threshold was relaxed to produce the improvement. Query
budget regression limits process() to at most3 SELECTs while preserving an actual
three-sample overheating alert and unknown-not-cleared stale evidence. Full CI runs
250/500/1,000device workloads, actual worker/DB/Redis recovery, historical analytics,
topology/customer-impact and resource measurements; final results belong to the PR
ledger. Do not relabel another runner's result as this local measurement.

Initial pilot proposal is120s+ intervals/concurrency1 for individually approved devices,
not a guarantee based on the synthetic benchmark. Sustain realistic history, failures,
full adapter deadlines and concurrency/CPU/memory monitoring before revising intervals.

## Supply chain, artifact and host evidence

[Trusted-artifact workflow](trusted-artifacts.md) gives exact signing/verification
commands, independent public-key bootstrap, immutable build/provenance requirements,
SBOM/scanner scope, license review and explicit remaining trust decisions. No production
artifact or signature was fabricated. Native scan findings are retained separately;
job success means execution, not absence of HIGH/CRITICAL issues or license approval.
The previous strict dependency/static gates remain enforced. Root signature verification
uses fixed /usr/bin/openssl, private temporary files, bounded timeout, no shell/download.
Two exact new LOW Bandit fingerprints were reviewed with source rationale; no broad
ignore or severity assertion was relaxed. Backend reviewed findings remain unchanged.

[Operator acceptance](operator-acceptance.md) covers read-only host inspection,
private bindings, systemd/SSH/VPN/MFA requirements, secure administrator recovery,
scanner deployment, second-host DR, controlled pilot preparation and emergency stop.
Do not install a public proxy, alter firewall/SSH/VPN, disable offline guards or copy
secrets into worksheets. Public source-lock inventory contains no production secrets.

## Executed evidence and installation scope

- Local initial new security cases:17 passed/2 DB-dependent skipped. Subsequent real
  disposable PostgreSQL execution:27 passed before the later fixture/SBOM/argv cases.
- Focused monitoring, maintenance/documents, Powercode, host installer and cross-module
  regression run:261 passed,2 warnings,70.65s. Counts overlap; later tests/final CI are
  recorded in the draft PR, not retroactively counted in that run.
- Local full non-DB backend run:844 passed/954 DB-dependent skipped/2 warnings in23.73s (before the final argv rejection case). Frontend typecheck/build passed in4.19s. Counts overlap with focused runs.
- Local28 fresh migrations applied successfully; no historical revision changed.
- Ruff undefined-name checks pass. Backend Bandit83 reviewed results/no HIGH or new
  unreviewed findings; host11 reviewed results/no HIGH/unreviewed findings.
- Deterministic source-lock SPDX inventory generated. Full backend/frontend, security,
  image scans and actual disposable Ubuntu host/upgrade/restore/browser jobs run on
  the final commit in GitHub Actions. Do not claim those results until completed;
  the final immutable run/job links and exact counts are maintained in the draft PR.

Local environment is not a suitable systemd Ubuntu VM. Disposable synthetic PostgreSQL
runs locally; actual systemd installer/verify/upgrade and same-host encrypted complete
restore are rerun by hosted Ubuntu CI. Minimal-ISO Docker bootstrap, full host reboot/
power-loss, independent second-host key/file/DB recovery and deployed scanner remain
NOT TESTED. Prior NOC115 evidence is historical, not execution of new source.

The local disk filled during creation of a new VFS-backed disposable benchmark
container before app import. Only that failed, data-free NOC116 container was removed
to recover space; existing images/databases/backups/containers were preserved. The
benchmark then ran locally against the dedicated synthetic database. Do not infer
production storage sizing from this cloud workspace limitation.

## Remaining blockers and acceptance

[Machine-readable blocker matrix](blockers.json) contains original evidence,
reproduction, corrections/tests, external dependency, operator action and only
PASS/FAIL/BLOCKED/NOT TESTED statuses. Code-level rows may pass independently; parent
A111-15/16 remain **BLOCKED**. A111-12 local15s target remains **FAIL** despite improvement.

Operator must independently approve vendor firmware/read-only credentials/OIDs,
Powercode mappings/consent, real providers, published trusted signed artifacts,
image/native CVEs and Redis licensing, AV engine/signatures, private VPN/MFA/egress,
minimal-host startup and off-host complete recovery. There is still no reviewed
host offline-to-live activation configuration. Do not bypass host safety validation.

No production/live network/Powercode access, real messages, public incidents,
deployments, merges, equipment changes or other repository changes occurred.

## Newly executed native-image findings — release blocked

The first actual Trivy job603/133669768022 retained artifact14279351987. Its
[exact baseline findings](native-image-baseline.json) include backend52 HIGH/224 total,
frontend3 HIGH/5 total, PostgreSQL1 CRITICAL+25 HIGH/62 total, Redis0 HIGH/CRITICAL/1 total.
The worker is the same backend image ID and must not be counted twice. These are
scanner package findings, not demonstrated exploits; none are suppressed or waived.

The PostgreSQL CRITICAL finding CVE-2025-68121 is in the bundled /usr/local/bin/gosu
Go1.24.6 executable, not a claim that the SQL engine itself has that specific defect.
Other bundled Go findings require a vendor-published/rebuilt compatible PostgreSQL16
image with patched toolchain (or independently reviewed VEX/reachability evidence).
Removing/replacing the privilege-drop tool without validated initialization/upgrade/
backup behavior is not a safe blind fix. Upstream artifact/build trust remains blocked.

Supported same-distro patch updates are applied to backend zlib1g (fixed version
reported by scanner for CVE-2026-85091) and frontend libxslt (three fixed HIGH findings).
No Python major, PostgreSQL major, Redis implementation or distro was replaced.
Final image scans must verify resulting package versions/findings; package indexes
are not a bit-identical build guarantee. Immutable published digests and retained SBOM
bind the actual release. Many Debian HIGH findings reported no FixedVersion and remain
open; green dependency audits do not overrule these native results.

The image evidence tool de-duplicates image IDs, shares only a CI scanner database cache,
retains license-review BLOCKED status, and supports --require-no-high for a fail-closed
release gate after saving results. Development CI collects evidence informationally;
it does not authorize publication, override critical findings or pass a release gate.
Source SBOM now preserves only recognized SPDX declarations, records ambiguous licenses
as NOASSERTION, and percent-encodes scoped package URLs. Formal legal approval is absent.
