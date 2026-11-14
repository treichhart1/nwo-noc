#!/usr/bin/env bash
set -Eeuo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
state=${1:?Specify isolated state directory}
image=$(python3 - "$state" <<'PY'
import sys
sys.path.insert(0,'scripts/release')
from validate_config import validate
print(validate(sys.argv[1]+'/acceptance.env').get('NOC_BACKEND_IMAGE','noc84-backend:rc'))
PY
)
user=$(docker --host=unix:///var/run/docker.sock image inspect --format '{{.Config.User}}' "$image")
case "$user" in ''|0|0:0) uid=0;; 10001|10001:10001) uid=10001;; *) echo 'Unreviewed runtime UID; refusing spool mutation' >&2; exit 2;; esac
"$ROOT/scripts/release/manage.sh" "$state" run --rm --no-deps --user 0:0 --cap-add CHOWN --cap-add FOWNER --cap-add DAC_OVERRIDE --volume "$ROOT/backend/app/runtime_permissions.py:/runtime_permissions.py:ro" --entrypoint python worker /runtime_permissions.py --uid "$uid"
