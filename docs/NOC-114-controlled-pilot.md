# Controlled read-only pilot review plan

**Not an activation instruction or authorization. No pilot is started by NOC114.**
AI NOC, AI Fiber Designer and MikroTik Management remain independent applications.
Use the existing [PilotPlan](../backend/app/pilot_plan.py) offline validator and
the [disabled templates](../deploy/pilot/); no second monitoring engine is introduced.
All templates intentionally fail approval validation until independently completed.
No address, password, token, production customer or real device is supplied.

## Preconditions and decision owners

The release owner pins the source/tree/images and approves the external root-owned
release manifest as described in [INSTALL](../INSTALL.md). A security owner records
OS/native-image scanning, Redis license review, VPN/MFA, host firewall, TLS, ingress
and egress validation. A recovery owner restores PostgreSQL, configuration, keys and
documents off-host using separately held age identity and matching application images.
The network owner approves one exact existing inventory device/endpoint, firmware,
read-only command/OID contract, polling interval and minimum-privilege credential.
An operator signs acceptance and a stop/rollback observation window. Missing evidence
is a NO-GO; a boolean in a JSON file is an assertion, not independent proof.

Begin on a dedicated Ubuntu24.04 amd64 acceptance server with no production routes.
Measure CPU/RAM/disk headroom with actual retention and intervals before approving
any route. The installer floor2cores/4GiB/10GiB is not production sizing. Keep backups
on protected encrypted storage; test off-host recovery before activation. Do not
open PostgreSQL, Redis, backend or dashboard ports publicly. Use the hardcoded
loopback ingress through an approved VPN/SSH tunnel; direct VPN-interface serving
requires separately reviewed proxy/TLS configuration. No public status gateway is
installed by the host package.

## Device selection and phased scope

Prepare, but do not activate, three candidate records: one MikroTik router, one
Ubiquiti/Cambium access point, and one Racom/Aviat microwave endpoint. The example
vendors are placeholders; choose only a validated supported interface. Use one
device at a time, not a three-device simultaneous approval. Reuse existing Site,
Device and Asset IDs; the context template contains no second inventory. Keep the
Powercode mapping synthetic, consent unknown and all delivery/publication disabled.

MikroTik: prefer RouterOS7 HTTPS GET allowlist on443; validate certificate and
identity/resource/interface/OSPF read permissions with a dedicated monitoring user.
RouterOS6 differences require their own validated interface contract; this template
does not establish compatibility. Radios: SNMPv3 authPriv GET of approved OIDs on161,
no SET or vendor write operation; validate actual firmware/MIB and supported metrics.
Missing/unsupported values remain unavailable, not zero or healthy.

Start at300seconds, concurrency1/batch1, bounded existing retry/backoff and timeouts.
Do not enable overlapping legacy and normalized pollers for the same device. Review
last-success time, failures and queue lag each day during a proposed7-day observation.
Use consecutive-sample validation and hysteresis from the existing RF policy; record
the operator-approved RSSI/SNR/noise/capacity/temperature thresholds per model rather
than inventing a common safe RF limit. Staleness must exceed the configured interval
plus bounded retry/deadline budget. Collector failure must not become an RF outage.

The host installer intentionally enforces offline/disabled live integrations. There
is no supported host activation switch in this milestone. A separately reviewed
deployment/egress configuration must preserve that installation baseline while
allowing only the one approved IP/protocol/port. Do not edit validate_env, disable
OFFLINE_MODE or add an unrestricted bridge to force activation. Existing offline
PilotPlan review always returns execution_enabled=false and network_calls=0.

## Privacy, access and retention

Use named least-privilege operator/technician roles, administrator approval for
policy changes, short sessions, no shared credentials, and audit access periodically.
Store secrets in protected secret mounts, never templates, URLs, metrics labels or
logs. Restrict documents and financial/customer data using existing permissions.
No production Powercode access, SMS/email transport or public incident publication
is included. Review any real customer mapping, consent/opt-outs and estimated versus
confirmed impact separately before approving a communications milestone.

Propose30days raw telemetry/90days rollups, but approve retention only after measured
disk/query sizing and legal requirements. Existing pruning jobs remain off until
bounded retention and restoration are tested. Use a nightly encrypted, writer-quiesced
backup in an approved downtime window and after approved configuration changes;
verify ciphertext hash daily and restore into an isolated environment weekly during
the pilot. Keep a separately protected off-host copy/identity; no automatic purge.

## Stop, incident response and recovery

Stop on unexpected write attempts, unknown outbound destination, credential leakage,
unbounded load/retries, repeated false alarms or degraded production device behavior.
First revoke the one-device egress permission, then stop the NOC monitoring service:
`sudo systemctl stop nwo-noc.service`. This stops NOC processes, not network equipment.
Verify no managed polling container remains running; preserve audit, queue and raw
evidence. Revoke/rotate the monitoring credential through the approved operator
process. Never reboot a radio/router, change RF settings or run remediation.

Treat suspected physical/power faults as uncertain until corroborated. Use internal
incidents and manually assigned work orders; no automatic dispatch or messages.
For application failure preserve the source/target/schema and recovery guard. Follow
[UPGRADE](../UPGRADE.md) and [BACKUP_RESTORE](../BACKUP_RESTORE.md): restore into a
new isolated database/filesystem, run matching images, verify history and approve
recovery explicitly. Never silently restore over newer data or claim irreversible
migrations can automatically roll back. A physical reboot/power-loss rehearsal,
off-host disaster recovery and a real device contract remain unverified release gates.
