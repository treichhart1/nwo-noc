#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
state_dir=${1:?Usage: backup.sh STATE_DIRECTORY NEW_DUMP_PATH}
out=${2:?Specify a new dump path}
[[ ! -e $out ]] || { echo 'Refusing to overwrite existing backup' >&2; exit 2; }
tmp=$(mktemp "${out}.XXXXXX")
trap 'rm -f "$tmp"' EXIT
"$ROOT/scripts/release/manage.sh" "$state_dir" exec -T db pg_dump -U noc_test -d nwo_noc -Fc --no-owner --no-privileges > "$tmp"
"$ROOT/scripts/release/manage.sh" "$state_dir" exec -T db pg_restore --list < "$tmp" >/dev/null
mv -n "$tmp" "$out"
sha256sum "$out" > "$out.sha256"
echo "Backup complete: $out (mode 0600); store encrypted and off-host."
