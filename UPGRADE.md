# Safe approved upgrades

Obtain an approved clean target checkout and root-owned `/etc/nwo-noc-release.json`.
Its `upgrade_from` list must explicitly include the installed commit, and the target must
preserve that commit's ancestry. All four approved runtime image roles must match the signed source/image contract; backend/frontend/PostgreSQL revision labels and the exact approved Redis digest are verified.
No automatic branch pull, registry tag following or source-code editing is performed.

Configure and externally validate encrypted backups first; see BACKUP_RESTORE.md.
Plan a maintenance window: applications/worker/frontend are stopped for consistent
backups and migration. Upgrade requires available disk headroom and a configured age
public recipient. It refuses a prior incomplete install/recovery guard.

```sh
cd <clean-approved-target-checkout>
sudo ./verify.sh
sudo ./upgrade.sh
sudo ./verify.sh
```

The implementation validates source, compatibility, image/configuration/migration heads
and Nginx before modifying the database. It stages code in a new release directory,
quiesces application writers, creates/checks an encrypted full-state backup, applies
migrations using a separate non-superuser owner, grants restricted runtime privileges,
starts/verifies the candidate, then atomically changes `current` and records history.
The same target returns `already_current`; it is not re-migrated or reseeded. Existing
release directories are never overwritten silently.

If an upgrade fails **before migration**, data and the old code pointer remain intact.
Services may be stopped; inspect the journal and verify the original release before an
operator restarts it. If failure occurs **after migration has started**, the persistent
`recovery-required.json` blocks unattended systemd startup, services remain stopped, and
no database downgrade, restore or automatic old-application startup occurs. Host-package containers use bounded on-failure restart rather than daemon-boot auto-start; the guarded systemd unit owns boot startup. A migration
exception does not prove the entire multi-revision upgrade is safely reversible.

Do not delete the recovery guard to force an old application onto an unreviewed schema.
Follow TROUBLESHOOTING.md: identify the exact source/target/schema, preserve all writes
and encrypted backups, restore into a new isolated database, run the matching old image
and verify restored data before a separately authorized recovery decision. Never restore
over newer production data. Archive failed releases for diagnosis rather than deleting
history. Unit/protocol changes require an explicit compatibility review; do not blindly
replace local unit customizations.

## NOC116 signed release prerequisite

Production-purpose install/upgrade now fails closed unless the exact approved JSON
has a detached Ed25519 signature at <approval-path>.sig and an independently trusted
root-owned0600 public key at /etc/nwo-noc-release.pub. No private signing key belongs
on the NOC host. /usr/bin/openssl must be available before release verification. See
[trusted-artifact procedure](docs/release/NOC-116/trusted-artifacts.md). Explicit
controlled-test approval remains isolated and does not authorize a release/live pilot.

## NOC117 PostgreSQL image prerequisite

New production-purpose approvals must also include the hardened PostgreSQL registry
digest and matching NOC source-revision label. Do not reuse a two-image NOC116 approval
or silently substitute the upstream base. Sign a reviewed four-role target manifest (backend/frontend/PostgreSQL/Redis),
retain supported upgrade_from ancestry and test encrypted backup/restore first. See
[container security report](docs/release/NOC-117/README.md). This is not production
authorization; all unresolved native/npm findings and operator gates remain.

## NOC120 Redis artifact requirement

New-source installation/upgrade requires the supported-library-patched Redis7.4.12 derivative from deploy/Dockerfile.redis, not the unchanged upstream base. The upstream source digest remains pinned as build material; the approved runtime digest is a separately reviewed immutable derivative. Require matching NOC source revision, original base identity, Redis7.4.12 binary, UID999/GID1000 and the fail-closed runtime-library probe. Four-role approvals and exact-image scans must be regenerated/reviewed and signed independently; do not relabel an old signature or use a mutable tag. Actual queue/AOF backup/restore and second-host recovery must pass on the derivative. Redis licensing remains an independent gate.
