#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
state_dir=${1:?Usage: manage.sh ABSOLUTE_STATE_DIRECTORY COMPOSE_ARGS...}; shift
[[ $state_dir = /* && -f $state_dir/acceptance.env ]] || { echo 'Missing isolated acceptance.env; run install.sh' >&2; exit 2; }
python3 "$ROOT/scripts/release/validate_config.py" "$state_dir/acceptance.env"
[[ ${NOC_PROJECT:-noc84-acceptance} =~ ^noc84-[a-z0-9-]+$ ]] || { echo "Project must use noc84- prefix" >&2; exit 2; }
export NOC_ENV_FILE="$state_dir/acceptance.env"
# Pin local daemon; ignore inherited remote endpoint selectors.
unset DOCKER_HOST DOCKER_CONTEXT DOCKER_TLS DOCKER_TLS_VERIFY DOCKER_CERT_PATH
exec docker --host=unix:///var/run/docker.sock compose --env-file "$NOC_ENV_FILE" -f "$ROOT/deploy/compose.isolated.yml" "$@"
