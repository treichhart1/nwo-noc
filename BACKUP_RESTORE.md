# Encrypted backup and recovery

Install/configure an age X25519 public recipient. Keep the matching private identity
**off the NOC host**, encrypted under approved key custody; losing it makes backups
unrecoverable. Never use a sample/demo key. Backups include sensitive customer/config
material and may not be uploaded as public CI artifacts.

```sh
# On a separate secure operator machine:
age-keygen -o protected-recovery-identity.txt
age-keygen -y protected-recovery-identity.txt > public-backup-recipient.txt
# Transfer only the PUBLIC recipient to the approved NOC host:
sudo install -o root -g root -m 600 public-backup-recipient.txt /etc/nwo-noc/backup-recipient
sudo python3 /opt/nwo-noc/current/scripts/host/manager.py backup --quiesce
```

`--quiesce` explicitly acknowledges service downtime. The backup stops app writers,
saves/stops Redis, validates a custom-format PostgreSQL dump with pg_restore --list,
archives configuration/encryption keys, exact source release, application data and durable
Redis state, then streams it through age. Plaintext staging is0700/0600 and removed only
from this operation's own temporary directory after completion/failure. The encrypted
archive/checksum manifest is0600; failed ciphertext is retained for diagnosis and never
qualifies as a successful upgrade backup. No source database or application data is deleted.

Upgrade uses this same backup path and blocks without a recipient, valid dump, successful
encryption and matching ciphertext SHA256. These checks do **not** verify possession of
the private identity or an off-host restoration. Copy ciphertext/manifest to approved
access-controlled off-host storage, verify hashes and perform isolated restoration tests.
No automatic backup deletion/retention purge is installed; capacity and retention ownership
must be configured by the operator. Local disk-only copies are not disaster recovery.

For optional scheduled backups, first review the downtime window, restore evidence and
recipient, then explicitly enable the installed, initially disabled timer:

```sh
sudo systemctl enable --now nwo-noc-backup.timer
sudo systemctl list-timers nwo-noc-backup.timer
```

## Isolated restore

1. Verify the encrypted file against its protected manifest's SHA256.
2. Decrypt using the off-host identity into a fresh private recovery directory on an
   isolated recovery host. Check archive names/types before extraction; reject absolute,
   traversal, device and unexpected link entries. Never extract over the live filesystem.
3. Inspect `release/release.json` and `installation.json`. Obtain the matching approved
   images/source and verify the archived schema/application compatibility.
4. Create a **new** PostgreSQL database named `restore_<unique>` in an isolated cluster.
   Validate `database.dump` with pg_restore --list, then restore with --exit-on-error,
   --no-owner and --no-privileges. Never use --clean, drop an existing DB, or target nwo_noc.
5. Reapply the matching documented migration/runtime-role permissions to the new target,
   mount recovered files/keys in a separate internal-only stack, and run verification plus
   representative synthetic/read-only record checks with the matching application version.
6. Record checksums, counts, history, key availability and recovery timings. Keep the source
   and failed candidate untouched. A live recovery cutover requires a separate operator plan.

The existing isolated `scripts/release/restore.sh` remains appropriate for its generated
acceptance stack; it deliberately cannot restore a host package over an existing database.
Do not point it at production or substitute acceptance credentials into a host package.
