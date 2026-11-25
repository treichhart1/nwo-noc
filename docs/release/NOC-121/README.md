# NOC-121 — final monitoring performance and collector readiness

**NO-GO for operational release/live pilot.** This candidate addresses A111-12 and documented collection gaps. No deployment, real device/Powercode connection, message, publication or signing authority is authorized.

## Source and repository integrity

Base draft PR124, exact source e52cc1354a0dd3cf77905dc5179da17704ef3af2, verified run742 (45512869000) completed SUCCESS with all10jobs. Branch feature/noc-121-final-performance is directly stacked on that source. The NOC110 integration manifest and inherited dependency chain remain intact. Existing PRs remain draft/unmerged; historical migrations are unchanged. No applicable AGENTS.md was present in the accessible checkout or base source tree.

The old cloud checkout is clean at715a74956dd633e86315dd5d323fefed8e32b3cc but its overlay has zero available writable bytes. It is untouched. Exact base source and same-repository Git history were recovered into a newly created private RAM source/unit-test directory. **No local RAM-backed benchmark or installation result is claimed.** Actual durable benchmarks and all required container/VM/security/regression jobs run on disposable GitHub runners. Commits use expected-parent, non-forced GitHub updates. No cache, image, volume, work or migration was deleted to free disk.

This report records the reviewed implementation and explicit pending evidence. Exact executed commit/tree, run IDs, all ten terminal job conclusions, complete after-measurements and raw artifact identities are finalized in the new draft PR ledger after execution. A source document cannot certify a future CI run.

## A111-12 baseline — all evidence retained

[BASELINE-PERFORMANCE.json](BASELINE-PERFORMANCE.json) preserves every250/500/1000-device round and resource/query count from739–742, including source/PR synthetic-merge contexts.

| Full durable profile |1000-device rounds|Min/median/max seconds|Over15s|
|---|---:|---|---:|
|C9 code snapshot,739/740 (user-referenced report)|36|10.066 /12.6035 /15.580|6|
|Final NOC120 source,741/742|36|10.068 /12.3335 /15.985|6|
|Same functional code combined|72|10.066 /12.5205 /15.985|12|

Both PostgreSQL and the monitoring outbox used separate ext4 named volumes. Backend2CPU/768MiB; PostgreSQL2CPU/1024MiB;8polling threads; pool10;4CPU/~16GB CI hosts. Every six-round1000-device invocation retained6000 samples AND6000 unique receipts. Do not mix earlier PostgreSQL-durable/RAM-outbox diagnostics with this profile.

## Targeted corrections and profiling

1. Retain the inventory Device object strongly for the duration of one poll. SQLAlchemy's identity map uses weak references; releasing the collector's last reference caused a second SELECT during staging. The same device is still validated by the collector; no global/cross-poll inventory cache is introduced.
2. Read the MonitoringAttempt before changing job success fields. Autoflush-enabled callers previously emitted a partial job UPDATE before the attempt SELECT, then another job UPDATE during atomic lease advancement. The final attempt, receipt, sample and job advancement still share the original transaction.
3. After first-iteration profiling, combine the job, matching attempt and exact-slot receipt into one indexed outer-joined snapshot. FOR UPDATE OF the job alone retains the original skip-locked job lock; nullable attempt/receipt joins do not acquire broader locks. Token/state/expiry/device/global advisory checks and unique receipt constraints still apply. This removes two more read round trips without dropping any evidence or moving commit/acknowledgement boundaries.
4. Add bounded benchmark phase timings for actual scheduler claim/execute, collection, sample staging, alert history/vector work, SQL driver/lock time, transaction commit, outbox read/write/acknowledgement/reconcile, and every real fsync. Timings are inclusive and overlap across8threads; they must not be summed into end-to-end latency. No SQL text, parameters, paths, endpoints or credentials are recorded.

The benchmark retains its original autoflush setting, device mix, size/round count, virtual schedule,2ms synthetic transport,8threads, existing CPU/memory limits, pool10, live alert/recovery assertions and original unique receipt/sample count assertions. PostgreSQL fsync/full_page_writes/synchronous_commit, every file/directory fsync, skip-locked claims, per-device/advisory locks, savepoints, fencing, replay and database commits remain enforced. Outbox code is unchanged.

New cold-identity-map/autoflush and existing-slot replay PostgreSQL regressions require one inventory SELECT, one attempt SELECT and one final job UPDATE while preserving telemetry, receipt, lease advancement and the unacknowledged outbox until caller commit. Existing crash/replay/deduplication/stale/missing/consecutive/RF recovery tests remain enabled.

The final CI host benchmark uses --require-target. It recomputes the complete nine-record/three-invocation/three-size/six-round profile, checks the existing CPU/RAM/thread/pool limits, both persistent filesystems, PostgreSQL durability, every expected sample/receipt and at least three real fsync calls per successful sample. Missing evidence or a single1000-device round over15s fails the job after retaining its artifact. Twenty-three boundary/negative cases passed locally; no historical failure is suppressed.

### First executed optimization — still FAIL

[FIRST-ITERATION-PERFORMANCE.json](FIRST-ITERATION-PERFORMANCE.json) preserves all full-durable measurements and inclusive profiles for833065f6d3c156b3779ed3d6275a83d950ee7402, push744/PR745. Push744:18one-thousand-device rounds,10.060/13.184/16.808s min/median/max,4over15s. PR745:18rounds,9.377/10.1685/10.816s,0over. Combined36rounds median10.7715s,4over15; no favorable run is selected as acceptance.

Every1000-device invocation preserved6000 samples/unique receipts, ext4 for both volumes and3000 real fsync calls per round. Normal-round SELECTs fell11834→10834 and UPDATEs3032→2032; alert creation and savepoint/release counts stayed intact. Slow rounds show fsync maxima259–401ms and commit maxima214–394ms with lower process CPU utilization, proving durable I/O stalls materially contribute. Inclusive phase totals overlap across threads and are not added into end-to-end time.

Initial runs744/745 completed all10CI jobs, but their benchmark was still informational and the performance result remains FAIL. The new --require-target gate closes that distinction for the next candidate; green informational CI is not a waiver.

The joined snapshot is a further targeted reduction in remaining database round trips; its strict full-durable result is now PASS in746/747, with all ten jobs successful in both executions. If storage stalls still violate15s, the gate must fail and the blocker remains. No durability waiver or assertion weakening is permitted.

**Executed joined-snapshot performance: PASS for the documented synthetic profile.** Source668a9b2c9ddb90483971414a0ea8b8586a0a04e8 passed strict runs746/747:36rounds,0over15s, min8.879/median9.8535/max10.672s. See [AFTER-PERFORMANCE.json](AFTER-PERFORMANCE.json) and [EXECUTED-EVIDENCE.json](EXECUTED-EVIDENCE.json). A111-12 is cleared only for this exact measured scope; actual hardware/live capacity and final delivery CI remain required. Acceptance requires every measured1000-device round within15s across independent executions, both volume filesystems verified non-RAM, all expected samples/unique receipts present, unchanged resource limits and passing correctness/security checks. No favorable-run selection, target relaxation or RAM substitution.

## Collector readiness

| Equipment/path | Implemented collection | Evidence and remaining requirement |
|---|---|---|
|RouterOS7 REST | Existing fixed GET allowlist/TLS/endpoint/time/retry policies unchanged | Synthetic contracts; exact firmware/model still needs authorized acceptance |
|RouterOS6.43+ /7 native API-SSL | New fixed print-only client, scheduler-only activation | Synthetic byte fixtures plus actual trusted/untrusted TLS over local AF_UNIX socketpairs; no live firmware certification |
|RouterOS before6.43 | Unsupported | Legacy MD5 challenge login deliberately rejected; do not deploy old firmware merely to meet protocol minimum |
|Ubiquiti airMAX AC/legacy | Existing explicitly mapped SNMPv3 authPriv GET and UISP RF contracts | Actual model/firmware SNMPv3 support and precise vendor MIB/OIDs/units remain unverified |
|Ubiquiti Wave Pro/LR/AP | UISP inventory foundation; no newly enabled live RF collector | Vendor confirms SNMP and changed AF60 MIB revisions, but secure transport/per-firmware field contract not established; BLOCKED |
|Cambium ePMP4600/legacy | Existing explicitly mapped SNMPv3 authPriv GET | Exact4600c/4625 firmware/MIB permissions/scales/optional fields need authorized verification |
|Racom Ray3 | Existing explicit microwave SNMPv3 GET, carrier/link/history/diagnostics | Vendor MIB digest, exact instance indexes, units, carrier semantics and security capability need verification |
|Aviat | Existing mapped multi-carrier microwave SNMPv3 GET | Exact product/firmware MIB, ACM capacity/carriers and TLS/authPriv support need verification |

### RouterOS native contract

Primary protocol sources: [frozen RouterOS API reference](https://help.mikrotik.com/docs/spaces/ROS/pages/47579160/API), [current API manual](https://manual.mikrotik.com/docs/developer-guides/api/). These establish sentence framing, post6.43 login and certificate-based API-SSL. Protocol minimum is **not** a security-approved firmware recommendation.

The new collector connects only to an explicitly allowlisted literal IPv4 address on8729; no DNS discovery/scanning, redirects, plaintext8728, anonymous TLS, certificate bypass, legacy login or arbitrary commands. Standard CA and IP-SAN verification and TLS1.2 minimum are retained. The wire boundary rejects all commands except fixed property-allowlisted resource/identity/interface/optional-health/optional-OSPF print sentences and exact login. Reply words/bytes/rows and a30s overall deadline/3s socket timeout are bounded. Retry/backoff remains the durable scheduler's responsibility; no hidden reconnect loop.

Read-only account policy should be limited to **read,api** where supported; exclude write/reboot/policy/sensitive/test/SSH/Winbox/REST rights not required by this adapter. Actual permissions remain an operator validation task; the NOC never creates or changes router accounts.

Use [mikrotik-native.example.json](../../../deploy/pilot/mikrotik-native.example.json) as an inert template (documentation address, enabled=false/admin_approved=false). After separate authorization, mount private0400/0600 config/credential files owned by the service UID and the approved CA. Set MIKROTIK_NATIVE_CONFIG_FILE plus existing MIKROTIK_ENABLED, MIKROTIK_DEVICE_ID, INTEGRATIONS_ADMIN_APPROVED and RF_SCHEDULER_ENABLED; OFFLINE_MODE must be false. Native and REST credentials/origins are mutually exclusive. A setting alone does not deploy a secret/CA mount or authorize egress: use a reviewed release configuration and read-only mounts; never edit generated signed artifacts to bypass their verifier. Config device/address must match authorized inventory, and polling must respect min_interval_seconds>=60. Initial300s interval is recommended pending measured device impact. The legacy manual REST entry point rejects native configurations, preventing a second polling path.

Reads identity/version to validate the protocol and normalizes supported CPU/memory/uptime, interface state/counters/errors/drops and optional OSPF state into existing monitoring telemetry; no duplicate inventory or new database model. The scheduler's pre-existing restricted DTO does not persist identity/version or update inventory firmware: record approved firmware separately during operator acceptance. This is a monitoring path, not a native inventory synchronizer. Unsupported metrics stay null. Native temperature is disabled unless its exact hardware/API Celsius or decicelsius unit is explicitly configured; ambiguous/multiple sensors stay unavailable. [MikroTik health documentation](https://help.mikrotik.com/docs/spaces/ROS/pages/25690117/Health) warns that health polling can be costly and API units vary. Counters retain exact integers and existing reset/delta rules. Raw router error text and credentials never enter exceptions/audit evidence. Native RouterOS6 IPAM/DHCP/ARP/route discovery is not included: existing NOC99 observation collection remains its separately allowlisted RouterOS7 REST path.

### Wave evidence and concrete blocker

[Wave Pro vendor specifications](https://techspecs.ui.com/uisp/60ghz-wireless/wave-pro) list SNMP/UISP, but do not establish SNMPv3 authPriv compatibility. [Official4.1.0 notes](https://community.ui.com/releases/Wave-PTMP-PTP-4-1-0/8f6eb0b1-eed8-401a-b9af-595065f97502) describe new station fields and revised MIBs. [UI-Team's MIB revision explanation](https://community.ui.com/releases/aef449c9-51e8-4373-bac8-06e2eab4b23e) documents different AF60 subtrees and shifted indexes. No indexes from community responses were adopted as validated OIDs.

Bounded read attempts for official ui-mib.zip archives at4.1.0/3.4.0 were refused by the environment proxy403; no archive checksum/contents or firmware compatibility is claimed. Obtain the exact vendor archive through an approved channel, verify its provenance/revision/units/instance suffixes, and establish an approved secure transport/UISP schema for the intended Wave firmware. Then supply synthetic captured fixtures before enabling anything. A community-string or unverified HTTPS endpoint fallback would weaken existing controls and was not implemented.

## Server sizing and workload envelope

Measured NOC119/120 independent Ubuntu acceptance used4vCPU/5120MiB/32GiB, not a production retention/1000-live-device capacity certification. Durable synthetic polling used the explicit2CPU/768MiB application and2CPU/1GiB database limits above; do not silently increase them to pass15s. CPU model must satisfy the existing x86-64-v2 native-wheel contract.

Recommended **engineering minimum for controlled testing**:4modern dedicated vCPU,8GiB RAM,100GiB persistent SSD with power-loss protection and working flush/barrier semantics. **Preferred planning configuration**:8dedicated modern vCPU,16GiB RAM,250GiB enterprise SSD/NVMe, with separate database/outbox/backup capacity and substantial free space. Extra CPU/RAM/storage is operational headroom for API/frontend/scanner/Grafana/imports/backups; those preferred specifications have **not** been benchmark-certified. Benchmark the approved exact images on selected hardware before accepting capacity.

Keep PostgreSQL and outbox on durable local storage, PostgreSQL WAL/fsync/full_page_writes/synchronous_commit enabled, volume/file ownership intact and I/O monitored. Reserve at least two verified backup generations plus staging/image space. The six-poll1000-device fixture's monitored tables occupied roughly11–12MB including fixture jobs/attempts/alerts/receipts/indexes; that is not a measured production bytes-per-day model. At1000devices/60s, arithmetic is1.44million samples/day before optional collectors. Set retention/budget from measured vendor payload/index/WAL growth, run rollups/retention in the supported workflow and alert before capacity exhaustion. Do not promise90day retention from this fixture.

Pilot starts with only separately approved representative devices, default300s polling and>=60s adapter floor, bounded8thread maximum and no added duplicate collector. Increase scope only after measured network/TLS/MIB response times, database/outbox backlog and alert/recovery latency satisfy the chosen interval. Synthetic2ms responses are not live-device capacity; worst-case native timeout behavior must be included in queue/lease sizing. No1000-live-device SLA is established.

## Security review and remaining approvals

Rechecked Canonical's primary Noble dispositions for [SQLite119373](https://ubuntu.com/security/CVE-2026-119373), [Expat113351](https://ubuntu.com/security/CVE-2026-113351), [Expat117610](https://ubuntu.com/security/CVE-2026-117610), [ACL54369](https://ubuntu.com/security/CVE-2026-54369) and [util-linux76642](https://ubuntu.com/security/CVE-2026-76642): vulnerable/deferred/evaluation or intrusive-backport disposition persists; no supported Noble fix was established. This is the sqlite3 source package, not the obsolete sqlite package. Unrelated source exclusions, upstream major versions or scanner0HIGH/CRITICAL do not patch installed code.

Existing signed-repository updates, pinned images/locks, Redis7.4.12 supported library remediation, full scans/SBOM/dependency audits/provenance and fail-closed signature/digest/source verification remain unchanged. Fresh scans are required in final CI; NOC120's34 lower backend findings/0HIGH/CRITICAL and zero findings for the three other candidate roles are historical counts until rescanned. No ignore or unsupported native library substitution was added.

[REMAINING-BLOCKERS.json](REMAINING-BLOCKERS.json) retains performance until measured, vendor-native issues, Redis/dependency legal review, production registry/signing/key custody, actual NTP/VPN/MFA/SSH/firewall/current-scanner setup, independent offsite recovery and live equipment/provider acceptance. These require vendor/operator evidence and cannot be completed through synthetic Codex tests alone.

## Tests and release decision

Initial local source/unit collection after correcting environment/provenance setup: **1092passed/964skipped/3warnings**; with strict-gate cases1115passed/964skipped, and the joined-snapshot PostgreSQL-only case remains for CI. New native cases:41passed, including real TLS on local socketpairs and no network contact. New PostgreSQL query-count/native-scheduler cases are not marked passed locally. Earlier archive-only/global-offline test failures were setup limitations, retained locally, not hidden by disabling assertions. Pinned top-level requirements were used locally; final CI uses authoritative hash-locked test dependencies.

## Executed strict acceptance and final delivery

Code snapshot `668a9b2c9ddb90483971414a0ea8b8586a0a04e8`, tree `daeb70d2f212df655ffbc176d7d2095bc72d1d39`: [push746](https://github.com/treichhart1/nwo-noc/actions/runs/45532362479) and [PR747](https://github.com/treichhart1/nwo-noc/actions/runs/45532368211) each completed **all10jobs SUCCESS**. PR checkout340f8cd6f7a388904449adab4600acecf1aa9ad1 has the identical tree, but different source labels; it is a temporary CI merge ref, not a merged PR. Production artifacts must use exact-source push evidence and separately approved immutable registry digests.

Per run: backend1115passed/965skipped; RF/PostgreSQL1151passed (including42native tests:41pure and1PostgreSQL); installer/security/actual scanner152; Grafana43; communications64; integration54; audit36. These overlap and are not a unique grand total. Playwright69successful executions across27invocations; host78checks; PostgreSQL readiness5; legacy upgrade/failure/matching restore11. Hash-locked dependency/security/type/build/migration checks passed; npm including development0 and pip no known vulnerabilities.

All36full-durable1000-device rounds passed15s, every invocation retained6000samples AND6000unique receipts, and every round3000fsync/1000savepoints/1000releases/1033commits. SELECTs8834 versus original11834, normal UPDATEs2032 versus3032. Raw before/first-failed/after files preserve every250/500/1000 round and concurrent inclusive phase. Runner I/O variance affects elapsed comparisons; deterministic query-count reductions, not all elapsed improvement, are directly attributed to code.

Measured final-code-snapshot health queries28.58–31.21ms. Polling process RSS127968–132404KiB, peak138988KiB; one-core-normalized CPU114.58–116.06%, excluding PostgreSQL and other containers. Both volumes ext4. These measurements support the testing specifications above; the preferred server is a planning recommendation, not a certified live workload.

Fresh exact native image scans: backend0HIGH/0CRITICAL with34lower findings; frontend/PostgreSQL/Redis0findings. Each independent VM scanned9exact installed image identities with0HIGH/CRITICAL. Backend/worker sharing is not double counted. Full raw Trivy/SBOM/provenance/artifacts and IDs are referenced in EXECUTED-EVIDENCE.json; GitHub archive hashes there are provider-reported, not locally rehashed. Historical730 archives were independently downloaded/hashed/read in dependency CI.

Both pinned Ubuntu24.04/KVM runs installed/verified/upgraded, created encrypted backups, restored database/configuration/documents/keys/authentication/monitoring history with source powered off, rejected damaged/wrong/incomplete recovery inputs, and rebooted source and receiver. Source reboot59.211s/56.475s; receiver57.482s/57.307s. These are regression evidence from the unchanged harness, not new installation development. Same-hypervisor recovery is not offsite disaster acceptance; actual NTPSynchronized=no and real VPN/MFA/current scanner deployment remain blocked.

Final collector review found that known sanitized authentication/permission/unsupported/response-limit errors were collapsed to generic collector_failed. The final delivery correction preserves only five explicitly allowlisted static codes; six PostgreSQL cases also prove arbitrary text remains generic, no sample/receipt is invented, and repeated errors create only a monitoring failure, never an RF outage. Normal successful polling and its measured profile are unchanged. Local final collection1115passed/971skipped; these six new PostgreSQL cases must execute in the final exact-source CI.

**The final delivery commit's SHA, all ten job conclusions and all additional strict benchmark rounds are recorded in draft [PR125](https://github.com/treichhart1/nwo-noc/pull/125).** This committed snapshot cannot certify its own future workflow; final CI must pass and does not waive a miss. The PR terminal ledger is the reconciliation point for that descendant, including exact images/artifacts and tests. No production GO or live pilot authorization. Required next actions are concrete acceptance tasks above, not an indefinite new-feature milestone.
