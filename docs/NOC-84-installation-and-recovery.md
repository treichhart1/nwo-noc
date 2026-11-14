# NOC-84 isolated Ubuntu 24.04 installation and release acceptance

This workflow installs the unreleased 3.2.1-rc.1 candidate with synthetic data.
It is not production deployment authorization. No live credentials, device
addresses, databases, or integration settings may be supplied.

## Dependencies and source

On a fresh disposable Ubuntu 24.04 LTS machine, clone treichhart1/nwo-noc and
check out the exact reviewed NOC-84 SHA. Run:

```bash
sudo bash scripts/release/bootstrap-ubuntu.sh
# Docker access is root-equivalent. Use a dedicated trusted installation operator.
# Either run the following as root or explicitly grant that operator Docker access.
bash scripts/release/install.sh /absolute/private/noc84-state
python3 scripts/release/access.py /absolute/private/noc84-state
```

Bootstrap installs ca-certificates, Git, Python 3, Docker Engine and Compose v2
from Ubuntu's repositories. Application dependencies are installed in the
Python/Node image builds, not globally. Downloads require approved repository
access during bootstrap/build only. For TLS inspection, set NOC_BUILD_CA to a
trusted PEM bundle; TLS verification remains enabled. For offline hosts, transfer
reviewed images with docker save/load and use NOC_SKIP_BUILD=1. Record image IDs
and `git rev-parse HEAD`; upstream base image tags and transitive Python packages
remain mutable, so archive the tested images for byte-identical recovery.

Install creates a mode-0700 state directory and mode-0600 acceptance.env with
new random credentials; it never overwrites an existing file. The generated
file is the only environment source. deploy/acceptance.env.example documents
its format. Configuration validation rejects production mode, remote database
hosts, enabled collection/pruning, integration credentials and loose permissions.
Never source or copy a production .env. Never commit the generated state.

Compose starts PostgreSQL 16, Redis 7, a one-shot Alembic migration, FastAPI,
worker and nginx/React. The application waits for a successful migration and
healthy dependencies. The installer waits for all five persistent services and
runs Alembic drift and worker checks. Volumes are project-scoped and retained.
Re-running installation is safe with unchanged state; do not replace secrets
while reusing an initialized database volume.

## Runtime network and browser access

All services have exactly one internal-only Docker network, no published ports,
no host networking, no Docker socket mounts and no external integration settings.
The Docker daemon and package installer use network access during image builds;
application services do not need public internet. Docker internal networks may
not publish loopback ports on some daemons, so `access.py` provides an explicit
loopback-only operator HTTP bridge through docker exec, without adding a routable
network to any application container. It is a development/rehearsal utility, not
a production reverse proxy; keep it foreground, trusted, and bound to 127.0.0.1.
Close it with Ctrl-C. Authentication still applies. Do not expose it publicly.

Open http://127.0.0.1:18090 locally; read the generated username/password from the
protected file using your local editor. External map tiles are disabled and
Leaflet marker assets are bundled locally. Offline monitoring permits only the
synthetic Redis service on port 6379. UISP sync is denied in offline mode.
The acceptance check probes only reserved TEST-NET destinations, never production
addresses. Those denied probes plus internal-network inspection establish the
bounded test evidence, not a certification of every host firewall or route.

## Acceptance and normal operations

```bash
# With access.py running in another terminal:
export NOC_ACCEPTANCE_STATE=/absolute/private/noc84-state
python3 tests/release/test_acceptance.py
cd frontend
npm ci
npx playwright install --with-deps chromium
npx playwright test
# Existing system Chromium may be selected with NOC_CHROMIUM_PATH.
```

Runtime tests deliberately create synthetic inventory and incidents, stop Redis,
and restart backend/frontend/worker. Run only against a fresh disposable project;
each run uses new backup paths and restore targets, preserving prior recovery
evidence. For a fully clean run use a new NOC_PROJECT and state directory. Stop the first access bridge before
starting another project on the same loopback port.

```bash
scripts/release/manage.sh "$NOC_ACCEPTANCE_STATE" ps
scripts/release/manage.sh "$NOC_ACCEPTANCE_STATE" logs --tail 100
scripts/release/manage.sh "$NOC_ACCEPTANCE_STATE" stop
scripts/release/manage.sh "$NOC_ACCEPTANCE_STATE" up -d --wait
scripts/release/manage.sh "$NOC_ACCEPTANCE_STATE" down
```

`down` retains volumes. Do not use `down -v` on an installation containing data.
State is operator-owned, database files are managed by PostgreSQL in Docker
volumes. Do not manually chmod database directories. Protect logs and backups.

## Backup, restore, upgrade and rollback

```bash
scripts/release/backup.sh "$NOC_ACCEPTANCE_STATE" /private/backup/noc84.dump
scripts/release/restore.sh "$NOC_ACCEPTANCE_STATE" /private/backup/noc84.dump restore_rehearsal
```

Backup uses PostgreSQL custom format, validates its archive directory, produces
a checksum, and refuses to replace an existing backup. Restore verifies the
checksum and archive, creates a new restore_* database, and uses exit-on-error.
It refuses the source name and never drops an existing target. A failed restore
is retained for diagnosis; retry under a new name. The application continues
using nwo_noc; restore does not silently switch it. Check restored Alembic revision,
row counts and representative synthetic records before any application switch.

For an upgrade: stop worker/writers, record old SHA/image IDs/migration revision,
back up the database, and separately encrypt the protected environment/configuration
and Compose source with access restricted to recovery operators. Test both backup
and configuration recovery off-host. Build/load candidate images, run migrations
with only db/redis active, then start services and run acceptance on disposable
cloned data. The NOC-84 milestone added no migration; the consolidated NOC-93 candidate requires migrations through `0011_monitoring_reliability`. Follow the current [NOC-93 release guide](NOC-93-release-readiness.md), not historical milestone revision assumptions. Never roll an old application
against an unknown newer schema. For rollback retain old volumes, restore the
matching pre-upgrade backup into a separate project, select the old SHA/images,
validate migration revision and application health, then request separate approval
for any traffic change. Never overwrite production data for a rehearsal.

## Troubleshooting

- Unsupported OS: use a fresh Ubuntu 24.04 host or the explicit Ubuntu container
  rehearsal script; do not label Debian host results Ubuntu host acceptance.
- Docker socket denied: use the trusted operator configured for the local daemon;
  do not chmod the socket world-writable. Never select a remote daemon.
- Build failure: inspect dependency repository/proxy/CA access; do not disable TLS.
- Configuration rejected: fix the named nonsecret field or permissions. Do not
  replace initialized database passwords with newly generated credentials.
- Migration failed: backend stays blocked. Inspect migrate logs; retain volumes,
  back up before repair, and rerun after fixing the cause.
- Worker unhealthy: inspect worker logs and Redis health; heartbeat expires in
  60 seconds. Restoring Redis allows the loop to recover without a host reboot.
- HTTP 503 from access.py: inspect backend/frontend health and the operator's
  Docker access. /health is liveness; migrations, DB, Redis and worker checks
  are separate acceptance gates.
- Backup/restore failed: check free disk, checksum, target name and permissions;
  do not bypass the refusal to overwrite an existing target.

## Release checklist and upstream dependencies

- [x] NOC-83 base 2f3f5edff1925104327d1f2c06f5504bf8398ae2; CI #424 / run
  43356265848 confirmed success through GitHub before NOC-84 began.
- [x] Full NOC-83 ancestor chain preserved; draft NOC-84 targets its branch.
- [ ] Review/integrate #87, #86 and earlier stacked PRs in dependency order, resolve
  NOC-82's stale release status/ancestry, then rerun on final consolidated SHA.
- [ ] Confirm all NOC-84 CI jobs at exact final head, including runtime and browser.
- [ ] Retain Ubuntu userspace installation, service health, API/auth/RF, restart,
  denied-egress, database recovery and browser evidence from an isolated run.
- [ ] Separate real Ubuntu VM boot/systemd/firewall/reboot rehearsal (containers
  share the cloud host kernel and Docker daemon; no bare-server equivalence claim).
- [ ] Archive reviewed images and dependency manifests for immutable recovery.
- [ ] Independent production design review: standard docker-compose.yml is not
  an enforced egress policy. Keep integration disabled until separately approved.

Security changes: mutations now require admin/operator; RF reads retain the
explicit role allowlist; RF simulation has no execution route. Browser logout
clears the local token but does not revoke already issued JWTs; short-lived tokens
or server-side revocation remain a future security enhancement. No production,
AI Fiber Designer or MikroTik Management files are touched.

## Recorded cloud evidence

The Ubuntu 24.04 container bootstrap installed actual Ubuntu packages and ran the
installer against the local cloud Docker socket; all five persistent services
became healthy. Reinstallation reused state and migrations successfully. The
application containers use their Python/nginx/PostgreSQL/Redis base distributions,
while the installer userspace is Ubuntu 24.04. This is not a VM boot test.

The full backend suite passed **257 tests, 0 skipped** against a fresh disposable
PostgreSQL 16 database. The initial runtime suite passed **10 tests**, including
Redis failure/recovery, restarts, denied TEST-NET egress, role denial, RF safety
and isolated backup restoration. Final runtime/browser and GitHub CI outcomes
are recorded in the draft PR after execution; no pending check is implied passed.
Existing dependency deprecations: anyio BlockingPortal alias and passlib crypt.

NOC-83 contains NOC-82's audit replayed as ad61cde on RF-81 c7e7edd; the original
#86 SHA 34e54e2 is therefore a logical dependency rather than a direct ancestor.
NOC-84 directly descends from 2f3f5ed, preserving that source and review chain.
Existing PRs and their branch refs remain untouched.
