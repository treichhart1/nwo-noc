#!/usr/bin/env bash
# Run as root only on a fresh disposable Ubuntu 24.04 machine.
set -Eeuo pipefail
source /etc/os-release
[[ $ID = ubuntu && $VERSION_ID = 24.04 ]] || { echo 'Only Ubuntu 24.04 supported' >&2; exit 2; }
[[ $(id -u) = 0 ]] || { echo 'Run bootstrap with sudo on the disposable host' >&2; exit 2; }
trap 'echo "Dependency installation failed. Check Ubuntu repository access and apt logs." >&2' ERR
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates git python3 docker.io docker-compose-v2
if [[ ${NOC_BOOTSTRAP_CONTAINER:-0} != 1 ]]; then systemctl enable --now docker; fi
docker --version
docker compose version
python3 --version
