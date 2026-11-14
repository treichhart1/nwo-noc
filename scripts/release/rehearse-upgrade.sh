#!/usr/bin/env bash
# Only a new, disposable offline project; all volumes/backups retained.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
state=${1:?Usage: rehearse-upgrade.sh NEW_ABSOLUTE_STATE}
[[ $state = /* && ! -e $state ]] || { echo 'Requires a new absolute isolated state directory' >&2; exit 2; }
baseline=b5329506a34ce0fe5b6889b20a559ab4f1646a0c
if ! git -C "$ROOT" cat-file -e "$baseline^{commit}" 2>/dev/null; then git -C "$ROOT" fetch origin "$baseline"; fi
build_dir=$(mktemp -d /tmp/noc93-old-images.XXXXXX)
trap 'rm -rf "$build_dir"' EXIT
# Preserve repository file readability despite a restrictive caller umask.
# This changes build-context permissions only, never baseline source contents.
(umask 022; git -C "$ROOT" archive "$baseline" | tar -x -C "$build_dir")
args=()
if [[ -n ${NOC_BUILD_CA:-} ]]; then args+=(--secret "id=proxy_ca,src=$NOC_BUILD_CA"); fi
docker build "${args[@]}" -t noc93-backend:rollback-b532950 "$build_dir/backend"
docker build "${args[@]}" -t noc93-frontend:rollback-b532950 "$build_dir/frontend"
python3 "$ROOT/scripts/release/rehearse_upgrade.py" "$state"
