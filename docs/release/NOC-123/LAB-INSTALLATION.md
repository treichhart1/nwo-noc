# Guided workflow and initial configuration

Use [QUICK-START](QUICK-START.md). Existing install.sh/upgrade.sh/verify.sh, PostgreSQL16 migration head0028_customer_status and /opt,/etc,/var/lib,/var/log,/var/backups/nwo-noc layout are reused. No migrations are changed. Application UID10001/frontend101 and vendor PG/Redis privilege dropping remain intact.

## Approval and evidence

Without --execute, prepare/install only print a plan. An actual operation requires root, exact reviewed commit/tree, clean source, preserved complete Git ancestry and a protected new work directory outside source. Existing managed installation paths cause rejection. Interactive input uses /dev/tty; administrator password input belongs exclusively to the existing initializer. Public age recipient is not a password or private identity.

Controlled automation may use --authorization-file pointing to a **root-owned0600 regular file in a protected root-owned parent**. This is useful for a reviewed disconnected lab and CI, not a production trust shortcut. Contract:

```json
{
  "purpose": "controlled-test",
  "commit": "REVIEWED_40_CHARACTER_COMMIT",
  "tree": "REVIEWED_40_CHARACTER_TREE",
  "reference": "INDEPENDENT LAB APPROVAL REFERENCE",
  "confirmation": "PREPARE ISOLATED LAB",
  "ssh_host": "lab.example.invalid",
  "backup_destination": "Approved independent encrypted backup destination description",
  "backup_recipient": "REPLACE_WITH_PUBLIC_AGE1_RECIPIENT",
  "isolated_lab_authorized": true,
  "private_management_reviewed": true,
  "native_risks_reviewed": true,
  "backup_custody_reviewed": true
}
```

For install require a newly reviewed file with confirmation=INSTALL ISOLATED LAB. Never store passwords, private keys, provider tokens or customer data here. The reference/booleans record the operator's review; they do not deploy or certify VPN/MFA/offsite controls. --password-stdin exists only for a protected broker or disposable harness; it is forwarded directly to install.sh and never captured by the wrapper. Do not use literal shell passwords or echo secrets into commands.

Prepare builds four revision-labelled images plus a **build-only** dependency auditor. Runtime image IDs are resolved and retained, full pinned Python closure and npm including dev are audited, full Trivy vulnerability/license reports and per-image SPDX retained. Any HIGH/CRITICAL image or nonzero dependency audit fails. Medium/LOW vendor findings are retained for explicit isolated-lab review, never considered operationally waived. The manifest binds raw files/SBOMs/locks/source/images using SHA256. Install rejects missing/altered/expired (>24h)/future-dated reports, dirty/wrong source and missing/mismatched loaded images; no mutable tags are install approvals.

The prepared folder may be copied through an approved integrity-checked protected transport to a disconnected empty lab with the exact same reviewed source and four Docker images. Restore ownership/modes root0700 directories/root0600 files and independently compare manifest/transport hashes. **It contains no production signature and is not a production release.** No generic untrusted tar extraction helper is added; operator reviews transport before restoring it. Never copy a directory containing lab.key into a public artifact. Actual test harness uses a private pre-install evidence transport and retains all existing signed recovery controls.

Install runs the actual target preflight: Ubuntu/ABI/core/RAM/durable capacity/filesystem, Docker/Compose/systemd/private address, synchronized time, ports and empty managed paths must pass. Static configuration, DNS approval, true VPN/MFA, firewall/Docker forwarding and physical SSD/PLP remain reviewed operator controls. The wrapper never changes these to manufacture readiness. Lab TLS is localhost-only/seven-day; it is not production PKI. Missing scanner/current definitions remain a deployment blocker for unrestricted document use; quarantine/fail-closed retrieval policies must remain enabled.

## Ordered synthetic setup

1. **Administrator login:** trusted private tunnel/TLS, secure initializer completion, named role review. External VPN/IdP MFA remains mandatory; native application MFA is not certified.
2. **Organization configuration:** review existing branding/settings and operator contacts through supported configuration; do not invent an organization API or edit application source.
3. **Towers/POPs:** create synthetic Site identifiers, reuse inventory foundations, no actual coordinates or device endpoints required.
4. **Device inventory:** synthetic devices/assets/categories/locations and documentation IPs; preserve stable IDs, no duplicate monitoring inventory.
5. **Topology:** document synthetic links/sectors/fiber/redundant paths and customer-service relationships. No live routing changes.
6. **Monitoring settings:** review intervals/retention/staleness with fixtures. OFFLINE_MODE=true; all live collectors, native API/REST/SNMP, Powercode and external providers disabled. Do not enable a second scheduler or synthetic worker merely to populate a dashboard.
7. **Alerts:** review thresholds/hysteresis/consecutive recovery, missing vs healthy vs stale and internal ownership. No messages/public publication/remediation.
8. **Backup scheduling:** approve quiescent window/recipient/independent destination/retention/RPO/RTO; execute encrypted backup and matching empty-host restore before enabling the existing timer. Destination description alone does not upload backups.
9. **System health:** verify all services/DB migrations/worker freshness/logs/private bindings, permissions, storage and actual authorized reboot recovery. Record CPU/RAM/disk/load; synthetic capacity is not a real-device SLA.
10. **Equipment acceptance preparation:** collect [equipment information](EQUIPMENT-INFORMATION.md), then obtain separate device/network/deployment approval. Initial future acceptance is one device at a time/300s/concurrency1. The offline lab package does not grant live overrides.

## Upgrade and recovery

Use existing NOC122 [lab instructions](../NOC-122/LAB-INSTALLATION.md), root UPGRADE.md/BACKUP_RESTORE.md and the reviewed signed/controlled-test purpose boundaries. A target upgrade needs a separate compatible exact-source approval and upgrade_from including installed commit. Do not rerun fresh guided installation as an upgrade. If migration starts and failure creates recovery-required.json, preserve it and all newer data; no automatic schema downgrade or silent restore. Restore matching release/database/config/docs/keys to another empty host and obtain cutover approval. Physical offsite acceptance remains operator-owned, even after two CI VMs pass.
