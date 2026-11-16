# Controlled installation operator runbook

**NO-GO for production/live pilot.** These commands are for a dedicated disposable,
isolated Ubuntu24.04 amd64 acceptance server after an independent test approval.
They match the root scripts exercised by hosted VM acceptance. This document does
not approve its own source, publish artifacts, activate integrations or configure a
production server. Keep AI Fiber Designer and MikroTik Management separate.

## 1. Server and release prerequisites

Use2+CPU cores,4+GiB RAM and10+GiB free disk plus measured database/image/backup
headroom; this is a test floor, not a production sizing guarantee. Require Ubuntu
Server24.04LTS amd64, root/sudo, official signed noble repositories, Python3.12,
supported Docker>=24/Compose>=2.24, age and python3-yaml. The installer provisions
supported missing packages while respecting compatible services. It deploys the
complete Python backend/PostgreSQL16/Redis7/Nginx in immutable containers; Node22
is a release-build dependency, not a host runtime dependency for prebuilt frontend.

No other NOC installation may occupy /opt/nwo-noc, /etc/nwo-noc, /var/lib/nwo-noc,
/var/log/nwo-noc or /var/backups/nwo-noc. Do not delete those paths to pass preflight.
Resolve UID/GID10001 or unit-name conflicts independently. Never run this against
an existing or production installation. Keep the server isolated from equipment,
Powercode, notification providers and production databases.

Obtain an independently approved exact source commit/tree and complete registry
images pinned by digest with matching OCI revision labels. Check publisher trust,
signature/attestation, licensing and image scan evidence independently. No such
production release publication is supplied by this draft. Choose the final approved
commit, not an arbitrary branch or a manifest copied without review:

```sh
git clone https://github.com/treichhart1/nwo-noc.git
cd nwo-noc
git checkout --detach <independently-approved-40-character-commit>
git status --porcelain
git rev-parse HEAD HEAD^{tree}
python3 scripts/release/release_acceptance.py --candidate HEAD > /tmp/noc-release-inventory.json
```

The status must be empty and inventory must match the approved release. A full-history
clone is required. The inventory does not grant approval. All draft dependencies must
be preserved; merging the PRs is not a prerequisite for this controlled checkout and
is not authorized. Never sudo arbitrary unapproved development code.

## 2. Configuration and backup preparation

Use the [inert configuration review worksheet](../../../deploy/host/installation-review.example.json).
It is a human checklist, **not** an installer input, runtime environment or approval.
The actual privileged approval contract is
[approved-release.example.json](../../../deploy/host/approved-release.example.json).
The release authority replaces every placeholder, specifies purpose, commit/tree,
complete immutable images, schema0028_customer_status and supported upgrade sources,
then supplies a reviewed root-owned0600 file outside the checkout:

```sh
sudo install -m 0600 /root/reviewed-noc-release.json /etc/nwo-noc-release.json
```

Do not treat copying the example as approval. Default purpose release requires
registry digests; --controlled-test accepts only explicitly matching test approvals
and immutable local image IDs. The CI harness uses that explicit test path; it does
not publish/approve production images. Existing installation scripts refuse dirty,
unapproved, mismatched or mutable sources.

Generate/hold the age private recovery identity on a separate trusted recovery host.
Supply only its public age recipient to installation; prove off-host key custody and
decryption before relying on backups. Do not print or copy private identities into
the NOC environment. Supply protected root-owned TLS cert/key/CA files. The package's
loopback verification URL requires localhost SAN; an external VPN/QR hostname needs
a separately reviewed certificate/proxy design. No public ACME/DNS/firewall opening.

| Component | Actual safe configuration source / default |
|---|---|
| PostgreSQL | Installer-generated internal noc_app runtime URL; separate noc_owner migration and bootstrap credentials; no host port |
| Secrets | Generated runtime.env and private read-only key mounts, never worksheet/repository plaintext |
| Nginx | deploy/host/nginx.conf.template rendered for localhost/Unix socket; frontend image, no unrelated native sites overwritten |
| Systemd | deploy/host/nwo-noc.service, ingress.service, backup.service/timer; dedicated nologin user, no Docker group |
| Workers | Existing core/planning worker once; maintenance owned by core; other network schedules off |
| Monitoring adapters | Existing Settings + deploy/pilot templates; UISP/MikroTik/RF/SNMP/admin approval flags disabled, OFFLINE_MODE=true |
| Powercode | Existing customer foundation; live/fixture flags off; no account/billing writes |
| Grafana | Existing private integration/provisioning; live health/embedding off; no anonymous dashboards |
| Fiber Designer | Existing versioned approved-data contract; ingest/worker off; separate engineering authority |
| SMS | Existing approval/outbox foundation; live delivery/worker/auto-drafts off; real provider not enabled |
| Public status/signup | Separate application/gateway concepts; not installed/exposed by host package; publication never automatic |
| VPN access | Host ingress hardcoded127.0.0.1; approved SSH/VPN tunnel, no public dashboard |
| Backups | /var/backups/nwo-noc local encrypted0600; off-host destination/custody requires separate operator setup |

Do not copy a generic .env over generated host settings or print resolved Compose
configuration. Configuring a token alone does not authorize any network connection.
The offline host package has no reviewed live-integration activation procedure;
do not disable verification, change OFFLINE_MODE or add an unrestricted network.

## 3. Installation and administrator

From the clean approved checkout on the disposable server:

```sh
sudo ./install.sh --backup-recipient <approved-age-public-recipient> --tls-cert /root/noc-cert.pem --tls-key /root/noc-key.pem --tls-ca /root/noc-ca.pem
sudo ./verify.sh
```

Enter the new16–72-byte administrator password through the hidden prompt; no default
password exists. Protected broker stdin via --password-stdin is an explicit automation
option; never pass the password in argv, environment, shell history or tickets.
The one-time transactional helper creates an audit record and refuses another admin
bootstrap. No setup token exists. The generated ADMIN_PASSWORD compatibility secret
is not the operator's login password and cannot reset it.

The installer validates dependencies/OS/resources/source/images/configuration before
initializing a dedicated database and all28 migrations. It starts core/planning
workers and Nginx, registers systemd, and verifies the whole package. It does not seed
demonstration inventory. An existing or partial installation is refused and preserved.
Do not rerun fresh install to perform an upgrade or erase failed state.

## 4. Verification, private access and service health

```sh
sudo ./verify.sh
sudo systemctl status nwo-noc.service nwo-noc-ingress.service
sudo systemctl list-timers nwo-noc-backup.timer
sudo journalctl -u nwo-noc.service -u nwo-noc-ingress.service --since today
```

Require verification exit0 and no FAILED checks. PASSED is check-specific; intentional
optional integrations report NOT_CONFIGURED. A container rehearsal warns that systemd/
boot is not covered; do not turn that warning into PASS. Review migration head, runtime
DB privileges, six healthy services, bounded logs, protected storage and no published
Docker ports. Never paste secret-bearing docker inspect/environment/config into reports.

From an authorized workstation, use a private tunnel (replace the host alias):

```sh
ssh -L 18090:127.0.0.1:18090 <approved-test-host>
```

Open https://localhost:18090 with the approved CA trust, authenticate and verify logout.
Do not bypass TLS warnings with insecure flags or bind the tunnel to0.0.0.0. No public
DNS or firewall rule is required. Inspect the complete dashboard/module navigation,
empty inventory and explicit unknown/missing metrics before importing synthetic data.
Use existing synthetic suites, not live devices or production Powercode, for evaluation.

Managed restart and emergency disable:

```sh
sudo systemctl restart nwo-noc.service
sudo ./verify.sh
sudo systemctl stop nwo-noc.service
```

The stop command affects NOC services, not equipment. Preserve data, guards and logs.
Do not independently start duplicate workers or an acceptance-mode poller beside the
managed services. Start only a verified ready installation after reviewing why it stopped.

## 5. Backup, upgrade and recovery

Review a downtime window and recipient before a writer-quiesced backup:

```sh
sudo python3 /opt/nwo-noc/current/scripts/host/manager.py backup --quiesce
```

Verify protected ciphertext SHA256/manifest and pg_restore listing; retain configuration,
keys, documents, Redis data and exact release along with SQL. Test decryption/restoration
with an off-host identity into a new isolated target. Backups are sensitive even when
encrypted; protect destinations/retention and never serve them through Nginx. The timer
is installed disabled; enable it only after operator review of downtime and restore proof.

For an approved compatible target, obtain a clean full checkout/new approval with
installed commit explicitly listed in upgrade_from and target ancestry preserved:

```sh
cd <clean-approved-target-checkout>
sudo ./verify.sh
sudo ./upgrade.sh
sudo ./verify.sh
```

No automatic git pull/tag following occurs. Upgrade verifies the current installation,
configuration/images/schema/headroom, creates a checked encrypted backup, stages code,
quiesces writers, runs migrations, verifies candidate services and atomically switches
the release. Historical data and configuration are preserved.

If migration has started and a later step fails, preserve recovery-required.json and
both releases/backups. Automatic old-code startup and database restore are blocked.
Do not delete the guard, downgrade populated migrations or restore over newer data.
Identify source/target/schema/writes, restore into a **new isolated database/filesystem**
with matching images/keys, verify history and obtain a separately authorized recovery
decision. See [BACKUP_RESTORE](../../../BACKUP_RESTORE.md), [UPGRADE](../../../UPGRADE.md)
and [TROUBLESHOOTING](../../../TROUBLESHOOTING.md). A pre-migration failure may leave
services stopped; verify the original ready state before an operator restarts it.

## 6. Remaining manual acceptance procedure

This is **not executed** by NOC115 unless final evidence explicitly says otherwise:

1. Provision a new disposable Ubuntu Server24.04 amd64 VM from the chosen ISO/cloud
   image with no Docker/Compose installed, sufficient resources and no production routes.
   Record exact image/hash, kernel, CPU/RAM/disk, repository provenance and existing units.
2. Supply independently approved source/images, controlled-test credentials/approval,
   public backup recipient and private TLS as above. Run actual install.sh and verify.sh;
   record exit codes, package installation, source tree, image digests, schema and timings.
3. Reboot that disposable VM through the hypervisor. Reconnect privately; verify real
   systemd startup, six healthy services, private listener/no host DB ports, logs, persisted
   documents/history and one administrator. Repeat a controlled hypervisor power-loss
   test only after backups; do not reboot network equipment or a production host.
4. Validate approved firewall/VPN/MFA/egress rules from independent private/public test
   vantage points. Confirm no public dashboard/DB/Redis or unexpected destinations. Do
   not scan a production network or connect any test machine to equipment without approval.
5. Copy encrypted backups and approved matching images/source to a **second isolated host**,
   obtain separately held identity, verify hash/decrypt, inspect archive safety and restore
   SQL/configuration/keys/files into new targets. Start matching application, compare
   history/checksums/ownership and test representative reads. Leave original DB untouched.
6. Record OS/native-image CVE findings, Redis license approval, malware scanner operation,
   key custody, recovery timings and sustained synthetic load/retention sizing. Keep each
   not-executed or blocked criterion explicit; an operator signs the evidence review.

Only then reconsider the separate [NOC114 pilot review](../../NOC-114-controlled-pilot.md).
Start no live pilot here. One-device approval, firmware/least-privilege credential,
exact endpoint/TLS/egress, rate limits, alert policy and emergency stop require separate
review. Keep customer mapping synthetic and notifications/publication disabled. There
is no permission to change router/radio/OLT settings or automatically remediate alarms.
