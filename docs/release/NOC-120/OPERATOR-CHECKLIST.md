# Operator acceptance checklist — no live activation authorized

Record owner, date, exact source/image/firmware, command, exit status, artifact hash, observations and approval reference for each applicable item. Use PASS/FAIL/BLOCKED/NOT TESTED; a planned test is not PASS. Resolve material risks before any live pilot GO.

## Release authority and legal

- [ ] Review NOC119 final source60a0a34260f70fac8c7b5e9e00c263d38d1f28d8, run730 ten jobs and retained historical failures; reconcile NOC120 final PR ledger.
- [ ] Review every VENDOR-REVIEW.json row and full current native/dependency scans; do not equate vendor severity/source exclusions with patches.
- [ ] Confirm supported fixed package versions against actual installed inventories. Unfixed/ignored/evaluation findings require explicit independent security decision, not blanket suppression.
- [ ] Resolve Redis7.4.12 RSALv2/SSPLv1 and custom/unknown third-party license obligations with legal/operator authority. Redis8 terms do not retroactively apply.
- [ ] Authorize exact source/tree and supported upgrades, trusted builder/registry publication, immutable four-role digests, complete SBOM and provenance.
- [ ] Approve independent Ed25519 key custody/public fingerprint, revocation/rotation/withdrawal and anti-downgrade policy. No signing private key on NOC host.
- [ ] Validate complete production evidence through PUBLICATION.md; unresolved open blockers must keep approval preparation failing.

## Actual server and data protection

- [ ] Record approved Ubuntu24.04 image and x86-64-v2 flags on every vCPU, disk/RAM/CPU and actual workload measurements.
- [ ] Configure approved static IP/DNS/private NTP with console recovery; verify synchronized UTC and clock offset after reboot/soak.
- [ ] Verify actual VPN/IdP MFA, named accounts, key-only SSH, emergency access and independent authorization review.
- [ ] Independently test default-deny host and Docker/IPv6 forwarding, loopback/private TLS bindings, no DB/Redis/public administrative exposure.
- [ ] Validate supported package/Docker updates and restricted bootstrap/maintenance egress. Preserve existing compatible services/data.
- [ ] Run actual install.sh and verify.sh, protected administrator setup, service restart and actual kernel reboot on the approved acceptance host.
- [ ] Verify secret file permissions, safe logs, least-privilege database and unique workers; keep live adapters/public publishing/delivery disabled.
- [ ] Deploy real ClamAV/current trusted definitions and reviewed least-privilege socket mount; repeat safe EICAR/quarantine/timeout/restart/approval tests. The core package does not install a scanner.
- [ ] Approve backup downtime, retention/capacity alerts, RPO/RTO, encrypted off-site destination and independently held recovery keys/trust fingerprints.
- [ ] Restore actual synthetic acceptance state to an empty host in a physical/site-independent failure domain; verify DB/schema/counts/authentication/documents/keys/Redis/history/reboot.
- [ ] Exercise supported upgrade, backup-before-migration and failed-upgrade recovery without removing the recovery guard or overwriting newer data.
- [ ] Sign off sustained polling/query/CPU/RAM/disk capacity. A15s target needs every required repeated round at or below15s, preserved samples/receipts and production-like durable storage. No cherry-picking.

## Common read-only pilot gate

**All live integrations remain disabled.** Only a separate authorization may approve one named device/account at a time. No scanning, SNMP walk/SET, config writes, reboot, routing/RF changes, remediation, dispatch, purchases, messages or publication.

For each row: record actual model, firmware/API/MIB version/hash, supported collector path, exact allowlisted endpoint/protocol/port, TLS CA/hostname or SNMPv3 authPriv policy, credential broker and nonsecret identity. Confirm device-side minimum privileges deny changes independently (do not issue a write against live equipment just to test denial without separate permission). Redact credentials/customer identifiers from evidence.

Start with concurrency1 and proposed interval>=120s (respect stricter adapter minimums), deadline/lease above the full validated request budget and bounded retries/backoff. Existing generic HTTP contract uses3s connection/pool/write,5s read,30s total, max2 retries; MikroTik scheduler contract needs at least40s. These are configured contract limits, not live throughput guarantees. Review radio/microwave per-device policy; do not assume every vendor shares HTTP behavior.

For each approved device test normal, missing/unsupported, malformed, permission-denied, timeout/disconnect, stale/future sample, duplicate, counter reset/reboot observation and stable multi-sample recovery. Missing telemetry stays unknown. Review device and NOC audit/connection logs, last-success timestamps and absence of duplicates before expanding.

| Integration | Implemented/unsupported path | Required acceptance evidence |
|---|---|---|
| MikroTik RouterOS7 | Allowlisted HTTPS GET REST identity/resource/interface/health/OSPF; documented IPAM observations | Exact ROS7 model/build, certificate/origin, read-only account, optional404 handling, counters/uptime/OSPF and no config writes. Exact firmware remains unverified |
| MikroTik RouterOS6 | **REST unsupported and rejected; native live binary API/SNMP collector not implemented** | Fixture/export normalization does not establish live support. Do not pilot ROS6 until a separately reviewed supported transport exists |
| Ubiquiti Rocket AC/Gen2 | Mapped SNMPv3 authPriv GET airMAX contracts | Actual firmware/MIB/OIDs, available RSSI/SNR/noise/rates/capacity/quality/GPS/errors; missing fields remain unavailable; no arbitrary walk |
| Ubiquiti AF11 | Generic/mapped microwave/export foundation only | Validate an implemented model-specific mapping, carrier/capacity/reset semantics;11GHz or inventory model alone is not compatibility |
| Ubiquiti Wave Pro/LR | **No certified dedicated live Wave collector** | Stay unavailable until a reviewed real interface/mapping and firmware acceptance exists; no invented API or antenna profile |
| Cambium ePMP4600c/4625 | Mapped read-only SNMPv3/RF contracts | Actual model/firmware/MIB/OIDs, subscriber authorization, units/MCS/errors/counters/staleness; no invented generic REST endpoint |
| Racom Ray3 | Mapped SNMPv3 microwave carriers | Model/firmware/MIB/OIDs, RSL/SNR/modulation/adaptive capacity/carrier and Ethernet state, permitted endpoint and authPriv |
| Aviat | Mapped SNMPv3 microwave carriers | Exact supported model/build/MIB, multicarrier relationships/counters/units and missing metrics; no generic API certification |
| Powercode | Synthetic/export read-only account/service reconciliation | Native field/schema/API or approved SELECT-only view, stable IDs, active/inactive/deletion/stale semantics, correct tower/sector/alternate paths, consent/opt-out provenance; no billing/provisioning writes |
| Grafana | Private approved UID links/restricted metrics and mock health | Actual deployed version/least-privilege datasource/service account, TLS/VPN/SSO/MFA, approved UID filter, cookie/CSP/embedding configuration; no anonymous/admin access |
| WordPress network status/signup | Isolated allowlisted public contracts/examples | Separately approved origin/HTTPS/cache/rate limits/Turnstile where signup applies, public privacy test and independent publication approvals. No live-site change here |
| Approved Fiber Designer integration | Versioned approved/published as-built import; draft rejection, stable IDs and conflicts | Actual independent app export/API version, read-only credentials and data ownership. NOC remains operational authority; Designer remains engineering authority; no other-repository changes |

All exact firmware versions above remain **NOT VERIFIED**; do not fill them from fixture labels. Reuse [NOC116 detailed compatibility](../NOC-116/compatibility.md) and [NOC119 operator procedures](../NOC-119/operator-acceptance.md).

## Stop, rollback and incident ownership

- [ ] Define named pilot owner, observation window and escalation contacts before activation.
- [ ] Stop on public exposure, wrong endpoint/privilege, untrusted artifact, clock drift, missing scanner protection, lost/duplicate history or unexplained load.
- [ ] Disable only the approved integration/scheduler and stop NOC polling if needed; close its egress/revoke its dedicated monitoring account under operator authority.
- [ ] Preserve queues, telemetry, audits, incidents, failed backups and source data. Do not change equipment or restore over newer records.
- [ ] Keep notification delivery/public publication/automatic RF remediation disabled even during a read-only equipment pilot.
- [ ] Record separate installation-test authorization versus live-device/production-release authorization. This document grants neither.

## Tasks Codex cannot complete alone

Independent legal/security risk acceptance; production signing/registry custody and approval; actual network/VPN/MFA/SSH/NTP/firewall setup; deployment/current scanner definitions; independent off-site storage/key custody and physical recovery; real firmware/provider/Powercode/WordPress/Designer compatibility. These require the named owner’s explicit authority, protected access and executed evidence. They are finite acceptance tasks, not automatic future development milestones.
