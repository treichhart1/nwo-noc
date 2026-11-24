# NOC-120 — final candidate reconciliation and operator handoff

**NO-GO for production release or live-equipment pilot.** This is a finite handoff for independently authorized isolated Ubuntu24.04 installation testing. No production publication/signing, server change, live collection, customer notification or public incident is authorized.

## Source and scope

Start: NOC119 draft PR123, exact commit60a0a34260f70fac8c7b5e9e00c263d38d1f28d8, tree1b5fbac702af60d505fa62366703e72c7453c8ed; run730 (45127023991) independently confirmed SUCCESS with all10jobs. New branch feature/noc-120-release-handoff is directly stacked on that commit and PR123’s feature/noc-119-pilot-security branch. No existing PR is assumed merged. The inherited NOC110 integration manifest and NOC119 dependency-manifest.json preserve the full reviewed stack and its documented NOC82 audit-side-commit exception.

Current workspace was clean at715a74956dd633e86315dd5d323fefed8e32b3cc with zero available writable bytes. Sandbox mount creation fails ENOSPC; read-only inspection required approved escalation. Existing local work, images, volumes, history and migrations are untouched. Source changes are delivered through GitHub expected-parent fast-forward commits, with actual builds/tests on disposable CI runners. No production or other repository is touched. No applicable AGENTS.md was found in accessible workspace/source ancestry; protected historical rehearsal directories were not altered.

This milestone reconciles acceptance, removes one verified redundant monitoring query, prepares a fail-closed unsigned evidence assembler, and supplies operator procedures. It does not add new network functionality, replace vendor libraries with unsupported ABI builds, create another NOC application or activate integrations.

**Exact final source/tree, new PR URL, all10 terminal job conclusions, fresh image identities/counts and measured rounds are recorded in this branch’s draft PR ledger after execution.** This source report cannot attest its own future CI. Pending/NOT TESTED entries are not PASS. Eighteen exact offline publication/archive regression cases were executed successfully in newly created disposable RAM-only directories, and six changed Python sources compiled in memory. No local container/VM rebuild is claimed; those require disposable CI.

## Reconciled historical acceptance

The original NOC119 blocker matrix was a conservative pre-execution snapshot. It now retains the entire original object under historical_snapshot and annotates executed results with exact source/run730; historical failures are preserved in STARTUP-FOLLOW-UP.md and prior Actions runs.

| NOC119 requirement | Final executed evidence |
|---|---|
| All10CI jobs | PASS, run730 exact source above; push729 also10/10 |
| Independent Ubuntu installation/provisioning/service verification | PASS, actual24.04 kernel,4vCPU/5120MiB/32GiB |
| Source kernel reboot/post-boot verification | PASS, changed boot ID,56.399s in730 |
| Guarded upgrade/encrypted backup | PASS |
| Signed source-off second-VM transport and restoration | PASS, distinct root/kernel/daemon, source powered off |
| DB/config/documents/keys/original authentication/audit/monitoring/Redis preservation | PASS |
| Recovered kernel reboot/post-boot verification | PASS, changed boot ID,58.445s in730 |
| Wrong key/corrupt/incomplete artifacts/existing-install refusal | PASS |
| Eight exact installed-image scans |0HIGH/0CRITICAL; backend variants34lower findings each, Redis1 at historical execution |
| Actual ClamAV engine/fault/quarantine/approval tests | PASS with safe test definitions; not production definition currency |

Canonical guest build20261017 SHA25604e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94; pinned image checksum is not a claimed OpenPGP publisher verification. Both VMs shared one CI hypervisor: physical/offsite disaster acceptance remains unverified. NTPSynchronized=no was actual evidence, not a passing time-sync test.

NOC119 per-run overlapping collections: backend1026passed/958skipped, RF/PostgreSQL1103passed, installer/security152passed, Grafana43, communications64, integration54, audit36,69successful Playwright executions across27invocations. Do not sum them as unique tests. New-source results belong in the final PR ledger.

[NOC-119-EVIDENCE.json](NOC-119-EVIDENCE.json) pins all five run730 artifact IDs, compressed sizes and GitHub-reported SHA256. New dependency-security CI downloads those exact retained archives with read-only Actions permission, independently hashes their bytes and inspects bounded JSON without filesystem extraction. It retains a separate reconciliation report. Failed download/expiry/digest mismatch fails rather than fabricating evidence. Until that job completes, archive hashes are GitHub-reported only, not independently rehashed by this workspace. Original full logs/Trivy/SBOM/provenance stay intact.

## Remaining security review and supported updates

[VENDOR-REVIEW.json](VENDOR-REVIEW.json) records primary Canonical source-package status checked2026-11-24, including every distinct CVE from the inherited native disposition set and selected scanner-coverage gaps. Individual SQLite CVE page timed out; Canonical’s sqlite3 package index explicitly confirms Noble Vulnerable. Unavailable lookups are retained, never called safe.

No new supported Noble fix was established for the material outstanding SQLite119373, Expat113351/117610, ACL54369 or util-linux76642 issues. Other deferred/evaluation findings remain blocked. ACL’s intrusive-backport decision and unrelated package/source exclusions do not patch installed code. Upstream SQLite/Expat/util-linux major versions do not justify an unsupported Noble library replacement.

Existing backend builds already apply supported signed-repository updates with APT::Update::Error-Mode=any. Fresh diagnostic run732 rebuilt at software commit42d87ab0c7af166f823f78468c0eabe0ecf771a5 and installed libsqlite3-0 3.45.1-1ubuntu2.11 and libexpat1 2.6.1-2ubuntu0.6. Candidate scan then reported backend34total/0HIGH/0CRITICAL, hardened PostgreSQL0, frontend0, Redis11total/0HIGH/0CRITICAL. **Redis’s historical1finding is not carried forward as a current count.** Exact final-source scans may differ and supersede diagnostic counts in the PR ledger. Full raw inventories and package-specific findings are retained; no findings were suppressed.

The rebuilt historical NOC117 baseline in732 retained57HIGH/240total, original PostgreSQL1CRITICAL/26HIGH, alternate PostgreSQL1CRITICAL/25HIGH. These are vulnerable historical baselines, not final deployed runtime images or a reason to disable the gate. Worker/backend sharing is not double-counted.

Native runtime exposure remains documented in NOC118/NOC119: nonroot/capability-free/read-only runtime and bounded inputs reduce paths but do not establish non-exploitability. SQLite is still present through Python/native dependencies; FTS5 compile options must be checked. Expat and local helper packages remain vendor risks. New source preserves Python3.12, Node22, PostgreSQL16 derivative, Redis7 and all historical migrations.

## Targeted performance correction and acceptance

NOC119’s final durable runs729/730 totalled36 measured1,000-device rounds: min9.976s, median12.301s, max15.312s;3/36over15. Each six-round1,000-device invocation preserved6000samples and6000unique monitoring receipts. Run730 alone had18rounds9.976–11.320s; selecting only that favorable run would hide the failed criterion.

Verified code issue: monitoring_alerts.process already SELECTs and locks the per-device alert rows; scheduler’s collector condition immediately issued a second SELECT FOR UPDATE against the same device/transaction. It now reuses the locked snapshot only inside that transaction, on success and failure paths. It is discarded after each poll; no cross-poll/process cache exists. Regression tests require one alert-lock SELECT for healthy/weak/timeout polls and a fresh lock on the next poll. Existing collector recovery, stale/missing data, RF rules, deduplication, receipts/fencing and audit regressions remain.

The benchmark records bounded SQL statement-category counts without SQL text/parameters. No collection is skipped or batched into an unprotected commit. Spool file/directory fsync, PostgreSQL fsync/synchronous_commit/full_page_writes, row/advisory locks, unique receipts, timeouts, retries, concurrency8, pool10 and CPU/RAM limits are unchanged.

Repeat tests use internal networking and durable named volumes, not tmpfs DB. Documented limits: backend2CPU/768MiB, PostgreSQL2CPU/1024MiB,4CPU/~16GiB CI hosts,250/500/1000devices across40POPs and six rounds per size, three invocations. Preserve every elapsed round, image identity, sample/receipt count, health query, process CPU/RSS and actual PG settings. CPU/RSS for the polling Python process exclude PostgreSQL/other containers. Synthetic2ms transport is not live equipment throughput or an SLA.

Final measured outcomes are in the new PR ledger/artifacts. The15s gate remains FAIL unless every required new round meets it; no single favorable run or green informational CI job clears it. A sustained pilot-host soak and conservative>=120s initial one-device intervals still require operator acceptance.

## Trusted artifacts and installation handoff

[PUBLICATION.md](PUBLICATION.md) gives the repeatable operator-controlled process: clean exact source/tree, four actual registry digests, full source/image SPDX, development-inclusive dependency audits, full container reports, actual builder provenance, security/license review, Ed25519 manifest signing and independently supplied public fingerprint.

scripts/release/prepare_publication.py only assembles **unsigned** protected evidence, reusing the installer verifier. It refuses mutable/missing identities, source mismatch, incomplete evidence, omitted development audits, HIGH/CRITICAL findings and open blockers. It cannot sign, publish, generate keys or manufacture risk acceptance; today’s unresolved production blockers must continue to reject production preparation. Disposable-key signature/tamper tests remain.

[INSTALLATION-GUIDE.md](INSTALLATION-GUIDE.md) covers measured floors versus recommended headroom, Ubuntu/static IP/DNS/private time, VPN/MFA/SSH/firewall/Docker, trust and initial administrator, actual service/reboot checks, scanner deployment limitations, encrypted backup/off-site custody, supported upgrades and explicit failed-migration recovery. Root INSTALL/UPGRADE outdated two/three-image wording is corrected to the actual four-role contract.

[OPERATOR-CHECKLIST.md](OPERATOR-CHECKLIST.md) covers RouterOS6/7, Ubiquiti/Cambium/Racom/Aviat, Powercode/Grafana/WordPress and approved Designer imports. No exact real firmware is certified. RouterOS6 live REST/native collector and dedicated Wave live paths remain unsupported. Default-off/live read-only gates and independently approved per-endpoint credentials/limits remain mandatory.

## Final decision and finite remaining work

[REMAINING-BLOCKERS.json](REMAINING-BLOCKERS.json) separates verified history, software/performance, vendor security, legal/operator approvals, actual server setup and live-equipment acceptance. Final CI execution does not remove those external requirements.

**NO-GO for operational release/live pilot.** Code-level isolated acceptance and prepared procedures are distinct from operator authorization. Required next actions are named acceptance tasks: review supported native fixes/security dispositions; legal Redis/dependency decision; trusted publication/signing authority; approve/configure actual NTP/VPN/MFA/SSH/firewall/scanner; durable workload soak; independently encrypted offsite restore; and actual model/provider read-only compatibility. No indefinite new milestone series, production GO, equipment write, message/publication or cross-repository work is proposed or authorized.

## Supported Redis runtime remediation discovered during NOC120

Fresh diagnostic scans at74281e7 found11 lower-severity rows in the original Redis7.4.12 base: libcrypto3/libssl3 CVE2026-107496/95691/95692/88822/88823, and zlib CVE2026-4738. Scanner fixed-version fields identify supported Alpine3.5.9-r1 OpenSSL libraries and zlib1.4-r0. Public Docker Registry metadata was checked read-only: the7-alpine locator still resolves to the existing pinned index7f7d30b7… and Redis7.4.12; no newer tag was assumed patched.

deploy/Dockerfile.redis therefore preserves that exact immutable server/base and vendor entrypoint/UID999:GID1000/data format while installing only supported signed Alpine library fixes. Build and read-only runtime checks require the unchanged Redis7.4.12 binary, ownership and minimum library versions; unavailable fixes fail closed. No custom repository, major-server change or vulnerability suppression is used.

New-source host approvals must explicitly bind the Redis derivative, matching source revision, exact original base label, server contract and read-only package/ownership probe. Historical sources without this Dockerfile retain the original exact-lock rule; that rule is not silently generalized. All four controlled-test image roles are labelled/bound to source, and actual VM/upgrade/recovery tests exercise the derivative. Compose/local acceptance captures immutable IDs; a production release still needs the separately approved derivative registry digest, complete reports and signature. Licensing is unchanged.

The diagnostic11-row baseline is retained. Only final exact-image scans and completed installation/recovery/queue tests may establish remediation; pending builds are not PASS.

### Redis acceptance follow-up

Official Alpine3.23-stable OpenSSL APKBUILD confirms3.5.9-r1 and all five named patches; zlib APKBUILD/security tracker confirms1.4-r0. Individual OpenSSL tracker pages were unavailable, not treated as clean evidence. tests/installer/redis_upgrade.py performs an actual no-network, nonroot/capability-free old-to-derivative test on a fresh volume, preserves Redis server/client binary hashes, verifies durable AOF/RDB sentinel and queue order, and restarts the derivative. Failed fixture state is retained. This supplements, not replaces, full VM/upgrade/recovery tests.

Diagnostic735/736 backend failure was a historical-source test mock returning JSON for every git subcommand, falsely reporting that a new Redis Dockerfile existed. The mock now explicitly represents absent historical files for ls-tree; original PostgreSQL digest assertions remain. New tests independently reject missing/wrong-source/wrong-base/wrong-version Redis approval and runtime probe failure. No gate or assertion is disabled to accommodate the derivative.

## Full durable-outbox performance correction

Review found that the prior pilot benchmark put PostgreSQL data on a named volume but created monitoring outbox temporary directories under the backend’s /tmp tmpfs. Historical timings remain valid for that exact database-durable profile, **not full durable-outbox I/O acceptance**. This is a test-profile defect, not evidence of lost production telemetry; real installed monitoring storage was already persistent.

NOC120 now mounts a second private local named volume for the benchmark outbox, validates local/no-custom-options volume configuration, and requires the explicit private-volume marker in every result. Fresh-volume ownership is initialized with only CHOWN using root-before-chmod-before-final-owner ordering. Runtime remains nonroot/read-only/capability-free; /tmp stays restricted for unrelated temporary files. File/directory fsync, PostgreSQL durability, locks, receipts, every sample, eight-thread concurrency and resource limits are unchanged. Only newly executed full-profile rounds may satisfy the15s criterion; do not compare a favorable RAM-outbox time as equivalent. Final exact-source results are in the PR ledger.

## Executed code acceptance snapshot — 24 November 2026

[ACCEPTANCE-EVIDENCE.json](ACCEPTANCE-EVIDENCE.json) records code commit `c9b8f42daee75eff33a56cfcee21319bd2214aad`, tree `eb06a58c7ef4230a4a12db809850aa03170d9b3d`. Push [739](https://github.com/treichhart1/nwo-noc/actions/runs/45509408897) and PR [740](https://github.com/treichhart1/nwo-noc/actions/runs/45509419639) each completed all ten jobs successfully. PR checkout `a39c40253fca876b77b69e90beb514c15b7cd6d7` is GitHub's synthetic merge with the same tree, not an actual merged PR. Use exact-source push artifacts for publication; do not relabel PR artifacts. This evidence-only descendant must pass its own ten jobs; the terminal PR ledger records that final SHA and results without pretending this snapshot tested a future commit.

Per run: backend1051 passed/961 skipped; full RF/PostgreSQL1106 passed; installer/security/scanner152 passed; Grafana43, customer-status64, integration54 and audit36 passed. These collections overlap and are not a unique grand total. Playwright recorded69 successful executions across27 invocations. Native host78 checks, PostgreSQL readiness5 and legacy upgrade/recovery11 checks passed. Node22 type checking/builds, development-inclusive npm audit (zero), pip audit (no known vulnerabilities), fresh/idempotent/schema-compatible migrations and supported0011-to0028 upgrade passed.

The supported Redis derivative retained Redis7.4.12 and unchanged server/client binary hashes. Original exact-base diagnostic scan:11 lower-severity findings; derivative:0. Backend:0 HIGH/CRITICAL,34 lower-severity findings. Frontend/PostgreSQL/Redis derivatives:0 findings. Each independent VM run scanned nine exact installed image identities with0 HIGH/CRITICAL. Complete historical vulnerable baselines remain preserved. Zero scanner HIGH/CRITICAL does not patch every vendor-unfixed issue.

Both independent Ubuntu24.04/KVM runs installed and verified the application, performed guarded upgrade/encrypted backup, powered off the source, verified signed synthetic recovery transport, recovered database/configuration/documents/keys/authentication/monitoring history, rejected corrupted/incomplete transport, and rebooted both source and recovered VM. Source reboot times55.487s/55.178s; receiver55.326s/56.054s. VM resources:4vCPU,5120MiB,32GiB. These are two VMs on one hypervisor, not independent-site disaster recovery. Actual NTP synchronization was **no**; real VPN/MFA and current production malware definitions remain operator acceptance.

**A111-12 remains FAIL:** full durable PostgreSQL **and outbox** ext4 volumes produced36 one-thousand-device rounds, min10.066s, median12.6035s, max15.580s;6 exceeded15s. Every one of six invocations preserved6000 samples and6000 unique receipts. Backend2CPU/768MiB, PostgreSQL2CPU/1024MiB,8 polling threads, pool10; PostgreSQL fsync/full-page-writes/synchronous-commit and file/directory fsync retained. Push739 had0/18 misses; PR740 had6/18. Neither cherry-picking the faster host nor RAM-outbox diagnostics clears this blocker.

**NO-GO for operational release/live pilot.** The finite remaining tasks are the measured durable workload acceptance, vendor security disposition, license approval, trusted registry/signing custody, actual secure server setup/scanner/NTP/VPN/MFA, independently offsite encrypted restore, and separately approved real firmware/provider read-only compatibility. The installer candidate and operator handoff are ready for review, not deployment authorization.
