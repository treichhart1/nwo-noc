#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
read_env(){ local key="$1"; local value; value=$(grep -E "^${key}=" .env | tail -1 | cut -d= -f2- || true); value="${value%\"}"; value="${value#\"}"; printf '%s' "$value"; }
POSTGRES_USER=$(read_env POSTGRES_USER)
POSTGRES_DB=$(read_env POSTGRES_DB)
[[ -n "$POSTGRES_USER" && -n "$POSTGRES_DB" ]] || { echo "POSTGRES_USER or POSTGRES_DB is missing from .env"; exit 1; }
mkdir -p backups
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="backups/nwo-noc-$STAMP.sql.gz"
docker compose exec -T db pg_dump --clean --if-exists --no-owner --no-privileges -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$OUT"
gzip -t "$OUT"
echo "Created $OUT"
