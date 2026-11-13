#!/usr/bin/env bash
# Reproduce the Ubuntu userspace installation from any local Docker host.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
state_dir=${1:?Usage: rehearse-ubuntu.sh ABSOLUTE_STATE_DIRECTORY}
[[ $state_dir = /* ]] || exit 2
# Image dependencies download through the configured Docker build proxy.
docker build -f "$ROOT/deploy/Dockerfile.ubuntu-rehearsal" -t noc84-ubuntu-test:24.04 "$ROOT"
if [[ ! -f $state_dir/acceptance.env ]]; then python3 "$ROOT/scripts/release/configure.py" "$state_dir"; fi
# Use the caller's UID so generated mode-0600 state remains accessible to them.
docker run --rm --user "$(id -u):$(id -g)" --group-add "$(stat -c %g /var/run/docker.sock)" \
  -v /var/run/docker.sock:/var/run/docker.sock -v "$ROOT:$ROOT" -v "$state_dir:$state_dir" -w "$ROOT" \
  -e NOC_SKIP_BUILD=1 -e "NOC_PROJECT=${NOC_PROJECT:-noc84-acceptance}" noc84-ubuntu-test:24.04 bash scripts/release/install.sh "$state_dir"
