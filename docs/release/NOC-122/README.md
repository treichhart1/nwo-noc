# NOC-122 — controlled lab readiness

**Candidate for a separately authorized, isolated lab. NO-GO for production or a live-equipment pilot.** No installation on an NWO server, equipment access, messages, public publishing or production signing is authorized by this report.

## Provenance and scope

Direct base: draft [PR125](https://github.com/treichhart1/nwo-noc/pull/125), exact commit `4d1d0bf018035d3f52c242de9d5f7a3ebe2db25a`, tree `a7a4fddba61810eff12c843d72f8afccae311834`. Verified [run749](https://github.com/treichhart1/nwo-noc/actions/runs/45535532989): all ten jobs SUCCESS. Branch `feature/noc-122-lab-readiness` preserves that complete ancestry; no existing PR is merged. The original clean checkout at715a74956dd633e86315dd5d323fefed8e32b3cc is preserved on the full cloud filesystem. An isolated exact-source RAM checkout is used only for source/unit tests, never durable performance acceptance. No applicable AGENTS.md was found in the source/accessible ancestor instructions.

[NOC110 integration manifest](../../audits/NOC-110-integration-manifest.json) and [dependency graph](../../audits/NOC-110-dependency-graph.mmd) remain historical provenance. Recent direct stack, oldest first: PR114 integration →115 audit →116 hardening →117 installer →118 validation →119 acceptance →120 blockers →121 container security →122 backend security →123 independent VM acceptance →124 handoff →125 performance →this PR. The PR numbers122/123 are **not** milestone numbers122/123. Full earlier PR ancestry/reviewed replay is retained by the integration audit; none of these are assumed merged into main.

Reviewed root install.sh/upgrade.sh/verify.sh, scripts/host manager/policy/artifact/trust/evidence/ingress/admin code, release signing/publication/recovery tools, NOC120 operator guide/checklist and NOC121 report/blockers/final PR ledger. No duplicate installer, inventory, collector scheduler or recovery implementation is introduced. No migrations, API behavior, lockfiles, resource limits or benchmark rules changed.

## Deliverables

- [Lab installation](LAB-INSTALLATION.md): separate explicit authorization, synthetic data, existing controlled-test purpose; production signature gates remain mandatory for release-purpose installation.
- [Server preflight](SERVER-PREFLIGHT.md): implemented stdlib read-only utility, bounded local commands, no default DNS/network probes, no changes; exact limits/status meanings.
- [Equipment compatibility](EQUIPMENT-COMPATIBILITY.md): exact native/REST contracts, units/unsupported fields, official sources, Wave archive blocker and per-device acceptance evidence.
- [Pilot checklist](PILOT-CHECKLIST.md): ordered administrator setup, host controls, Powercode preparation and phased stop/recovery procedure.
- [Machine-readable requirements](OPERATOR-REQUIREMENTS.json): finite owners, inputs, status and acceptance evidence.

## Verified baseline performance and sizing

Use the final PR125 ledger to reconcile the earlier committed NOC121 snapshot. Exact4d1d0bf push748 and PR749 measured **36** full-durable1000-device rounds across40POPs: min8.934s, median11.272s, max14.172s, **zero over15s**. Six invocations each preserved6000samples and6000distinct receipts. Every1000-device round retained3000file/directory fsync calls,1000savepoint/release pairs,1033commits. Backend2CPU/768MiB, PostgreSQL2CPU/1GiB,8threads,pool10, both data/outbox on ext4 persistent volumes, all PostgreSQL durability controls on. The older six misses and first failed optimization remain historical evidence. Synthetic2ms replies are not real-radio capacity certification. This milestone keeps --require-target and repeats the mandatory benchmark in CI without changing the workload.

|Configuration|Recommendation and limits|
|---|---|
|Minimum controlled lab|4modern dedicated amd64/x86-64-v2 cores or vCPUs,8GiB RAM,100GiB persistent SSD; confirm every vCPU ABI, flush/barrier semantics and power-loss protection|
|Preferred operational planning|8modern dedicated cores/vCPUs,16GiB RAM,>=250GiB enterprise SSD/NVMe, independent encrypted backup storage; not a certified production capacity|
|Actually executed independent VM baseline|4vCPU,5120MiB,32GiB, Ubuntu24.04/KVM; install/reboot/recovery acceptance, not a retention/sizing benchmark|
|Durable monitoring measurement|4CPU/~16GiB CI host with the smaller unchanged application/database limits above; hardware SSD identity/PLP unverified|

Budget PostgreSQL tables/indexes/WAL/retained receipts and outbox; images plus staged releases; documents; bounded raster tiles/LiDAR jobs; optional private Grafana retention; ClamAV engine/current definitions; workers; and at least two encrypted backup generations. Keep substantial free space and independent backups. The1000-device six-poll fixture's11–12MB is not measured production growth. At300s polling,1000devices mathematically produce288000samples/day; at60s,1.44million, before additional collectors. Measure actual payload/index/WAL/backup growth and agreed retention on the lab hardware. Do not extrapolate90-day storage from a fixture or store persistent data on tmpfs.

## Current security review

Primary vendor pages rechecked for this assignment: [SQLite119373](https://ubuntu.com/security/CVE-2026-119373) remains vulnerable for **sqlite3** in Noble; [ACL54369](https://ubuntu.com/security/CVE-2026-54369) remains ignored because backport is intrusive; [util-linux76642](https://ubuntu.com/security/CVE-2026-76642) remains vulnerable. Preserve all other unresolved [NOC120 vendor rows](../NOC-120/VENDOR-REVIEW.json) unless independently updated; not every historical CVE was reclassified here.

**New supported fix:** Canonical updated [Expat113351](https://ubuntu.com/security/CVE-2026-113351) and [Expat117610](https://ubuntu.com/security/CVE-2026-117610) on25November: Noble fixed in `2.6.1-2ubuntu0.7`. Existing signed apt refresh/upgrade obtains supported fixes; the runtime Dockerfile now explicitly fails if libexpat1 is below that floor. No ABI replacement, unsigned package, advisory suppression or major upgrade. Final exact image inventories/scans must confirm this; the upstream fix does not settle other Expat/native findings. A zero HIGH/CRITICAL scan is not proof every historical native issue is fixed.

Redis7.4.12 RSALv2/SSPLv1 and dependency licensing, production registry/signing/trusted public-key custody, vendor-native risk review, actual host VPN/MFA/NTP/SSH/firewall/scanner integration and physical offsite recovery remain independent blockers. No production key is created. All live integrations stay disabled.

## Execution and final reconciliation

Initial local preflight regressions:26passed. The utility is also executed against this cloud host; expected full-disk/container/readiness failures are evidence, not a successful Ubuntu installation. Authoritative final container/VM/regression/migration/performance results, exact commit/image IDs, job URLs, counts and raw artifact metadata are reconciled in the new draft PR's final ledger after CI. A source report cannot certify its own future workflow. All ten required jobs must pass; a performance miss remains a failure.

Operational release and live pilot remain **NO-GO**. The concrete next action is the named operator's review of the finite requirements and separate isolated-lab authorization, followed only later by per-device approval. No general new development milestone is needed unless that acceptance demonstrates a specific software defect.

### Initial CI review correction

First candidate2234ce70, push751/PR752: dependency-security correctly rejected three new LOW Bandit source statements (subprocess import/use and an internal status assert); the downstream VM/aggregate dependencies did not pass. Dependency vulnerability audit itself found no known issues. Replace the internal assert with explicit validation and review only the two exact LOW subprocess fingerprints, with fixed callsite/argv, no shell, sanitized output and timeout/size evidence. HIGH/new/changed/stale results still fail. This is source-bound static triage, not a CVE ignore or gate relaxation. Four additional disk/installation detection regressions bring new preflight coverage to30cases; final execution is recorded in the PR ledger.
