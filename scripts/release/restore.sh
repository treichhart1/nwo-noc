#!/usr/bin/env bash
# Restore ONLY into a new acceptance recovery database. Never drop/replace.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
state_dir=${1:?Usage: restore.sh STATE_DIRECTORY DUMP_PATH restore_NAME}
dump=${2:?Specify dump path}; target=${3:?Specify new restore_NAME database}
[[ $target =~ ^restore_[a-z0-9_]+$ ]] || { echo 'Target must be a new database named restore_[a-z0-9_]+; live/source database forbidden' >&2; exit 2; }
[[ -f $dump && -f $dump.sha256 ]] || { echo 'Dump/checksum missing' >&2; exit 2; }
sha256sum --check "$dump.sha256"
manage=("$ROOT/scripts/release/manage.sh" "$state_dir")
"${manage[@]}" exec -T db pg_restore --list < "$dump" >/dev/null
"${manage[@]}" exec -T db createdb -U noc_test "$target"
"${manage[@]}" exec -T db pg_restore -U noc_test -d "$target" --exit-on-error --no-owner --no-privileges < "$dump"
"${manage[@]}" exec -T db psql -U noc_test -d "$target" -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version'
echo "Restored to $target; source untouched. On failure retain target for diagnosis; retry with a new name."
