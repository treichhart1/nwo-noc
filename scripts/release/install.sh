#!/usr/bin/env bash
# Explicit isolated acceptance install only. Does not modify the host firewall.
set -Eeuo pipefail
trap 'echo "Installation failed at line $LINENO. Inspect: scripts/release/manage.sh $state_dir logs" >&2' ERR
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
state_dir=${1:?Usage: install.sh ABSOLUTE_STATE_DIRECTORY}
[[ $state_dir = /* ]] || { echo 'Use an absolute state directory' >&2; exit 2; }
if [[ ${NOC_ALLOW_CONTAINER_TEST:-0} != 1 ]]; then
  source /etc/os-release
  [[ $ID = ubuntu && $VERSION_ID = 24.04 ]] || { echo 'Requires Ubuntu 24.04 LTS (container tests must explicitly set NOC_ALLOW_CONTAINER_TEST=1)' >&2; exit 2; }
fi
command -v python3 >/dev/null || { echo 'Install python3 first (see bootstrap-ubuntu.sh)' >&2; exit 2; }
command -v docker >/dev/null || { echo 'Install Docker/Compose first: scripts/release/bootstrap-ubuntu.sh' >&2; exit 2; }
docker --host=unix:///var/run/docker.sock info >/dev/null
# Read only this task's generated state, never the repository .env.
if [[ ! -f $state_dir/acceptance.env ]]; then python3 "$ROOT/scripts/release/configure.py" "$state_dir"; fi
if [[ ${NOC_SKIP_BUILD:-0} != 1 ]]; then
  args=()
  if [[ -n ${NOC_BUILD_CA:-} ]]; then args+=(--secret "id=proxy_ca,src=$NOC_BUILD_CA"); fi
  docker --host=unix:///var/run/docker.sock build "${args[@]}" -t noc84-backend:rc "$ROOT/backend"
  docker --host=unix:///var/run/docker.sock build "${args[@]}" -t noc84-frontend:rc "$ROOT/frontend"
fi
if [[ -f $state_dir/release-manifest.json ]]; then python3 "$ROOT/scripts/release/pin_images.py" "$state_dir"; fi
"$ROOT/scripts/release/manage.sh" "$state_dir" config --quiet
"$ROOT/scripts/release/manage.sh" "$state_dir" up -d --wait db redis
python3 "$ROOT/scripts/release/bootstrap_runtime_db.py" "$state_dir"
"$ROOT/scripts/release/prepare-runtime.sh" "$state_dir"
"$ROOT/scripts/release/manage.sh" "$state_dir" up -d --wait --wait-timeout 180
"$ROOT/scripts/release/manage.sh" "$state_dir" exec -T backend alembic check
"$ROOT/scripts/release/manage.sh" "$state_dir" exec -T worker python -m app.worker_health
echo "Isolated services ready. Start loopback access: python3 scripts/release/access.py $state_dir"
