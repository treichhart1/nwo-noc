#!/usr/bin/env bash
# Only a new, disposable offline project; all volumes/backups retained.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
state=${1:?Usage: rehearse-upgrade.sh NEW_ABSOLUTE_STATE}
[[ $state = /* && ! -e $state ]] || { echo 'Requires a new absolute isolated state directory' >&2; exit 2; }
baseline=bf642bdb467b696c9a3778d51bf4ce816d0f82f7
if ! git -C "$ROOT" cat-file -e "$baseline^{commit}" 2>/dev/null; then git -C "$ROOT" fetch origin "$baseline"; fi
build_dir=$(mktemp -d /tmp/noc93-old-images.XXXXXX)
trap 'rm -rf "$build_dir"' EXIT
git -C "$ROOT" archive "$baseline" | tar -x -C "$build_dir"
args=()
if [[ -n ${NOC_BUILD_CA:-} ]]; then args+=(--secret "id=proxy_ca,src=$NOC_BUILD_CA"); fi
docker build "${args[@]}" -t noc93-backend:rollback-bf642bd "$build_dir/backend"
docker build "${args[@]}" -t noc93-frontend:rollback-bf642bd "$build_dir/frontend"
python3 "$ROOT/scripts/release/rehearse_upgrade.py" "$state"
