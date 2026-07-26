# NWO NOC v3.1.0 — WISP Operations

A WISP-only network operations platform for Northwest Ohio Broadband.

## v3.1 capabilities

- UISP device synchronization and WISP device assignment
- Towers, POPs, sectors, licensed microwave, wireless and fiber paths
- GIS map and topology
- Operations dashboard
- Incident lifecycle: open, acknowledge, resolve and reopen
- Customer-impact tracking
- Scheduled maintenance windows
- Device telemetry ingestion and history API
- Audit logging
- PostgreSQL migrations through `0003_operations`
- Safer backup and restore scripts that refuse to import into a non-empty database

## Safe upgrade from v2.3

Keep v2.3 running until the script creates a verified backup. From the extracted v3 directory:

```bash
chmod +x scripts/*.sh
./scripts/upgrade-from-v2.3.sh ~/nwo-noc-v2.3.0-wisp-foundation
```

The script backs up v2.3, copies `.env`, stops v2.3, creates a clean v3 database, restores the backup, runs Alembic migrations, builds the containers and checks `/health`.

## Fresh installation

```bash
cp .env.example .env
nano .env
docker compose up -d --build
curl http://localhost:8000/health
```

Frontend defaults to `http://SERVER-IP:8090`.

## Database backup

```bash
./scripts/backup.sh
```

## Restore into a clean v3 database

```bash
docker compose down -v
docker compose up -d db
./scripts/restore.sh backups/nwo-noc-YYYYMMDD-HHMMSS.sql.gz
docker compose up -d --build
```

The restore script deliberately refuses to restore over existing tables.

## Telemetry API

Authenticated ingestion endpoint:

```text
POST /api/devices/{device_id}/telemetry
```

History endpoint:

```text
GET /api/devices/{device_id}/telemetry?hours=24
```

## Upgrade from v3.0

1. Back up the current database.
2. Copy your existing `.env` into this v3.1 directory.
3. Stop the old stack and build this release:

```bash
docker compose down
docker compose up -d --build
```

The backend automatically applies migration `0004_live_monitoring`. Confirm the database contains exactly one Alembic revision row:

```bash
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT * FROM alembic_version;"
```

Expected revision: `0004_live_monitoring`.

### Enabling monitoring

Open **Devices**, select a device, enable **TCP reachability monitoring**, and set a reachable management host and TCP port. Common ports include 443 for HTTPS, 80 for HTTP, 8291 for MikroTik WinBox, and 22 for SSH. The worker checks due devices every 15 seconds and honors each device's configured interval. A device is marked down after two consecutive failed checks.
