# Ubuntu24.04 operator installation and recovery guide

**Candidate for independently authorized isolated installation testing, not a production release authorization.**
Read [README](README.md), [remaining blockers](REMAINING-BLOCKERS.json) and the signed-release procedure below before invoking sudo. The AI NOC, AI Fiber Designer and MikroTik Management remain separate applications.

## 1. Size and prepare the server

Use a dedicated Ubuntu Server24.04 LTS amd64 VM/server with x86-64-v2 on **every vCPU** (SSE3/SSSE3, SSE4.1/4.2, POPCNT, CX16, LAHF). Generic QEMU qemu64 is incompatible with the locked NumPy/GIS wheels. Do not alter live hypervisor CPU settings during this assignment.

Executed NOC119 VM acceptance used **4vCPU,5120MiB RAM,32GiB root**. Durable polling measurements used4vCPU/~16GiB hosts, backend2CPU/768MiB and PostgreSQL2CPU/1GiB; these are different test environments. The installer’s2CPU/4GiB/10GiB-free floor is a rejection threshold, **not measured full-VM acceptance**.

For a small controlled installation, budget at least4 compatible vCPU,8GiB RAM and80GiB SSD as an **operator sizing recommendation with headroom**, not a tested SLA. Allocate extra space for images, monitoring history, documents, release staging and at least two complete persistent-state copies. Confirm growth/retention and off-host backups before sizing. The1,000-device15s criterion has failed intermittently; do not schedule1,000 real devices every15s based on a favorable run.

Record image checksum/signature provenance, OS/kernel, architecture, CPU flags, RAM, disk, UTC, installation duration and resource measurements. Use independently approved supported Ubuntu repositories; never enable proposed/unstable packages to force a security result.

Read-only checks:

```sh
cat /etc/os-release
uname -a
lscpu
free -h
df -h / /opt /var/lib /var/backups
timedatectl
```

## 2. Static addressing, DNS and time — operator-owned changes

Choose addresses, gateway, VLAN and DNS from the approved IP plan. Do not use example documentation addresses on the real network. Preserve the existing Netplan configuration and maintain console/out-of-band access. Validate the proposed YAML using `sudo netplan generate`, then use `sudo netplan try` with console access before accepting it. Recheck `ip -br address`, `ip route` and `resolvectl status`. Do not blindly replace an existing server’s Netplan files.

Use approved internal DNS resolvers and only the necessary egress. Configure a private NTP source through the chosen Ubuntu time service, preserving existing configuration. For systemd-timesyncd, an operator-reviewed drop-in under /etc/systemd/timesyncd.conf.d may contain:

```ini
[Time]
NTP=REPLACE_WITH_APPROVED_PRIVATE_TIME_SOURCE
FallbackNTP=
```

The placeholder is intentionally unusable. Permit only that approved destination/protocol; verify:

```sh
timedatectl show -p NTPSynchronized
timedatectl timesync-status
systemctl status systemd-timesyncd
```

Require synchronization and acceptable offset after restart/reboot and a recorded soak. NOC119’s default-deny guests reported **NTPSynchronized=no**; this is not an accepted time source. Do not open unrestricted public NTP to hide it. Choose one time daemon; do not install competing services.

## 3. VPN, MFA, SSH and firewall

Use named operator identities through an approved VPN/identity provider with MFA. Native NOC MFA is not certified by SSH-tunnel tests; VPN/IdP MFA and independent access review are required. Retain key-only SSH, restricted administrative source addresses, console recovery and a tested break-glass procedure before disabling passwords/root access. Review `sudo sshd -T`; validate changes with `sudo sshd -t` before reload. This guide does not modify a host.

Firewall acceptance must cover host INPUT/OUTPUT and Docker forwarding, including IPv6 and the actual iptables/nftables backend. Default deny; only explicit VPN/SSH administration, approved private DNS/NTP/CA, bounded update/registry bootstrap and encrypted backup destinations are candidates for exceptions. DB5432/Redis6379 and Nginx/container administrative ports must not be publicly published. Test from an independent unauthorized source and retain results.

Keep the packaged ingress on loopback18090. Use a local tunnel without a public listener:

```sh
ssh -L 18090:127.0.0.1:18090 APPROVED_NOC_SSH_HOST
```

Do not open18090 publicly, add public DNS, enable anonymous Grafana, or install the public status/signup gateways as part of core installation. Do not rely on UFW alone as proof Docker forwarding is isolated.

## 4. Obtain trusted source and images

No approved production release is published by NOC120. A separate release authority must approve source/tree, four registry digests, native risks/licenses, complete evidence and supported upgrade sources. CI local ImageIDs are not registry RepoDigests or proof of publisher identity. Use [PUBLICATION.md](PUBLICATION.md); do not clear open blockers to make preparation pass.

The host needs Docker>=24, Compose>=2.24, Python3.12, python3-yaml, age, OpenSSL and CA certificates from approved supported repositories. The installer installs missing supported dependencies while respecting compatible existing services; plan restricted bootstrap egress beforehand. Application PostgreSQL16, Redis7, backend/worker and Nginx run in containers; do not install a second competing host database. Prebuilt frontend deployment does not need host Node; release builds retain Node22.

Verify versions and daemon/storage configuration. Do not copy the CI-only controller image-store conversion onto an existing host. Do not delete images, volumes or databases to accommodate installation.

```sh
docker version
docker compose version
python3.12 --version
openssl version
age --version
```

Clone only the approved repository, checkout the approved exact40-character source commit, and verify the clean tree against the release manifest. Never run a moving development branch with sudo.

```sh
git clone https://github.com/treichhart1/nwo-noc.git nwo-noc
cd nwo-noc
git checkout --detach APPROVED_40_CHARACTER_COMMIT
git status --porcelain
git rev-parse HEAD
git rev-parse HEAD^{tree}
```

Place independently verified, root-owned0600 approval, detached signature and complete evidence envelopes in a protected directory outside the checkout. The verification key is /etc/nwo-noc-release.pub, obtained through a separate authenticated channel and matched against the authority’s independently recorded SPKI fingerprint. Never install a production private signing key on the NOC host.

## 5. Install and initialize the administrator

Stop if managed installation paths already exist; fresh install is not an upgrade. Set up off-host age recovery custody and transfer **only the public backup recipient**. Obtain private-CA certificates matching the actual local verification URL (localhost) and protect files. Do not disable TLS verification.

After independent authorization, actual script syntax is:

```sh
sudo ./install.sh --backup-recipient APPROVED_PUBLIC_AGE_RECIPIENT \
  --tls-cert /root/approved-cert.pem \
  --tls-key /root/approved-key.pem \
  --tls-ca /root/approved-ca.pem
sudo ./verify.sh
```

The installer privately prompts twice for a new16–72-byte administrator password. There is no default password, public setup endpoint or reusable setup token. Do not pass passwords in argv/environment or logs. A protected broker may use the implemented --password-stdin path. Record audit/bootstrap completion without secrets.

Production-purpose installation requires the existing signed manifest/evidence gate. An explicitly approved disposable test may use --controlled-test with a matching purpose=controlled-test manifest; **never use that switch to bypass a production security blocker**.

## 6. Verify the complete installation

```sh
sudo systemctl status nwo-noc.service nwo-noc-ingress.service
sudo journalctl -u nwo-noc.service --since today
sudo ./verify.sh
sudo ss -lntup
```

Require backend/frontend health, PostgreSQL current migration head0028_customer_status and least-privilege noc_app role, Redis and both worker health, Nginx validation, protected configuration/storage, private bindings and journald. Review verify output statuses individually: intentionally disabled integrations are NOT_CONFIGURED, not failures or live compatibility evidence. Do not publish resolved Compose configuration or runtime.env; they contain secrets.

Verify authentication, permissions and representative synthetic/read-only records through the VPN. Reboot the actual acceptance server and rerun checks, recording changed kernel boot ID and startup duration. Source and recovered VM reboots already passed in NOC119; they do not certify the new server.

All live adapters, public publishing, customer delivery, auto-remediation, live Powercode and Fiber synchronization remain disabled. The core worker and local planning worker start once; do not create duplicate schedulers.

## 7. Malware scanning and definitions

Core installation does not deploy a production ClamAV service or certify current signatures. The existing Unix-socket adapter uses MAINTENANCE_DOCUMENT_SCANNER=clamav and MAINTENANCE_CLAMAV_SOCKET; this is **not sufficient without deployment and socket access**. An operator-reviewed, least-privilege socket mount/service arrangement for both consumers and the actual host security policy is required; current packaged Compose does not automatically expose the host scanner socket. Do not silently edit generated release files or invent a supported installer option.

Deploy an approved scanner using supported signed packages, current authenticated definitions, bounded update egress and restricted Unix-socket permissions. Record engine/definition versions, signature age and update failures. Never make the socket world-writable or expose TCP3310 publicly. Repeat actual safe EICAR, clean upload, timeout, stop/restart, quarantine, denied approval and protected retrieval tests. Definition currency and deployed scanner integration remain BLOCKED until executed; NOC119’s real engine used safe test definitions only.

## 8. Backup scheduling and off-site storage

Keep the age private recovery identity off-host in independent protected custody; lost keys make backups unrecoverable. Backups include customer/configuration/encryption data. Define RPO/RTO, retention/capacity alerts, approved destination, recovery contacts and legal retention before scheduling. The existing backup requires explicit downtime/quiescence:

```sh
sudo python3 /opt/nwo-noc/current/scripts/host/manager.py backup --quiesce
sudo systemctl enable --now nwo-noc-backup.timer
sudo systemctl list-timers nwo-noc-backup.timer
```

Enable the timer only after approving its window/recipient and verifying a real restore. Copy ciphertext plus protected checksum manifest and separately retained release trust to approved encrypted off-site storage. Verify hashes after transfer; store recovery credentials and the independently trusted signing-key fingerprint separately. Do not upload backup contents/keys as public CI artifacts. Local SHA checks and pg_restore --list do not prove off-site recoverability.

## 9. Disaster recovery, upgrade and rollback

Follow [BACKUP_RESTORE.md](../../../BACKUP_RESTORE.md) and [UPGRADE.md](../../../UPGRADE.md). Use an empty isolated receiver with its own database/volumes. Verify the signed recovery transport, exact ciphertext hashes, original release trust against the independently supplied public-key fingerprint, source/schema/images and required files before mutation. Restore configuration, encryption keys, PostgreSQL, documents, monitoring history and Redis; verify original authentication, history/counts/constraints/queues and reboot.

NOC119 tests/installer/pilot_vms.py exercised two independent kernels/roots/daemons with source powered off. It is an explicitly guarded disposable GitHub CI harness, **not a production restore command**. scripts/release/restore.sh is for its generated acceptance stack, not host-package production restoration. No turnkey overwrite/cutover command is authorized. Operator recovery remains a reviewed empty-host procedure, with physical off-site failure-domain acceptance required.

For an approved supported target checkout with signed upgrade_from containing the installed commit:

```sh
sudo ./verify.sh
sudo ./upgrade.sh
sudo ./verify.sh
```

The target must retain ancestry, exact source/images, protected configuration and migration compatibility. Upgrade stages a new release, quiesces writers, verifies an encrypted backup, migrates and verifies before switching current. If migration starts and upgrade fails, retain recovery-required.json and all data; unattended startup stays blocked. Never delete the guard or promise an automatic irreversible schema downgrade. Restore the matching release/database into a new isolated environment, preserve newer records and obtain separate cutover approval.

## 10. Handoff and stop criteria

Complete [OPERATOR-CHECKLIST.md](OPERATOR-CHECKLIST.md). Stop on untrusted artifacts, bad signatures/digests, missing keys, failed verify, public exposure, unsynchronized clocks, scanner unavailability where required, incompatible firmware or unauthorized requests. Disable only the approved pilot adapter/scheduler, stop polling if necessary, close its approved egress and preserve audit/history. Do not reboot equipment, change routes/RF, send messages or publish incidents.
