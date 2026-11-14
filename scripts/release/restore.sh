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
# Restore ACLs for the restricted runtime role when that role exists. The target
# was freshly created above; no source/existing database is modified.
"${manage[@]}" exec -T db psql -U noc_test -d "$target" -v ON_ERROR_STOP=1 <<'SQL'
DO $$ BEGIN
IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='noc_app') THEN
 GRANT USAGE ON SCHEMA public TO noc_app;
 REVOKE CREATE ON SCHEMA public FROM PUBLIC;
 GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO noc_app;
 GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO noc_app;
 EXECUTE format('GRANT CONNECT ON DATABASE %I TO noc_app',current_database());
 EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC',current_database());
 ALTER DEFAULT PRIVILEGES FOR ROLE noc_test IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO noc_app;
 ALTER DEFAULT PRIVILEGES FOR ROLE noc_test IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO noc_app;
END IF;
END $$;
SQL
"${manage[@]}" exec -T db psql -U noc_test -d "$target" -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version'
echo "Restored to $target; source untouched. On failure retain target for diagnosis; retry with a new name."
