#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]]; then echo "Usage: ./scripts/restore.sh backups/nwo-noc-YYYYMMDD-HHMMSS.sql.gz"; exit 1; fi
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd); cd "$ROOT"
FILE="$1"; [[ -f "$FILE" ]] || { echo "Backup not found: $FILE"; exit 1; }; gzip -t "$FILE"
read_env(){ local key="$1"; local value; value=$(grep -E "^${key}=" .env | tail -1 | cut -d= -f2- || true); value="${value%\"}"; value="${value#\"}"; printf '%s' "$value"; }
POSTGRES_USER=$(read_env POSTGRES_USER); POSTGRES_DB=$(read_env POSTGRES_DB)
TABLES=$(docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select count(*) from pg_tables where schemaname='public'" | tr -d '[:space:]')
if [[ "${TABLES:-0}" != "0" && "${FORCE_RESTORE:-}" != "YES" ]]; then
  echo "Refusing to restore into a non-empty database ($TABLES public tables)."
  echo "For a clean replacement, run: docker compose down -v && docker compose up -d db"
  echo "Then run this restore command again before starting backend."
  exit 2
fi
gunzip -c "$FILE" | docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"
echo "Restore completed from $FILE"
