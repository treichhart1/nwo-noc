# Operator acceptance / recovery and pilot checklist

**NO-GO for a live pilot.** No connections, firewall/SSH/VPN changes, messaging or
publication are authorized by this document. Obtain separate device/server approval.
All live integrations/delivery/publication remain disabled by default.

## Host and controlled installation

Use the NOC115 deployment-runbook plus NOC116 signed-release prerequisites. Never
install over an existing installation. Actual scripts: sudo ./install.sh (approved
manifest, backup recipient and protected TLS arguments), sudo ./verify.sh,
sudo ./upgrade.sh from an independently approved compatible checkout. No historical
migration is rewritten. Purpose=controlled-test is explicitly isolated, not production.

Record before/after OS image/hash, kernel/architecture, CPU/RAM/disk, package list,
Docker/Compose versions, application digest/source,28 migration heads, systemd units,
private TLS/auth results, service status and retained logs. Minimum-ISO Docker bootstrap,
hypervisor reboot/power-loss and second-host restoration remain NOT TESTED until run.

Read-only host checks (run only on approved disposable server):

```sh
sudo ./verify.sh
sudo systemctl status nwo-noc.service nwo-noc-ingress.service
sudo systemctl cat nwo-noc.service nwo-noc-ingress.service
sudo systemctl list-timers nwo-noc-backup.timer
sudo nginx -t  # only if native Nginx exists; packaged Nginx is checked by verify.sh
sudo sshd -T  # only if SSH server exists; inspect locally, redact output
sudo ss -ltnp
sudo journalctl -u nwo-noc.service --since today
```

Confirm dedicated nologin account/no Docker group, systemd resource/restart/sandbox
limits, root-owned secret0600/data0700, unprivileged Unix ingress, loopback dashboard,
no DB/Redis host ports, internal-only application network and bounded log retention.
Verify firewall/egress/VPN/MFA from separately approved independent vantage points;
a loopback listener is not proof that an upstream reverse proxy/firewall is private.
Do not edit SSH, VPN, firewall or authentication configuration in this milestone.

## Secure local administrator recovery

Requires an independently authorized privileged console and downtime/backup review.
Never use PASSWORD=... make variables or --password arguments; legacy make target now
refuses. From the approved private host, identify the exact installed Compose project
and run inside its backend container using the protected operator console:

```sh
python -m app.reset_admin --approve-local-recovery --username admin \
  --reason 'Approved operator recovery ticket reference'
```

This command is inside the privileged operator-selected backend container, not a
public API or automatic host command. Interactive hidden input/confirmation is default;
--password-stdin supports a protected broker, never echo a literal password in a
shell pipeline/history.16–72 UTF-8 bytes, no NUL/newline. Audit records contain OS UID,
reason and target; no password. Existing sessions at/before audit time are denied;
wait one second before new login because token issue times use whole seconds.
Recovery requires DB availability and is transactional. Preserve the audit and review
all sessions/operators afterward. OS UID inside a container alone does not identify
a human: retain privileged-console/operator access logs. Never bypass authentication.

## Document scanning

Default disabled scanner quarantines uploads; error/infected/unapproved documents
are not downloadable. File signatures, byte limits, private storage, checksum and
role checks remain. ClamAV INSTREAM replies must be bounded, complete, NUL-terminated
and exact stream: OK before clean classification; partial/fake/ambiguous responses
fail closed. Synthetic/EICAR mocks are not a deployed scanner.

Before operational acceptance install a supported scanner with updated signatures,
private Unix socket permissions allowing only intended runtime access, bounded CPU/
memory/timeouts and monitoring. Mount only the approved socket, not public TCP. Test
harmless and EICAR fixtures through the actual upload/rescan/approval/download path,
scanner outage, quarantine, checksum and unauthorized retrieval. Record scanner engine/
signature DB version, timestamps and incident response. No scanner is claimed active here.

## Complete disaster recovery (independent host still NOT TESTED)

1. Quiesce approved synthetic writers; create encrypted backup with
   sudo python3 /opt/nwo-noc/current/scripts/host/manager.py backup --quiesce.
2. Verify ciphertext SHA256, age decrypt and pg_restore listing. Retain matching
   source/images, DB, configuration, encryption keys, documents, Redis and release
   history; key custody must be separate from encrypted backup and server.
3. Copy encrypted archive and signed release evidence to an independently provisioned
   second isolated host via an approved authenticated channel. Verify transport hash.
4. On that host independently verify signing-key fingerprint, manifest signature,
   source/image digests; recover age identity from separate custody. Restore into NEW
   database/filesystem targets, never over newer data. Retain original installation.
5. Run exact matching application/schema; verify historical customer/inventory/IPAM/
   maintenance/document/incident/finance references and checksums, roles and audit trail.
   Record actual RPO/RTO/commands/errors. Same-host isolated restore is not this test.
6. For post-migration upgrade failure retain recovery-required.json, target/old releases
   and encrypted backup; do not remove guards, automatically downgrade, start old code
   on incompatible schema or silently overwrite a database. Separate recovery approval.

## Pilot configuration and acceptance (not activation)

Reuse deploy/pilot templates; no parallel inventory or deployment application.
Prepare one approved MikroTik ROS7, one reviewed Ubiquiti/Cambium AP, one reviewed
microwave radio, existing tower ID and synthetic customer mapping. Use example.invalid
names/documentation IPs only; put actual device addresses/secrets in separately protected
operator files after approval. No production values belong in repository templates.

Initial proposal:120s+ intervals, concurrency1, bounded retries/deadlines/leases,
minimum read-only privileges, explicit single endpoint/OID allowlists, verified TLS or
SNMPv3 authPriv. No scans/SET/config/reboots/remediation. OFFLINE_MODE remains true and
integration flags false in this candidate. The host package intentionally has no
reviewed offline-to-live activation configuration; do not disable its safety guards.

Approve each compatibility row and actual egress/DNS/CA scope before any connection.
Verify missing/stale data stays unknown, duplicate suppression, consecutive recovery,
worker/DB/Redis failures, audit identity, measured CPU/memory/latency and retention.
Keep SMS/email/provider writes/public publishing disabled and Powercode synthetic.
No customer approval, equipment purchase, routing/VLAN changes or technician dispatch.

Emergency stop: sudo systemctl stop nwo-noc.service (NOC only). Verify no collectors
remain and preserve logs, data, configuration and backups. Integration disable must
be accepted on the actual approved deployment before pilot sign-off. Rollback follows
schema-aware recovery above. Operator signatures/evidence references are mandatory;
this checklist does not authorize or initiate a live pilot.
