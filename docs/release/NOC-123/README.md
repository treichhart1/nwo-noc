# NOC-123 — guided isolated lab package

**Controlled-test candidate only. Production/live-device NO-GO.** No NWO server installation, production account, network change, customer message, public publication, production signing or PR merge is authorized.

Base draft PR126, exact8999b6c26c8c0790df2159a2c2587fa32b00a1d2, verified run756 (45872128579), all10jobs SUCCESS. Branch feature/noc-123-lab-installation preserves the complete unmerged upstream chain. No applicable AGENTS.md was found. The original clean checkout715a749 remains untouched on the full cloud filesystem; separate exact-source temporary working copies are for source/unit checks, never durable benchmark claims.

## Scope and implementation

scripts/host/lab.py adds **plan → prepare → install**, reusing existing host preflight, shared source/permission policy, pinned scanner, image SPDX exporter, install.sh and verify.sh. It cannot select production mode. Existing production signature/manifest/independent-key/evidence checks and upgrade/recovery behavior remain intact.

Plan is read-only. Prepare needs explicit root operator authorization, an independently reviewed exact source/tree and clean checkout, approved private management and backup custody, and a new protected directory outside source. It builds four source-labelled images, captures immutable local IDs, audits complete Python test/runtime/audit locks and npm including development dependencies, runs full all-severity Trivy/license scans with mandatory no-HIGH/CRITICAL gate, produces per-image SPDX and binds all records to source/locks/images/file SHA256. No package/firewall/network/SSH configuration is changed. Build-only audit tooling is separate from runtime images. Preparation may run on an approved builder; it does not certify that builder as the target installation server.

Install requires **fresh independent confirmation**, existing-install rejection, real target hardware/dependency/preflight checks including synchronized time, fresh <=24h evidence and exact loaded/source-labelled images. It generates lab-only seven-day localhost TLS material, prepares only a controlled-test-purpose approval, delegates private administrator prompting/stdin to the existing initializer, installs and verifies. Failures preserve private logs/evidence and all data. No volumes, images, databases or existing services are deleted automatically. There is no unsigned-to-production conversion, advisory waiver or production signing key.

The final summary gives private URL/tunnel, verification/migration/admin/backup/disabled-integration status and troubleshooting. Only public age recipient and nonsecret backup-destination description are accepted; private keys/passwords are never CLI arguments or stored in authorization metadata. Copying backups offsite remains a separately configured operator action, not a promise from a description field.

## Testing and operational boundaries

New unit tests exercise source/evidence/authentication boundaries, stale or changed locks/scans/SBOMs, missing/mismatched identities, HIGH/CRITICAL rejection, private-key rejection, dry-run non-mutation and hardware failure. Initial executed result:33passed. Final exact-source counts and all ten CI conclusions are recorded in the draft PR terminal ledger after execution; no future result is asserted here.

The independent Ubuntu VM harness builds/audits with the actual guided preparation tool on a clean controller checkout, transfers the protected source-bound bundle and four images, then executes the actual guided first installation in an isolated VM. Negative corrupted/source/purpose/missing-image cases preserve separate failed copies. Existing administrator, encrypted backup, upgrade, source reboot, source-powered-off second-host recovery, restored history and recovered reboot tests remain. The VM is increased to4vCPU/8192MiB/120GiB sparse disk so target lab preflight can genuinely pass8GiB provisioned/100GiB filesystem requirements. The prior smaller installation fixture is historical evidence, not used to waive the new checks. Only one8GiB VM runs at a time; source is powered off before recovery.

A loopback-only CI SNTP responder binds standard UDP123, drops to the unprivileged controller identity, returns actual controller UTC and stops on private control marker, parent exit or bounded deadline. Guests allow only that private endpoint and must report actual NTPSynchronized=yes. This is executed test-clock evidence, not certification of a production time service, VPN or MFA. No real operator host configuration is changed.

**The strict performance job is unchanged**:1000devices/40POPs,15s per round, backend2CPU/768MiB, PostgreSQL2CPU/1GiB,8threads,pool10, persistent PostgreSQL/outbox and all fsync/receipt/sample assertions. The installation VM sizing change does not change this separate benchmark's limits or workload.

## Operator handoff

[QUICK-START](QUICK-START.md), [LAB-INSTALLATION](LAB-INSTALLATION.md), [TROUBLESHOOTING](TROUBLESHOOTING.md), [EQUIPMENT-INFORMATION](EQUIPMENT-INFORMATION.md). NOC122 equipment acceptance and host/security/operator requirements remain authoritative where unchanged.

Supported Expat2.6.1-2ubuntu0.7 and immutable Nginx1.30.8 fixes remain. Final scans/audits must run again on final exact images. Preserve SQLite/Expat66382/ACL/util-linux/systemd/other vendor-native risk, Redis/dependency licensing, production registry/signing/key custody, real server VPN/MFA/firewall/TLS/NTP/current malware definitions and physical offsite recovery requirements. Do not equate zero scanner HIGH/CRITICAL with all vendor fixes. Wave secure protocol/per-firmware MIB compatibility stays blocked.

This is a finite guided lab installation package, not another feature program. Next action is separately authorized empty-server testing with the exact reviewed source and operator-owned controls. Live collectors, Powercode, messages, public status publication and RF remediation stay disabled.

Latest local hardening selection:131passed, including36guided cases plus existing preflight/installer/artifact tests. The full earlier collection1179passed/971skipped; the three additional guards execute in final CI. All Docker helpers force the local Unix socket and a root-system PATH; extra authorization secret fields and insecure password fallback are rejected. The disposable controller explicitly installs age/python3-yaml/OpenSSL as test prerequisites. Real server security/production approvals remain absent.

Initial push758 stopped before VM boot because the new fixture attempted sudo after the existing NoNewPrivs/capability drop. Correction moves privileged bundle preparation and private UDP bind before that boundary; responder immediately drops identity and the hardened controller only adopts an original-user0700 stop marker. No capabilities/NoNewPrivs/permissions are weakened. Raw guided scans/audits/SPDX are retained separately without authorization, logs or TLS private keys. Actual final VM evidence is required after the correction.
