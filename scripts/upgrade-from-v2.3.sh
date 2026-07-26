#!/usr/bin/env bash
set -euo pipefail
SOURCE_DIR=${1:-$HOME/nwo-noc-v2.3.0-wisp-foundation}
TARGET_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
[[ -d "$SOURCE_DIR" ]] || { echo "Source directory not found: $SOURCE_DIR"; exit 1; }
[[ -f "$SOURCE_DIR/.env" ]] || { echo "Source .env not found"; exit 1; }
cd "$SOURCE_DIR"
./scripts/backup.sh
BACKUP=$(find backups -maxdepth 1 -name 'nwo-noc-*.sql.gz' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)
[[ -n "$BACKUP" ]] || { echo "Backup was not created"; exit 1; }
cp .env "$TARGET_DIR/.env"
mkdir -p "$TARGET_DIR/backups"
cp "$BACKUP" "$TARGET_DIR/backups/"
TARGET_BACKUP="$TARGET_DIR/backups/$(basename "$BACKUP")"
docker compose down
cd "$TARGET_DIR"
docker compose down -v --remove-orphans || true
docker compose up -d db
for i in $(seq 1 30); do docker compose exec -T db pg_isready >/dev/null 2>&1 && break; sleep 2; done
./scripts/restore.sh "$TARGET_BACKUP"
docker compose up -d --build
curl --fail --retry 20 --retry-delay 3 http://localhost:${BACKEND_PORT:-8000}/health
echo
echo "Upgrade complete. Backup retained at $TARGET_BACKUP"
