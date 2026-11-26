# Operator-owned phased acceptance

No live access is authorized here. Use a named owner, approval reference, exact source/image identities, timestamped observations and PASS/FAIL/BLOCKED/NOT TESTED for each step. External delivery, public publishing, automatic RF remediation, customer provisioning and configuration writes remain disabled throughout every phase.

## Before PhaseA — operator host preparation

1. Approve a disposable isolated Ubuntu24.04 host and [hardware/storage](README.md). Preserve console access, existing service ownership and source/backup state. Run [read-only preflight](SERVER-PREFLIGHT.md); missing evidence is not PASS.
2. Set a static private address using the approved IP plan under console protection. Preserve Netplan configuration; operator validates `sudo netplan generate`, then `sudo netplan try` before acceptance. This document makes no network changes.
3. Verify approved DNS resolvers (`resolvectl status`), private NTP source (`timedatectl timesync-status`, `timedatectl show -p NTPSynchronized`), offset and post-reboot soak. Existing VM evidence with NTPSynchronized=no is **not** accepted time synchronization.
4. Approve VPN-only access with named identities and MFA at the VPN/IdP. Native application MFA is not certified. Verify SSH key-only authentication, restricted source and break-glass/console access; inspect `sudo sshd -T` and validate proposed configuration using `sudo sshd -t`. No automatic SSH changes.
5. Operator validates default-deny IPv4/IPv6 INPUT/OUTPUT and Docker forwarding (iptables/nftables backend), not UFW status alone. Allow only approved VPN/SSH, private DNS/NTP/CA, bounded signed package/registry bootstrap and encrypted backup destinations. Do not publish PostgreSQL5432, Redis6379, backend8000, Grafana administration or loopback18090 to the internet. Test from an independent unauthorized source. Do not paste generic firewall commands that could lock out the server.
6. Verify private dashboard TLS chain/SAN/fingerprint and trust distribution; no -k, anonymous Grafana or public DNS/Let's Encrypt automation. Dedicated application UID10001, restricted config/data/log/backup ownership and systemd hardening remain from the package; inspect actual permissions, do not recursively chmod existing data.
7. Deploy an independently approved ClamAV/current authenticated definition update process with restricted Unix socket permissions and a reviewed read-only mount/service arrangement for actual document consumers. MAINTENANCE_DOCUMENT_SCANNER=clamav and MAINTENANCE_CLAMAV_SOCKET alone do not install/mount the scanner. The packaged core does not configure a production scanner. Never expose3310 or world-writable sockets. Record engine/definition signatures/age, approved update egress, safe EICAR/clean/timeout/unavailable/quarantine/restart/protected-retrieval tests. CI safe test definitions are not production definition currency.
8. Review supported security updates, signed repositories, journald/logrotate retention, capacity/clock/scanner alerts, encrypted backups and separate offsite recovery keys. Approve downtime/RPO/RTO and backup retention. Do not enable unattended software major upgrades. Legal/native-risk/signing/publication decisions remain separate from isolated-lab authorization.

## PhaseA — isolated application installation

Follow [LAB-INSTALLATION](LAB-INSTALLATION.md) only after approval. Record install exit status, source/tree, four immutable local IDs or independently approved registry digests, full scans/SBOM and actual verify results. All live adapters remain false/OFFLINE_MODE=true.

Initial administrator order:

- Privately initialize the administrator (16–72-byte password, no default); verify login via trusted loopback TLS/VPN tunnel and role boundaries. Store recovery credentials in the approved broker, not documents/Git.
- Review named administrator/operator/technician/viewer access using the existing roles. Confirm unauthorized inventory/documents/customer/financial access is rejected. VPN/IdP MFA is independently required.
- Create synthetic Site/tower/POP records, existing inventory Device/Asset references and sector/backhaul relationships. Reuse IDs, never duplicate authoritative inventories.
- Document topology and alternate paths, management-interface/IPAM relationships and synthetic customer/service associations; do not assign documentation IPs to actual devices.
- Review alert thresholds/hysteresis/consecutive samples, stale/missing behavior and internal incident ownership using fixtures. Missing power/RF is unknown, not healthy or confirmed outage.
- Verify health, unique worker scheduling, protected persistence/logs, backups, guarded upgrades and recovery. Reboot the lab under separate approval; verify services and boot ID.
- Verify customer communications/public status remain approval-controlled and no external transmission/public publication occurs. Synthetic incident→impact→approval→simulated recovery does not contact providers.
- Capture CPU/RAM/disk/query/backlog measurements on the selected hardware. Strict1000-device CI performance is scoped synthetic evidence, not this host's certification.

## Routed WISP configuration worksheet — documentation only

Reuse deploy/pilot/context.example.json, mikrotik-native.example.json, mikrotik-router.example.json, wireless-access-point.example.json and microwave-backhaul.example.json. They are inert templates, not loaded activation commands. A planning worksheet may contain:

```json
{
  "approved": false,
  "phase": "A-synthetic-only",
  "site_id": 1,
  "devices": [
    {"existing_device_id": 1, "management_ipv4": "192.0.2.10", "path": "native-api-ssl", "port": 8729, "interval_seconds": 300},
    {"existing_device_id": 2, "management_ipv4": "192.0.2.11", "path": "validated-snmpv3-authPriv-get", "port": 161, "interval_seconds": 300}
  ],
  "allowed_sources": ["192.0.2.100/32"],
  "backhaul_link_id": null,
  "sector_id": null,
  "external_service_id": "SYNTHETIC-SERVICE-1"
}
```

All addresses are RFC5737 documentation values, not NWO management networks; replace only under operator approval. The worksheet is **not an executable application schema**. Resolve exact existing IDs and map routed tower paths, ingress/source NAT if any, explicit per-device egress and OSPF adjacency expectations. Do not allow whole RFC1918 ranges, infer management reachability from tower proximity, automatically discover subnets, change routes/VLANs or assume each service has one path. OSPF monitoring is read-only and optional; preserve redundant path uncertainty.

## PhaseB — representative equipment, separately authorized

Start concurrency1/300s polling with one MikroTik router, then one RouterOS MikroTik switch, one supported microwave backhaul and one supported access point, **one at a time** after independent approval. Use [equipment acceptance](EQUIPMENT-COMPATIBILITY.md). Confirm exact model/firmware/MIB/secure method before credentials or egress activation. Wave/AF5XHD/ambiguous Cambium names remain blocked without the required contract evidence.

Core lab policy intentionally forbids live overrides. Operator must review a separate supported deployment configuration/secret mounts/access policy before activation; do not hand-edit generated signed release files, flip global flags to bypass validation or run a second collector. Keep unsigned controlled-test artifacts separate from production-approved releases. Do not expand before normal/missing/stale/error/recovery/duplicate/counter-reset cases and device load/connection logs are accepted. Synthetic fault injection is not permission to reboot or disrupt live equipment.

## Powercode preparation — currently BLOCKED for live access

Existing NOC95 stable customer/service reconciliation and synthetic export fixtures are reused; **POWERCODE_LIVE_ENABLED=true is rejected by Settings**. There is no approved live adapter that can be enabled by entering a URL. Do not claim endpoint configuration is sufficient.

The operator must supply read-only vendor API documentation/version/approved endpoint contract or SELECT-only export/view, stable customer_id/service_id and service status semantics; tower/sector/device/service-location associations; active/inactive/deleted/multiple-service/duplicate/stale behavior; plan mapping; consent/opt-out source and timestamps; polling/rate/timeout limits; TLS/credential storage and minimum-permission evidence. Redact billing/payment/contact secrets from fixtures. Use existing fixture imports/dry runs with synthetic data first. Never access production Powercode during this assignment or write accounts/billing/plans/provisioning later through this integration.

Before a future authorized adapter: exactHTTPS allowlisted origin, approved CA, brokered private secrets, conservative300s or stricter vendor rate, bounded timeouts/backoff, synchronization freshness and failure isolation. Stale/unknown mappings must not become confirmed customer impact; alternate working paths preserve service. Unknown consent/opt-outs/invalid or duplicate phones exclude notification eligibility. No SMS/email or public status publishing is enabled by mapping acceptance.

## PhaseC — one approved tower

Only after PhaseB signed acceptance. Verify documented dependencies and redundant paths, supported telemetry units, collector isolation, storage sample/unique receipt preservation, alert stability/hysteresis, capacity based on contemporaneous radio capacity, real load and quiet recovery windows. Do not treat interface speed as wireless capacity or missing AC telemetry as commercial power loss. Record a bounded observation period, escalation contacts and criteria for stopping.

## PhaseD — approved gradual expansion

Approve additional towers individually using measured hardware/network/TLS/collector/database/outbox response and recovery evidence. Recheck retention/free space, CPU/RAM, scanner costs and backup windows. Do not equate the36passing1k synthetic rounds with a1000-live-device SLA. Maintain300s until measured need and separately reviewed capacity justify a change; never reduce security/durability/performance assertions.

## Stop, recovery and final signoff

Stop on wrong endpoint/privilege, untrusted artifact, TLS failure, public exposure, clock drift, lost/duplicate telemetry, unexplained device load, collection error storms, unavailable required scanner or failed verification. Disable only the approved NOC job/integration, stop NOC polling if necessary, preserve outbox/telemetry/audits/backups, close approved egress and revoke the dedicated monitor account under device-administrator authority. Do not change router/radio configuration, force topology updates, message customers or publish incidents.

Upgrade failure after migration requires preserving recovery-required.json and newer data; restore a matching backup/release to an empty independent host, not silent schema rollback. Physical offsite recovery and independent key custody must be executed by the operator. Record each finite requirement in OPERATOR-REQUIREMENTS.json with evidence/owner before any operational GO. This checklist grants no deployment or live-access authorization.
