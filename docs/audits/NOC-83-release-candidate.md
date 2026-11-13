# NOC-83 Ubuntu 24.04 release candidate

Candidate version: **3.2.1-rc.1**, unreleased; no tag or release published.
Scope is synthetic local validation only. No production, network equipment,
UISP synchronization, deployment, or live self-healing is authorized here.
RF self-healing remains read-only and simulation-only.

## Installation rehearsal (pending on a disposable Ubuntu 24.04 host)

1. Use a new isolated VM with no routes or credentials to production/equipment.
   Install Docker Engine and Compose v2 from Docker's official Ubuntu instructions,
   Git, Python 3.12 and Node 22. Record OS, Docker/Compose versions and architecture.
2. Clone only `treichhart1/nwo-noc`. Check out the exact reviewed PR head SHA;
   record `git rev-parse HEAD`. Do not use a moving branch for promotion.
3. Build `docker compose build`. Use the committed npm lockfile. In an HTTPS
   inspection environment the Dockerfiles accept optional BuildKit `proxy_ca`
   secrets; never disable certificate verification or bake credentials into images.
4. Create a new `.env` from `.env.example` with mode 0600, unique disposable
   database/admin passwords and a random SECRET_KEY. Set APP_ENV=staging,
   BIND_ADDRESS=127.0.0.1, API_DOCS_ENABLED=false, UISP_BASE_URL and UISP_TOKEN
   empty, RF_COLLECTION_ENABLED=false, RF_HISTORY_PRUNING_ENABLED=false,
   UISP_VERIFY_TLS=true. Never copy a live .env or production database.
5. Before starting any worker, enforce host/container egress isolation: deny all
   equipment/production routes and external traffic. Static validators do not
   prove firewall enforcement. Follow docs/noc-75y-staging-network-isolation-test-plan.md.
6. Render `docker compose config --format json` to a protected scratch file.
   Run scripts/validate_compose_network.py, validate_staging_egress_policy.py
   and validate_staging_isolation.py as documented in CI. Run backend/scripts/
   rf_staging_preflight.py with the same environment; its unsafe controls must fail.
7. Only during a separately authorized rehearsal, use a unique COMPOSE_PROJECT_NAME,
   start db/redis, run `docker compose run --rm --no-deps backend alembic upgrade head`,
   then `alembic check` and repeat upgrade. Record revision 0006_rf_query_index.
   Seed only synthetic users/devices. Start backend/frontend; start the worker only
   after runtime isolation has been verified. Check service health, login, role
   denial, dashboard/RF freshness, frontend assets and /health version 3.2.1-rc.1.
8. Run complete pytest with RF_INTEGRATION_TEST_DATABASE_URL explicitly pointing
   to a separate disposable database named nwo_noc; tests write synthetic data.
   Run npm ci and npm run build. Retain evidence at the exact SHA.

## Rollback and recovery rehearsal

Before any future upgrade, record the old source SHA, immutable image digests,
Compose configuration and migration revision. Securely back up .env separately.
Stop writers/worker before the consistent database backup; use pg_dump and test
restoration into a *new*, isolated PostgreSQL database with ON_ERROR_STOP.
Record table counts and a synthetic sentinel row before/after restoration.

For recovery, stop candidate writers, retain its volumes and logs, and create a
separate Compose project/database volume. Restore the pre-upgrade backup there;
select the old reviewed SHA/images and configuration. Verify schema, login,
version, representative historical data and health before any authorized traffic
switch. Do not run `docker compose down -v` on a retained installation. A previous
application may be incompatible with a newer schema; prefer restoring the matching
backup rather than blindly downgrading. This candidate adds no schema migration.
A disposable 0006 → 0005 → 0006 round trip was verified; it is not proof of all
historical downgrade paths or a production rollback rehearsal.

## Evidence and remaining gates

- Source review: #85 CI #422 passed; #86 CI #420 backend assertion failed and
  caused dependent acceptance failure. See NOC-83-stack.md for SHAs and ancestry.
- Local Python 3.12: complete PostgreSQL-backed suite passed (243 tests); no skips. Migration upgrade/check/repeat passed.
- Backend and frontend Docker image builds passed with TLS verification enabled.
- Frontend TypeScript/Vite build passed. No dedicated frontend unit-test command
  exists; build success does not establish interactive browser acceptance.
- Compose exposure/static isolation and egress manifest validators passed; RF
  safe preflight passed and collection-on, TLS-off and production controls failed.
- Synthetic benchmark: 250 radios ×20 samples median 5.5ms; 1000×20 median 8.3ms
  (3 repeats, informational, environment-specific).
- Disposable pg_dump restored successfully to a new database at migration head;
  single migration downgrade/re-upgrade passed. No live data was used.
- Local host is Debian 13, not Ubuntu 24.04. CI runners are now explicitly pinned
  to Ubuntu 24.04; obtain green checks at the final PR SHA before RC acceptance.
- Full isolated Ubuntu installation, five-service runtime health, interactive
  browser acceptance, enforced egress verification, and representative data
  recovery remain pending. No application stack was deployed during this task.
- Upstream stacked PR review/integration remains required; no production approval.
