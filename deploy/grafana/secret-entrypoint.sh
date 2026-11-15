#!/bin/sh
set -eu
# No xtrace, credential argv or output. Files are mounted read-only, runtime-owned.
[ -f /run/secrets/grafana_db_password ] || { echo 'Grafana database credential file missing' >&2; exit 1; }
NOC_GRAFANA_DB_PASSWORD=$(cat /run/secrets/grafana_db_password)
export NOC_GRAFANA_DB_PASSWORD
exec /run.sh
