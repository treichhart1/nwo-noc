#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

echo "[1/4] Checking required files"
test -f "$ROOT/.env.example"
test -f "$ROOT/docker-compose.yml"
test -f "$ROOT/frontend/src/types/assets.d.ts"

echo "[2/4] Checking Python syntax"
python3 -m compileall -q "$ROOT/backend/app" "$ROOT/backend/alembic"

echo "[3/4] Checking frontend TypeScript syntax"
if command -v node >/dev/null 2>&1; then
  ROOT_PATH="$ROOT" node <<'NODE'
const ts = require('/opt/nvm/versions/node/v22.16.0/lib/node_modules/typescript');
const fs = require('fs');
const path = require('path');
const root = process.env.ROOT_PATH;
for (const rel of ['frontend/src/main.tsx', 'frontend/src/lib/api.ts', 'frontend/vite.config.ts']) {
  const file = path.join(root, rel);
  const source = fs.readFileSync(file, 'utf8');
  const result = ts.transpileModule(source, {
    fileName: file,
    reportDiagnostics: true,
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext }
  });
  const errors = (result.diagnostics || []).filter(d => d.category === ts.DiagnosticCategory.Error);
  if (errors.length) {
    console.error(errors.map(d => ts.flattenDiagnosticMessageText(d.messageText, '\n')).join('\n'));
    process.exit(1);
  }
}
NODE
else
  echo "Node not installed; skipping syntax-only frontend check"
fi

echo "[4/4] Checking Docker Compose syntax when Docker is available"
if command -v docker >/dev/null 2>&1; then
  (cd "$ROOT" && docker compose config >/dev/null)
else
  echo "Docker not installed; run 'docker compose config' on the deployment server"
fi

echo "Validation completed"
