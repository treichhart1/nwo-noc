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
