# NOC-124 — release freeze and installation handoff

Controlled isolated lab only. No production or live-equipment approval. Original NOC-123 PR127 remains draft/unmerged at commit `97555d86802a9ee64a5d0d761fa5afcd84c5c339`, tree `60b61b849735e9ac8da177c1da6e0937b22e4842`. Do not rewrite that history or merge older PRs. The corrective child and its exact installation commit/tree/commands are recorded in the final PR acceptance ledger after CI; do not approve a moving branch as a release.

## Verified inherited evidence

Run773, head97555d8, all ten jobs SUCCESS. The checkout actually tested by the PR run is merge revision27612c49a06bc74bc28a2b9af5b7b46e35a24630 with the same tree. Run772 also passed ten jobs. The final PR127 ledger supersedes chronological progress counts in NOC123 README.

Actual Ubuntu24.04 KVM installation, private TLS administrator authentication, guarded upgrade/encrypted backup, source reboot, source-powered-off signed second-VM recovery, restored data/configuration/documents/keys/history and receiver reboot passed in both runs. Preparation was on a disposable Ubuntu controller and installation on the independent guest. Physical off-site failure domains and real VPN/MFA were not verified.

Thirty-six full durable1000-device/40POP rounds: min8.572s, median10.5985s, max13.189s, zero over15s;36000samples and36000unique receipts for those rounds. Backend2CPU/768MiB, PG2CPU/1GiB,8threads/pool10, ext4 PG/outbox volumes and all fsync/locking/count assertions unchanged. These are synthetic2ms transport measurements, not a live-equipment SLA.

Backend1194passed/971skipped; PostgreSQL/RF1157passed; pilot security152; Grafana43; customer status64; integration54; audit36; browser69executions/27invocations; host78checks; readiness5; legacy upgrade/restore11. Python/npm audits zero, including development dependencies. Backend38non-HIGH/CRITICAL findings; frontend/PG/Redis0. Raw scans/SPDX/provenance/archive digests are retained in PR127/run772/773, not recreated by this handoff.

## H124-1 — scheduled backup sandbox recovery (HIGH, corrective exception)

The frozen `deploy/host/nwo-noc-backup.service` uses ProtectSystem=strict but does not include `/run/nwo-noc-ingress` in ReadWritePaths. `Host.backup` quiesces application writers; its caller's finally block calls `Host.start`, which calls prepare_ingress and clears/recreates the frontend socket. Those operations require writes to that directory. systemd255 documents the entire hierarchy as read-only except /dev,/proc,/sys and explicit exceptions. The main NOC unit already has this narrow exception. A scheduled backup could therefore leave application recovery incomplete despite a successfully encrypted archive. Prior tests exercised manual backup and unit syntax, not this service sandbox.

Correction: add only `/run/nwo-noc-ingress` to the backup unit's existing ReadWritePaths. Keep ProtectSystem=strict, ProtectHome, NoNewPrivileges, PrivateTmp, all resource/security controls and the disabled timer. No application code, lockfile or migration changes.

Regression: exact unit permission contract; in the actual independent Ubuntu VM, run the installed prepare_ingress operation under real systemd with the original read-only profile (require EROFS) and the narrowly corrected profile (require success), then start the real backup.service, verify one new ciphertext/current-source checksum/decryption and full application recovery, and assert the timer remains disabled. Existing backup-count, upgrade, reboot, signature, second-host recovery and strict performance assertions are unchanged. Local namespace mounting was denied; it is not represented as a successful systemd test. Final executed evidence and the corrective source identity belong in the final PR ledger.

## Ancestry

All126 upstream PR tips were checked against8999b6c:125direct ancestors; PR86 is the documented historical exception. Audit34e54e226f93159adf7d2dc52548d36aad277b46 is present asad61cde731a7707af44734a7261474af3409671e; both stable patch IDs ared9051e1a8473d39b68352ad48af984c166b1c13c. See docs/NOC-110-full-integration.md and its manifest. Do not merge/cherry-pick that old audit again. NOC123 is nine linear commits beyond PR126, zero behind; this correction is a child, not a replacement implementation.

## Protected server worksheet — no secrets

Keep completed management details in approved private operator records, not public PR comments. Never record passwords, tokens or private keys.

|Field|Operator supplies|
|---|---|
|Named lab authorization / owner|Reference, owner, scope, date and downtime approvals|
|Hostname|Private DNS name|
|Static private IP / subnet|Address and CIDR/netmask|
|Gateway / management interface|Approved route/interface|
|DNS / NTP|Approved resolver and time-source addresses; actual sync evidence|
|CPU / RAM|Model, x86-64-v2, dedicated cores, installed/available RAM|
|Storage|SSD/NVMe model, filesystem, usable/free capacity, flush/PLP evidence|
|VPN / MFA|Approved access and MFA policy/reference; no recovery codes|
|SSH|Operator username, port, source restriction and public-key fingerprint/reference only|
|Dashboard TLS|Public-certificate fingerprint and client trust procedure|
|Backup|Destination, independent failure domain, retention, RPO/RTO, approved quiescence window|
|Public backup recipient|Public age1 recipient only; private identity kept independently|
|GitHub access|Approved repository access process; no token in URLs or worksheet|

Minimum lab:4modern x86-64-v2 cores,8GiB RAM,>=100GiB persistent ext4/xfs filesystem and>=30GiB free before preparation. Prefer8cores/16GiB/>=250GB enterprise SSD/NVMe for planning, separate GIS/document/history capacity and independent encrypted backups. Virtual-disk testing is not SSD/PLP or production capacity certification.

Operator installs/reviews missing prerequisites from approved signed Ubuntu24.04 repositories: Git, Python3.12/python3-yaml, Docker>=24, Compose major2>=2.24, Docker buildx, OpenSSL, age/age-keygen, CA certificates and logrotate. Preserve compatible Docker/services. Approve bootstrap registry/PyPI/npm/Ubuntu egress separately. No management-network scanning. Configure private IP/DNS/NTP, VPN/MFA, SSH keys/default-deny firewall/Docker forwarding before approving installation; the guided tool does not perform those changes.

## Installation and access procedure

Use the exact corrected source commit/tree and complete command blocks in the final acceptance ledger, not NOC123's historical identifiers or a branch tip. Review all prerequisites and PASS/WARNING/FAIL/NOT VERIFIED results. Plan does not authorize changes. Prepare and install each require separate interactive confirmation and a root-private new evidence directory outside the checkout. Do not edit prepared hashes or reuse failed preparation directories.

The guided tool asks for the lab authorization reference, host-only SSH/VPN hostname, backup destination description/public recipient, isolation/security/key-custody reviews and PREPARE ISOLATED LAB then INSTALL ISOLATED LAB. Administrator password is privately entered through the existing initializer,16–72UTF-8 bytes; never argv/environment/worksheet. No default password; default initial username admin.

Verify localhost lab certificate fingerprint through an authenticated independent channel, transfer/trust only the public certificate on the workstation and use a loopback-only SSH tunnel. Never copy lab.key or disable TLS verification. Open https://localhost:18090/ and authenticate. The lab certificate lasts seven days; it is not production PKI. No public DNS/listener or anonymous Grafana. Verify after install and after separately authorized reboot; kernel boot IDs must differ and units/DB/workers/frontend must recover automatically, not through manual starts that manufacture a pass.

Manual backup requires separate downtime approval and `manager.py backup --controlled-test --quiesce`; it stops writers and restarts services. Preserve .tar.age and .tar.age.json under /var/backups/nwo-noc, verify integrity, copy through approved encrypted off-host procedures, and restore to another empty host with matching source/images/keys. Metadata restore_tested=false is not recovery acceptance. Do not overwrite newer databases. Keep the timer disabled until the actual service test, restore/key-custody evidence and a UTC downtime window/retention policy have operator approval. Existing default is02:00UTC plus up to15min randomized delay; offsite copying/retention is operator-managed.

On any failure stop, preserve state/logs/backups/recovery-required.json and follow existing TROUBLESHOOTING.md/BACKUP_RESTORE.md. No volume deletion, healthcheck bypass, source reset, schema downgrade or silent restore. A changed migration needs an explicit recovery plan. Optional/live integrations stay disabled.

## Ordered synthetic initial configuration

1. Log in privately, review named accounts/roles and external VPN/IdP MFA.
2. Review existing organization branding/settings/operator contacts; do not invent an organization API.
3. Create synthetic Site/tower/POP records with stable existing identifiers.
4. Create synthetic Device/Asset inventory, avoiding duplicate monitored assets.
5. Document synthetic physical/logical topology and redundant dependencies.
6. Document IPAM VRFs/prefixes/VLANs/interfaces/assignments; database documentation only.
7. Review monitoring intervals/retention/staleness using fixtures; OFFLINE_MODE=true, all live collectors/providers disabled, no duplicate workers.
8. Review alert thresholds/hysteresis/consecutive recovery; unknown/stale is not healthy or a confirmed outage. No messages/public publication/remediation.
9. Create synthetic maintenance/work orders/checklists; documents remain protected/quarantined until scanner acceptance.
10. Approve backup scheduling/restore and system health: migrations, fresh worker heartbeats, private bindings, permissions, capacity, logs, restart/reboot evidence. Keep timer disabled until approved.

## Equipment worksheet

Copy a row per actual device into protected records; no credentials. Default approval=false/live_test_executed=false. Reuse Site/Device/Asset/Link IDs.

|Vendor / family|Model/part / firmware full build|Private management IP / port|Protocol contract|Official MIB / revision / SHA256|Role / tower / IDs|Read-only account-policy / source allowlist / CA reference|Owner / acceptance status|
|---|---|---|---|---|---|---|---|
|MikroTik CCR1036/CCR2116/RB5009/CRS/netPower|To supply|To supply|Native API-SSL8729 for supported ROS6.43+/7; separately approved ROS7 HTTPS REST; SwOS-only unsupported|N/A for native; supply official MIB if another supported SNMP path is selected|To supply|Dedicated read,api native policy; trusted CA/IP-SAN; no write/reboot/sensitive rights|NOT VERIFIED|
|Ubiquiti airMAX/AF5XHD/AF60-XR/Wave|To supply|To supply|Mapped secure implemented contract only; Wave remains BLOCKED|Exact official per-firmware archive/imports/revision/hash/instances/units|To supply|Validated authPriv or documented secure API; no community-string/TLS bypass|BLOCKED pending model evidence|
|Cambium Force4600C/ePMP|Exact part required; do not substitute4600cAP/4625|To supply|Mapped SNMPv3 authPriv GET where validated|Official MIB/imports/hash/units/indexes|To supply|Approved read-only account and explicit source/endpoint allowlist|NOT VERIFIED|
|RACOM RAy3|Exact band/build|To supply|Mapped SNMPv3 authPriv GET|Official per-carrier/ACM MIB mapping/hash|Backhaul / tower / link IDs|Approved read-only policy/allowlist|NOT VERIFIED|
|Aviat microwave|Exact platform/band/build|To supply|Mapped SNMPv3 authPriv GET|Official platform/carrier MIB mapping/hash|Backhaul / tower / link IDs|Approved read-only policy/allowlist|NOT VERIFIED|

Native MikroTik compatibility is synthetic-protocol evidence, not live firmware certification; older NOC120's ROS6 unsupported statement is historical/superseded by NOC121/122. Temperature/OSPF/ASIC/PoE availability is not assumed. Wave needs exact official ui-mib.zip/modules and verified secure management/field mapping; no guessed OIDs, inherited airFiber mappings or insecure fallback. See NOC122/EQUIPMENT-COMPATIBILITY.md and NOC123/EQUIPMENT-INFORMATION.md. Future equipment acceptance needs separate authorization, one device at a time,300s/concurrency1; no live changes or fault induction.

## Final operator acceptance categories

|Category|Status / owner action|
|---|---|
|Software functionality|Inherited CI/VM evidence verified; corrective backup service evidence must pass before recommending new source|
|Actual server preparation|Operator: hardware, approved packages, private networking/VPN/MFA/SSH/NTP/firewall/IPv6/Docker, trusted TLS and current malware definitions|
|Vendor security|Operator/security: review remaining SQLite/Expat/ACL/util-linux/systemd/other native findings; zero scanner H/C is not all-vendor-patches proof|
|Licensing|Operator/legal: Redis and dependency license decisions|
|Production signing|Operator/release owner: registry approval, immutable release artifacts, SBOM/audits/provenance, Ed25519 signature and independently trusted public key; no production key here|
|Equipment/providers|Operator/vendor: actual firmware/MIB/protocol, read-only permissions/CA/allowlists and comparison evidence; Powercode/Grafana/WordPress/Designer stay disabled until separately approved|
|Physical off-site recovery|Operator: independent location/storage/key custody and actual restore with authentication/history/documents/keys and reboot verification|

Decision: isolated lab only after named authorization and final corrective evidence review. Production/live-equipment NO-GO. No new general development milestone is proposed; the next step is actual authorized isolated-server preparation/testing.
