# NOC-112 security and performance hardening

## Decision and provenance

**NO-GO for production or a live-device pilot.** This candidate is suitable for
continued isolated packaging validation only after its final CI passes. Operational
security and real-device acceptance remain release gates, not accepted risks.

Branch `feature/noc-112-final-hardening` starts at the exact NOC111 draft PR115
commit `3901106c7ed848a0d786e36bd3d4a69e99d2a8c1`. Its CI578/579 passed. No PR was
merged, closed, retargeted or rewritten. Main remains outside the development stack.
The NOC110 integration manifest and dependency graph remain authoritative:112 direct
upstream ancestors and one equivalent PR86 replay, preserving NOC83–109 functionality.
Review/integrate upstream PRs in the manifest order, then114,115, and this PR.
No repository AGENTS.md or additional contribution instructions were found.

No separate application's repository, live equipment, production database,
Powercode service, messaging provider or public website was accessed. All telemetry,
customer records and device endpoints used in tests are synthetic. RF remediation
remains simulation-only; integrations and external delivery remain disabled by default.

## Finding disposition

The identifiers refer to the reproduction/root-cause detail in the
[NOC111 audit](NOC-111-comprehensive-audit.md). No finding is silently waived.

| Finding | Severity | NOC112 disposition and evidence |
|---|---|---|
| A111-01 | High | Resolved, reverified: durable login budgets, bounded body, dummy verification, failure-closed storage and sanitized errors retained; authentication regression suite. |
| A111-02 | High | Resolved, reverified: bounded raw/decompressed provider responses, TLS, exact endpoint allowlists and read-only requests retained; compressed-bomb/deadline regression tests. |
| A111-03 | Medium | Resolved, reverified: malformed Turnstile responses rejected; mock-only provider tests. |
| A111-04 | Medium | Resolved, reverified: concurrent logout uses unique conflict-safe revocation and one audit event. |
| A111-05 | Medium | Resolved, reverified: null monitoring values and invalid device/site/link references rejected. |
| A111-06 | Medium | Resolved, reverified: private API no-store; public cache policy remains separate. |
| A111-07 | Medium | Resolved, reverified: site/device bucketing preserved, two SELECTs; no competing inventory. |
| A111-08 | Medium | Resolved, reverified: sanitized analytics/fiber worker recovery warnings retained; bounded retries and opt-in scheduling preserved. |
| A111-09 | Low | Resolved, reverified: labelled login inputs, autocomplete and accessible errors; actual Chromium tests. |
| A111-10 | Low | Resolved, reverified: example provider endpoint remains example.invalid. |
| A111-11 | Medium | Remediated: runtime/test/audit transitive SHA256 locks, immutable Action commits and image index digests; fail-closed consistency gate. Host package repository reproducibility/scanning remains under A111-16. |
| A111-12 | Medium | Partially remediated: tower batch query reduction and frontend code splitting measured below. Native1000-device polling, long history queries and sustained throughput still require remediation/sizing. |
| A111-13 | Low | Partially remediated: two demonstrably unused standard/library symbols removed. Registration/re-export imports and unproved dead code preserved; no repository-wide autofix. |
| A111-14 | Informational | Reviewed, reverified: all83 exact Bandit fingerprints unchanged, zero high/unreviewed findings; hard gate retained. This is not proof of vulnerability absence. |
| A111-15 | High operational | Blocked by external validation: real firmware/OIDs, approved one-device VPN/TLS/egress, Grafana auth/datasource, customer mappings/consent, carrier ambiguity and WordPress acceptance. No production access authorized. |
| A111-16 | High operational/supply-chain | Partially remediated immutable images; still blocked: OS-image vulnerability assessment, exact Redis server licensing approval, off-host recovery/keys, deployed AV, host firewall/boot hardening. No claim of an identified image CVE or complete license clearance. |
| A111-17 | High | Resolved, reverified: pytest9.0.3 retained and hash-locked; expanded dependency audit includes test/audit tools, not runtime alone. |

No new critical vulnerability was reproduced in this focused hardening pass. This
is not exhaustive source proof or a deployed-host penetration test. Any risk
acceptance requires an explicit operator decision; none is inferred here.

## Changes and preservation of behavior

Tower-health consumption already acquires its transaction advisory lock before
reading a bounded batch of at most200 stored samples. Previously each row then
reloaded policy and reacquired the same lock, adding redundant round trips. The
worker now calls an internal locked-ingestion helper with one batch policy snapshot.
The public ingest function still acquires the lock and validates policy normally.
Every row retains its savepoint, canonical inventory checks, observation hashes,
same-time conflict handling, daily accounting and power-to-outage correlation.
No deduplication, authorization, transaction or recovery checks were removed.
The regression test verifies one lock/policy read per batch and no samples on replay.
Existing rollback/malformed-payload/restart tests remain in place.

Thirteen page-only frontend modules now use React.lazy with a shared Suspense
boundary. Routes, props, role restrictions and API permissions are unchanged. No
warning threshold was increased. Chromium verifies the RF chunk is absent on the
initial dashboard, fetched on navigation, and navigation back retains the shell.
Only unused `ipaddress` and SQLAlchemy `func` imports were removed. Intentional
model registration, imports with possible side effects and historical files remain.

No database migration was added, removed, renumbered or edited. There are still28
ordered migrations with single head `0028_customer_status`. No persistent entity
identifiers or authoritative source boundaries changed. Fresh migration, repeat
upgrade and supported0011-to-head upgrade checks show no schema drift.

## Measured performance

Same host, PostgreSQL16, synthetic40-POP topology, three frames/device, transactions
rolled back between runs. These are single before/after runs, not p95/p99 or a
production capacity certification. Runs shared the cloud host with other checks;
SQL statement counts provide the more repeatable evidence.

| Devices | Samples | Ingest before/after ms | SQL statements before/after | SELECTs before/after |
|---:|---:|---:|---:|---:|
|250|750|6856.08 /6151.29|9159 /7659|3572 /2822|
|500|1500|16100.52 /12757.71|18185 /15185|7092 /5592|
|1000|3000|39647.94 /32312.17|36214 /30214|14116 /11116|

At1000 devices this removes6000 statements (16.6%) and3000 SELECTs (21.3%);
elapsed ingestion falls18.5%. The same289 maintenance findings and1670 synthetic
customer-service relationships remain; no duplicate findings or external calls.
Assessment remains93 SELECTs and675 statements,3809.32→3673.88ms; peak process RSS
183052→184592KiB. Repeated assessment6131.03ms after versus4438.88ms before shows
this change does not establish an improvement in every phase. It does not fix the
independent native RF polling throughput limit reported by NOC111.

Frontend entry chunk504.87→353.11kB (30.1% reduction), gzip144.93→106.32kB;
build5.21→5.02seconds. Vite's500kB chunk warning is gone. This reduces initial JS
transfer, not total module bytes or a measured browser rendering latency.

## Reproducible inputs and security checks

`backend/requirements.lock` contains55 runtime packages; `requirements-test.lock`
contains60; `requirements-audit.lock` contains33 audit packages constrained to the
same overlapping versions. Combined advisory coverage is84 unique packages. Direct
application versions and pytest9.0.3 remain pinned. Locks were resolved with
CPython3.12 and pip-tools7.5.2; installation uses `--require-hashes --only-binary=:all:`.
Linux amd64 wheels were actually installed in a fresh virtual environment and the
backend image. Other architectures require their own installation/native tests;
recorded hashes alone are not architecture compatibility evidence.

Regenerate deliberately from repository root with a writable cache, first runtime,
then tests constrained by runtime, then audit constrained by tests:

```sh
PIP_TOOLS_CACHE_DIR=/tmp/noc-pip-tools pip-compile --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --no-emit-trusted-host -o backend/requirements.lock backend/requirements.txt
PIP_TOOLS_CACHE_DIR=/tmp/noc-pip-tools pip-compile --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --no-emit-trusted-host -c backend/requirements.lock -o backend/requirements-test.lock backend/requirements.txt backend/requirements-dev.txt
PIP_TOOLS_CACHE_DIR=/tmp/noc-pip-tools pip-compile --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --no-emit-trusted-host -c backend/requirements-test.lock -o backend/requirements-audit.lock backend/requirements-audit.txt
python scripts/release/check_supply_chain.py
```

Review all version/artifact changes and rerun tests/audits before committing. CI
checks direct constraints against locks, matching overlapping versions, hash
presence, exact40-character Action SHAs and consistent immutable image references.
GitHub action SHAs were resolved from the current v4/v5 refs through GitHub metadata;
registry multi-platform index digests are recorded in `deploy/image-lock.json`.
Versions/majors were preserved. Digests prevent silent drift, not malicious publisher
content. Local application tags remain build handles; the existing release identity
capture records immutable local image IDs and refuses silent repinning. Optional
Grafana still requires an explicitly reviewed image digest.

Runtime/test/audit PyPI vulnerability queries returned zero known advisories across84
packages; npm audit returned zero. This does not include OS/native-library CVEs or
future advisories. Full Bandit83-result review and undefined-name checks pass.
The backend container builds with hash validation and imports native GIS libraries
with `--network none`. Compose validation preserves loopback-only frontends/backend
and unpublished PostgreSQL/Redis/workers. No security gate was disabled.

## Validation ledger

Final commit CI links, exact test counts and remaining workflow failures are recorded
in the draft PR; CI is authoritative for that exact commit. Local machine-readable
before/after evidence is in [NOC112 evidence](audits/NOC-112-evidence.json).

Focused PostgreSQL tower/audit regressions:112 passed. Final unit run:764 passed,
949 database-dependent skips, two warnings,10.23seconds. Supply-chain negative and
consistency/workflow tests:12 passed. Actual Chromium focused tests:five passed, zero skipped
in9.6seconds, covering authentication/logout, shell role restrictions, actionable
errors and lazy navigation. Private, signup and public-status frontend builds pass.
Full PostgreSQL-backed regression and isolated Ubuntu/recovery results are recorded
in the final-commit PR validation ledger; these remain required completion gates.
No live firmware, production operator workflow or physical host boot acceptance is
claimed from mock/synthetic or Ubuntu-userspace-container tests.

The first published workflow run580 failed before job creation because an unquoted
pip command contained YAML-significant `:all:` syntax. Commands are now quoted and
a regression test parses all eight jobs and checks shell-step types. An early local
full run loaded a five-image assertion before the sixth pin was added; the final
source assertion and negative tests pass. The final PR ledger distinguishes these
initial failures from the subsequent final-source validations. No assertions or
security checks were weakened.

## NOC113 installation prerequisites and recovery gates

Reuse [the Ubuntu installation and recovery guide](NOC-84-installation-and-recovery.md)
and existing `scripts/release/` tooling. Do not replace it with a second installer.
The supported clean host is Ubuntu24.04LTS; bootstrap installs Python3 and Docker
Compose, while application containers use CPython3.12, Node22 for builds, PostgreSQL16,
Redis7 and nginx. Optional Grafana requires an approved pinned image, private
credentials and tested least-privilege authentication; anonymous access stays off.

Before host packaging acceptance, record CPU/RAM/disk budgets measured under the
chosen collection interval, the immutable release manifest, OS patch/scanner results,
server Redis licensing, and required GDAL/PROJ/psycopg notices. Node is not a runtime
requirement when using prebuilt frontend images. Python/native hash-lock validation
must run for the actual host architecture.

Use private/VPN access, TLS and exact egress allowlists. Validate firewall, nginx
headers, systemd/Docker boot and restart policy on a disposable full host; the current
Ubuntu userspace rehearsal cannot establish host boot/firewall correctness. Supply
unique nonproduction rehearsal secrets; enable no integration or public gateway by
copying an example file. Keep Redis/PostgreSQL unpublished and document directories
private, persistent and writable only by the intended service UID. No new daemon,
worker loop, outbound destination or schedule was introduced here.

Back up PostgreSQL plus encrypted environment/keys, documents, signup-private and
terrain files, queue/spool volumes and release manifests consistently. Test off-host
restoration with the matching source/image identity, verify checksums and role
permissions, and exercise the existing failed-upgrade rollback procedure before
activation. Do not downgrade a populated schema blindly or delete failed release
volumes. Malware-scanner deployment and retention/key rotation require operator
configuration; existing safe hooks are not proof of an installed scanning service.

Health acceptance must include API/frontend, database migration head, Redis, worker
heartbeat/queue backlog, collector freshness, alerts/incidents, maintenance,
notification approval gates and restore verification. Stop or emergency-disable
integrations locally before investigation; never reboot or reconfigure equipment.
A one-device pilot requires a separately approved device, credential scope, firmware
contract, rate limit, TLS/endpoint policy, rollback and observation window. No such
approval or connection occurred during this milestone.
