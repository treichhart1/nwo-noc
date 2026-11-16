# NOC-111 source, database and architecture audit

## Assessment and scope

**GO for the next isolated security/performance hardening milestone after final CI
passes. NO-GO for production or a live-equipment pilot.** This is an audit assessment,
not deployment authorization. No confirmed critical finding was reproduced. The
confirmed high-severity application findings below are corrected; high-severity
operational validation gates remain open and are explicitly distinguished from
confirmed code vulnerabilities.

Repository-wide inventory/static analysis and regression coverage were combined
with risk-based manual review of shared controls and sensitive paths. This is not
formal verification, exhaustive line-by-line proof, a penetration test against a
deployed host, legal advice or a compatibility certification for actual firmware.
No live equipment, production database/Powercode, external messaging, public incident
publication or other repository was used. AI Fiber Designer and MikroTik Management
remain separate applications. RF self-healing remains simulation-only.

## Repository integrity and provenance

Baseline: NOC110 draft PR114 at
`f3de701d29f6ee12b310bd27a6b9f617747e24fa`; Actions574/575 passed all8 jobs.
The working tree started clean. No `AGENTS.md`/CONTRIBUTING instructions were found
in the repository, tracked subdirectories, `/workspace`, or filesystem root.
README, Makefile, validation/installation scripts, CI, NOC110 manifest/configuration
inventory and release/module documentation were reviewed. No historical PR is
merged, closed, retargeted or rewritten. This branch is
`feature/noc-111-comprehensive-audit`, directly based on that exact NOC110 tip.

The NOC110 [manifest](audits/NOC-110-integration-manifest.json),
[full PR snapshot](audits/NOC-110-pr-snapshot.json) and
[dependency graph](audits/NOC-110-dependency-graph.mmd) remain authoritative for the
stack. Re-executing `scripts/release/integration_manifest.py` verifies112 direct
upstream PR ancestors plus the PR86 equivalent replay, unchanged merge-tree and
single28-revision migration chain. NOC110 itself adds one consolidation commit.
Main remains `5a5f21138339f522d7e6de6ad38b83f47d14f83c`, not the integrated candidate.
No missing milestone was inferred merely from green CI: source directories, API
contracts, registered metadata and cross-module workflow tests were also inspected.

There are no duplicate top-level function/class definitions within the scanned
application modules. Intentional late ORM registration/function-local cross-module
imports are retained. No competing tower/customer/device/work-order authority was
introduced. Build directories, Python caches, browser artifacts and private `work/`
evidence/backups are ignored; none is silently removed. A protected old acceptance
state in `work/` is not readable to the current user and was not opened/escalated.
No tracked backup, credential file, compiled output or node_modules was added.

## Findings and corrections

### A111-01 — High — private authentication abuse controls — corrected

**Affected:** `app/api/routes.py`, `app/core/login_guard.py`, `app/main.py`.
**Evidence/root cause:** the original login queried/verified credentials without an
application-level request budget; unknown users also skipped password hashing, and
large login forms were not bounded before parsing. This creates brute-force and
resource-abuse exposure even though the private interface requires a VPN deployment.

**Correction:** durable PostgreSQL budgets of30 attempts/account,120/peer and600
globally per60s. A transaction advisory lock serializes reservation; attempts commit
before password verification, including failures. Existing indexed AuditLog rows
hold HMAC scope keys, never raw usernames/IPs/passwords/tokens. Reads inspect only
the most recent bounded number of rows using the entity cursor index, not all
historical audit records. Authentication storage failure returns a sanitized503;
429 includes Retry-After60. Unknown/inactive users perform dummy verification.
Credentials are bounded before lookup and actual login body bytes are capped8KiB
before form parsing. Existing passwords/JWT issuance/roles are unchanged.

**Regression:** durable expiry, account/peer/global limits, failed-password
persistence, username rotation, concurrent reservations (exactly one winner),
unknown-user dummy verification and chunked oversized form rejection.
**Remaining risk:** application limits complement VPN/MFA/TLS/edge enforcement;
they do not prove resistance to distributed attacks or all timing analysis.
The peer is the trusted server-provided client address, never an independently
trusted X-Forwarded-For value. Proxies/NAT can conservatively share a budget.
Thresholds are reviewed constants, not a hidden runtime disable switch. Audit
growth needs an approved retention/capacity policy; no audit history is deleted here.

### A111-02 — High — bounded HTTP provider decoding — corrected

**Affected:** signup Turnstile/geocoder, read-only UISP/RouterOS HTTP, Grafana,
Powercode fixture transport and local explanation client.
**Evidence/root cause:** signup used eager `get/post` before checking response
length: a synthetic2MiB stream was consumed completely despite32/64KiB limits.
Other clients limited accumulated decoded bytes after HTTPX decompression, allowing
oversized compressed allocation before their application guard.

**Correction:** one dependency-free `app/core/http_json.py` helper bounds both raw
wire bytes and decompressed output before JSON parsing, with a total response-read
deadline. Gzip, zlib-wrapped deflate and raw deflate remain supported. Malformed,
unsupported or incomplete encodings fail closed. Existing client timeout/retry
limits, endpoint allowlists, TLS checks, approval conditions and sanitized error
codes remain. HTTP response contexts close promptly on rejection. Unused imports
made obsolete by this replacement were removed; no device operations were added.

**Regression:** bounded read/close, decompression bombs, valid compressed JSON/raw
deflate preservation, deadline expiry, and rejection without retry in read-only,
Powercode and local-explanation adapters. Existing Grafana/provider/firmware
contracts also run. **Remaining risk:** real provider/firmware compatibility is
unverified; unsupported encoding chains/concatenated gzip members are rejected,
not silently interpreted. This does not replace network-level egress restrictions.

### A111-03 — Medium — malformed Turnstile envelopes — corrected

**Affected:** `app/signup/providers.py`.
**Evidence/root cause:** JSON lists/scalars reached `.get` and raised AttributeError
instead of returning a failed verification. **Correction:** require a dictionary
and sanitize malformed/deep JSON, timestamp/decompression/timeout failures.
**Regression:** null/list/boolean/number/string envelopes never authenticate.
**Remaining risk:** actual Turnstile hostname/action configuration still needs
deployment acceptance; mocks are not a real anti-spam certification.

### A111-04 — Medium — concurrent logout revocation — corrected

**Affected:** `app/api/session_routes.py`.
**Evidence/root cause:** requests passing authentication concurrently could insert
the same revocation PK; the loser returned503 despite successful revocation.
**Correction:** PostgreSQL ON CONFLICT DO NOTHING, with audit creation only when
the presented token is newly revoked. The transaction remains atomic.
**Regression:** replay after parallel auth checks and real concurrent sessions
produce204/204, exactly one durable revocation and one logout audit. Existing JWT
identity/expiry/logout tests remain. **Remaining risk:** expired/previously rejected
tokens still receive401 from the ordinary authentication dependency; clients already
treat that as logged out. No other session is revoked.

### A111-05 — Medium — invalid legacy inventory updates — corrected

**Affected:** legacy device/link routes and DevicePatch contract.
**Evidence/root cause:** a nonexistent site FK was assigned before commit, causing
IntegrityError/500 and invalidating the session. Parent/link endpoint references
and explicit nulls for nonnullable monitoring settings had equivalent paths.
**Correction:** validate existing site/device identities before mutation; share link
endpoint validation across create/update. Reject explicit null monitoring settings
at schema validation while preserving omitted fields and legitimate nullable refs.
**Regression:** invalid site/parent/link endpoints fail with4xx before mutations or
success audits, the session stays usable, and omitted patch fields remain unchanged.
**Remaining risk:** source/topology uncertainty is not solved by FK existence;
documented forwarding/physical relationships still need independent validation.

### A111-06 — Medium — private response cache policy — corrected

**Affected:** private ASGI middleware.
**Evidence/root cause:** only selected sensitive route families had no-store headers.
Other authenticated inventory/operational responses relied on client/proxy behavior.
**Correction:** apply Cache-Control:no-store to all private `/api/` responses,
including denials, preserving separate public status caching and signup boundaries.
**Regression:** unauthenticated private endpoint responses have no-store; existing
public ETag/cache tests and financial/document/customer privacy regressions remain.
**Remaining risk:** deployments must purge old caches and enforce their proxy policy;
HTTP headers alone are not a browser-history or shared-workstation security guarantee.

### A111-07 — Medium — repeated site/device inventory scans — corrected

**Affected:** legacy site-summary collection route.
**Evidence/root cause:** each Site scanned the complete device list: O(sites×devices)
membership work despite only two SQL queries.
**Correction:** pre-index devices by canonical site ID and fallback legacy name,
then summarize each group once. Canonical IDs still override conflicting names.
Sorting, totals and imported-status health semantics are preserved.
**Regression:** canonical/legacy conflict membership and exact counters; rollback-only
profile asserts40 POPs and exact device counts with no network access.

| Devices | Before median ms | After median ms | Reduction | SELECTs |
| --- | --- | --- | --- | --- |
|250|8.47|5.86|30.8%|2→2|
|1,000|26.91|15.38|42.8%|2→2|
|10,000|347.63|214.21|38.4%|2→2|

[All five runs and resource scope](audits/NOC-111-sites-profile.json). This measures
complete ORM retrieval/summary serialization in warm local processes; database
server/container memory, HTTP transport and cold-cache production behavior are not
included. No fixed SLA is asserted. Empty imported inventory scores are historical
inventory semantics, not proof of live monitoring health.

### A111-08 — Medium — silent worker interruption — corrected

**Affected:** opt-in analytics and Fiber Designer preview worker loops.
**Evidence/root cause:** broad exceptions were swallowed with `pass`, concealing
failed rollups/previews from operators. **Correction:** emit static sanitized
warnings; preserve existing session rollback/close, cadence and approval boundaries.
**Regression:** injected DB failures close sessions and log interruption without
the synthetic credential in exception text. **Remaining risk:** central log collection,
alert routing and retention need operational configuration; no external notification
or automatic topology approval is introduced.

### A111-09 — Low — login accessibility — corrected

**Affected:** shared React login form.
**Evidence/root cause:** adjacent labels lacked label/input association; errors were
not announced as alerts. **Correction:** stable IDs/htmlFor, username/current-password
autocomplete semantics and an alert role. **Regression:** Chromium selects fields
by accessible label and verifies rate-limit error/no session creation.
No UI redesign or authentication transport change.

### A111-10 — Low — example network target — corrected

`.env.example` contained a concrete internal UISP address. It is replaced by
`https://uisp.example.invalid`. Its production status was not assumed or probed.
Integrations remain disabled by default, require approval, and do not activate merely
because a credential exists. This documentation-only correction needs no runtime test.

### A111-11 — Medium — supply-chain reproducibility — open

Runtime Python direct requirements and JavaScript dependencies are pinned; npm lock
has131 package entries and no missing integrity field in dependency entries.
Python transitive dependencies lack a hash-locked resolution. GitHub Actions use
mutable major-version refs and base images use mutable tags (`python:3.12-slim`,
`node:22-alpine`, `nginx:stable-alpine`, PostgreSQL16 and Redis7). Existing release
manifests pin built image IDs but do not make a fresh build independently immutable.
**Recommendation:** reviewed action SHAs, platform image digests, hash-locked Python
dependencies/SBOM, provenance and retained image archives in NOC112/113. No blind
major upgrade or unverified digest substitution is made here.

### A111-12 — Medium — unresolved scale limits — open

NOC110 exact-commit CI measured1,000-device RF rounds18.515–20.678s, exceeding a
15-second fleet cadence; tower ingestion used36,214 statements for3,000 samples,
and paged30-day RF history took5.56s for96,000 direct synthetic points. The main
frontend chunk still triggers existing >500KiB advice. These are measured risks,
not a claimed regression or production SLA. Site-summary improvement does not solve
the other workloads. Profile ingestion/query plans, retention/rollups, UI code
splitting, concurrent users and sustained worker resource contention in NOC112.

### A111-13 — Low — source ergonomics/unused-code advisory — open

Baseline Ruff F401/F841 inspection identified160 advisories (143 imports,17 local
assignments). Registration imports and side effects must be reviewed before removal.
Only imports actually made obsolete by the bounded-decoder replacement are removed.
Legacy compressed/one-line controllers and large React components remain maintainability
debt. No destructive cleanup or speculative dead-code deletion is performed.

### A111-14 — Informational — reviewed static results — triaged

Bandit1.8.6 reports83 final source results:52 synthetic acceptance assertions,
13 synthetic/noncredential token/password literals,15 SQL interpolation notices,
one subprocess import, one fixed-argv subprocess and one worker-ID `/tmp` notice.
No HIGH result is present. Fixed catalog SQL/validated identifiers/generated schema
names and bound values were reviewed; no SQL-injection exploit was reproduced.
The subprocess is a fixed bounded terrain child, not a generic shell. Worker-ID
markers contain random identifiers, not credentials, and destination replacement is
atomic in private container tmpfs. Assertions are acceptance checks, not auth guards.

[Each exact source fingerprint, context and justification](audits/NOC-111-bandit-review.json)
is retained. CI emits the full Bandit JSON, then rejects any HIGH, new/unreviewed,
changed, missing-file or stale result. `--exit-zero` only permits the reviewer gate
to consume the entire JSON; it is not the pass criterion. Negative gate probes for
new/HIGH/incomplete scans were verified. No blanket rule/filename suppression was added.
Ruff F821/F822/F823 reports0 undefined/export/local-reference errors across app/scripts.

### A111-15 — High operational validation gate — open, not a reproduced code exploit

Actual firmware/MIB/RouterOS permissions, approved one-device egress, VPN/MFA/TLS,
private Grafana authentication/datasources, Powercode/Fiber Designer contract and
consent validation, carrier idempotency/reconciliation/compliance, public WordPress
hosting/cache policy and a longer concurrent soak remain unverified. No synthetic
test establishes optical continuity, definitive outage cause or end-user restoration.
**Required:** separate approvals, minimum credentials, enforced allowlists, emergency
disable/rollback ownership and observed controlled-pilot evidence before activation.

### A111-16 — High operational/supply-chain gate — open, not a known image CVE claim

Language dependency scans pass, but no dedicated OS/container CVE scanner is installed
in this environment and none is run by current CI. Image builds are not an image CVE
audit. Off-host encrypted keys/configuration/document recovery, production backup
ownership, document malware-scanner deployment and full-host boot/firewall hardening
also remain unverified. Redis7's exact server image/version/license must be reviewed
independently of the MIT-licensed Python Redis client. LGPL psycopg/binary and native
GDAL/PROJ bundled notices require redistribution review. **Required:** review exact
platform images/SBOM/licenses and execute off-host recovery before production approval.

## Architecture and domain coverage

The registered schema has171 tables,370 FKs and240 metadata indexes; the private
application exposes329 path contracts/377 operations including the minimal health
route. [Schema inventory](audits/NOC-111-schema-inventory.json) records names/FKs/indexes,
not customer data. The single28-migration head stays0028_customer_status. No table,
saved reference, lifecycle history or migration was removed/renumbered.

| Domain | Reviewed ownership/control | Regression evidence |
| --- | --- | --- |
| Shared backend | one metadata/get_db graph, native auth/dependencies/role guards, bounded jobs and sanitized domain exceptions | release consolidation, final release security, full integration, new audit |
| Towers/devices/sectors/backhauls | Site/Device/Sector/NetworkLink remain canonical; source-normalization histories retained | inventory/reference tests, topology and tower-health suites |
| RF/RouterOS monitoring | exact GET/OID allowlists, disabled-by-default/admin guards, private credentials, timeouts/retries, missing/stale semantics | read-only transport, vendor/radio/microwave, RF acceptance and scheduler recovery |
| Historical RF/troubleshooting | separate raw contracts/projection, deterministic advisory evidence and simulation-only self-healing | historical analytics/troubleshooting/operations suites |
| Powercode/customer impact | canonical CustomerAccount/Service mappings; fixture-only HTTP or explicit local read-only PG contract; no production writer | Powercode, outage, notification and NOC110 workflows A/B/D/E |
| Public status/SMS | dedicated public-only ASGI, allowlisted projection, independent approvals, source-bound stability proof, native queue | customer-status64-test suite, notification/delivery/replay/consent/privacy tests |
| Signup | separate public ASGI, CSRF/Turnstile/rate/idempotency, encrypted PII, advisory qualification | signup contracts, mock providers, privacy and installation workflow |
| Inventory/IPAM | Asset is physical; Device is monitored identity; transactions/bindings reuse VRF/prefix/interface/site/service IDs | equipment/IPAM/concurrent movements/rollback/replacement tests |
| Field/QR/maintenance | stable private identifiers, technician assignment scope, one WorkOrder/occurrence engine, preserved histories | field, maintenance, secure uploads, NOC110 workflow C |
| Documents/planning files | bounded validation, private owned storage, checksums/versions, quarantine and role checks; bounded native processing | maintenance/document/planning suites and browser workflows |
| Unified/Fiber Designer | canonical operational node/edge/circuit/strand refs; approved external provenance; engineering stays external | unified/fiber reconciliation, conflicts, redundancy, draft rejection and rollback |
| Finance | Decimal periods/bases and explicit shared allocations; actual/estimated/collected separation, admin-only values/exports | finance/DB balance constraints, NOC110 workflow G |
| Grafana | approved links, numeric labels/no PII, private datasource views and least-privilege reader | Grafana contracts/history/role/privacy/missing-state suites |
| Frontend | native auth, role navigation, isolated public bundles, shared mobile field styles, actionable errors | TypeScript/build, all earlier browser stages and new audit tests |
| Workers | leases/receipts/outbox, unique maintenance occurrences, preview-only fiber sync, explicit rollups and bounded retries | duplicate/recovery/concurrency and actual service restart acceptance |

## Database, security and dependency validation

Fresh migrations, drift detection and repeated upgrade ran against new disposable
`noc111_clean`. Full regression covers rollback, invalid FKs, duplicate prevention,
concurrent inventory/authentication changes and historical ownership. CI retains
populated old-release upgrade, injected failure rollback, separate restore and
real PostgreSQL/Redis/worker restart acceptance. No production data was queried.
Auth budgets reuse the existing entity cursor index and append audit rows; no
schema upgrade is required. Counter history retention is explicitly not automated.

Static scans cover application modules and undefined names in release scripts;
dependency scans use pinned audit tools in `requirements-audit.txt`, outside runtime
images. pip-audit2.10.1 resolved55 Python packages with0 known vulnerabilities;
npm audit reports0 at all severities across130 installed dependency entries.
These are time-bound registry results, not proof of absence of undisclosed flaws.
[Runtime license metadata](audits/NOC-111-runtime-licenses.json) covers23 pinned direct
packages; inspected license files fill missing pysnmp/lazrs metadata. Most are
permissive, with LGPL psycopg and native/binary notices needing explicit packaging
review. Transitive/frontend/native license aggregation and image CVEs remain a gate.

## Reproduction, testing and evidence

The initial narrow reproduction had8 failures/2 passes. Fixed defects and new
controls now have36 audit regressions, including actual concurrent PostgreSQL
sessions in uniquely named synthetic schemas that are cleaned up only after their
own tests. HTTP uses in-memory MockTransport; no live source/provider is needed.
The combined transport/provider/security regression selection passed337 tests,
including the shared-reader edge regressions; final full counts and final SHA/Actions
results are recorded in the draft PR after execution. Focused suites overlap with
the complete backend suite and must not be summed as unique tests.

Chromium audit checks:2 passed/4.0s. Private/public-status/signup builds and strict
TypeScript passed. Python compilation, whitespace checks and the static gate passed.
The earlier full run was intentionally interrupted when the shared HTTP correction
expanded; it is not counted as passed. The final source is rerun completely.
[Structured evidence](audits/NOC-111-evidence.json) and
[before/after profile](audits/NOC-111-sites-profile.json) contain no secrets/PII.

Reproduce locally only with disposable PostgreSQL and generated nonproduction env:

```bash
python scripts/release/integration_manifest.py
cd backend
python -m pytest -q
python -m pytest -q tests/test_comprehensive_audit.py
# Only explicit APP_ENV=staging OFFLINE_MODE=true SYNTHETIC_MONITORING_ENABLED=true:
python -m app.audit_sites_benchmark
```

CI adds the audit regressions/profile, accessible-login browser checks, undefined-name
scan and exact reviewed Bandit gate while retaining all existing checks. It does not
disable tests, assertions, roles, migration checks or external-delivery restrictions.

## Release-readiness checklist

* [x] Exact NOC110 provenance verified; intended NOC83–109 modules retained.
* [x] Confirmed source corrections reproduced and regression-covered.
* [x] Canonical IDs, migrations, FK/unique/index and history ownership retained.
* [x] Public/private API separation, notification approvals and financial/document
  permissions preserved; API no-store and login budgets strengthened.
* [x] Synthetic read-only adapter/provider/recovery tests and measured profile.
* [x] Language dependency/static scans performed with explicit triage/limitations.
* [ ] Final-commit CI completion/counts: authoritative evidence is the draft PR.
* [ ] Resolve A111-11/12 supply-chain and scale hardening before production sizing.
* [ ] Close A111-15/16 operational/security verification gates before live activation.
* [ ] Approved image/configuration/secret/file backup archives and off-host recovery.
* [ ] Final Ubuntu host installation package, TLS/VPN/MFA/egress controls and named
  rollback/emergency-disable owner independently accepted.

No production readiness declaration, merge, deployment, real notification, live
network change or separate-application modification is authorized by this report.
