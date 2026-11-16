# Packaging troubleshooting and remaining gates

- **Approval rejected:** verify root ownership0600/non-symlink file, protected parent,
  exact commit/tree, repository, approval reference/purpose and immutable image digests.
  Do not weaken checks or approve an arbitrary development branch. Default installation
  cannot use local test image IDs. Obtain signed/approved artifacts through the release owner.
- **Existing installation/path:** stop. Fresh installation is not a resume/upgrade command.
  Preserve every directory, database, volume, secret, document and backup. Use verification,
  inspect sanitized installation events and follow an operator-reviewed recovery plan.
- **OS/resources/dependencies:** only Ubuntu24.04 amd64 is accepted. Correct disk/RAM/CPU
  shortages or official package repositories. Existing incompatible Docker/services/accounts
  are not overwritten. UID10001 is reserved for this package; resolve conflicts explicitly.
- **Private port busy:** choose a free unprivileged loopback port with `--port` during fresh
  installation. Never bind0.0.0.0 as a workaround. No public firewall/DNS changes are needed.
- **Failed initial installation:** inspect `/var/lib/nwo-noc/installation.json` and protected
  `/var/log/nwo-noc/installer.jsonl`; retain the staged approved release and database. Do not
  remove managed paths merely to rerun install. If an administrator was already created,
  the one-time helper refuses another setup. Recover the existing account through a separately
  approved privileged procedure rather than recreating/defaulting credentials. There is no
  public setup/reset endpoint, password printed in logs or automatic destructive reset.
- **Service failure:** `systemctl status nwo-noc`, `journalctl -u nwo-noc --since today`,
  `sudo ./verify.sh`. Use the stored private Compose project for scoped service logs only.
  Do not dump secret-bearing environment/configuration into tickets.
- **Migration or candidate startup failure:** a recovery guard blocks unattended startup
  after migration starts. Retain it, both releases and all backups; determine actual schema
  state and writes. Restore to a new isolated database with the matching application/image,
  compare data and document a recovery decision. Never automatically downgrade migrations,
  clear the guard blindly, overwrite a current database or silently start an old app.
- **Backups unavailable:** configure a valid public age recipient and prove off-host identity
  custody/decryption/restore. No private key is installed on the NOC. Partial ciphertext is
  not a successful backup. Ensure sufficient disk; no automatic purge deletes old backups.
- **Optional integrations not configured:** intentional, not an installation failure. Do not
  connect to production to make a test green. Respect separate device/provider approvals.
- **Container rehearsal warning:** actual Ubuntu userspace may be verified, but systemd,
  full host boot and host firewall cannot be certified from that container. Disposable-host
  Actions adds actual systemd checks; it still does not simulate power loss or certify a
  minimal fresh server's complete reboot behavior without running those tests.

Remaining production gates include NOC111/112 high operational findings: OS/native image
vulnerability assessment, Redis7.4 server licensing, trusted artifact publication/signing,
full host boot/firewall/VPN/MFA acceptance, deployed document scanning, off-host recovery
keys/files and actual vendor firmware/contract validation. Native1,000-device RF polling
exceeds15seconds; collection intervals/headroom and sustained performance require sizing.
The package does not make unsupported adapters real, approve a pilot, deploy to production,
modify equipment, send messages or publish incidents. RF remediation remains simulation-only.
