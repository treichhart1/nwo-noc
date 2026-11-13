#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

echo "[1/4] Checking required files"
test -f "$ROOT/.env.example"
test -f "$ROOT/docker-compose.yml"
test -f "$ROOT/frontend/src/types/assets.d.ts"

echo "[2/4] Checking Python syntax"
python3 -m compileall -q "$ROOT/backend/app" "$ROOT/backend/alembic"

echo "[3/4] Checking frontend types and production build"
(cd "$ROOT/frontend" && npm run build)

echo "[4/4] Checking Docker Compose syntax when Docker is available"
if command -v docker >/dev/null 2>&1; then
  (cd "$ROOT" && docker compose config >/dev/null)
else
  echo "Docker not installed; run 'docker compose config' on the deployment server"
fi

echo "Validation completed"
