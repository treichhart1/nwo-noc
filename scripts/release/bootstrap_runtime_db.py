"""Least-privilege runtime role in the explicitly generated isolated DB only."""

import re, subprocess, sys
from pathlib import Path
from validate_config import validate

ROOT = Path(__file__).resolve().parents[2]


def main(state):
    values = validate(Path(state) / "acceptance.env")
    password = values["APP_DATABASE_PASSWORD"]
    if not re.fullmatch("[a-f0-9]{48}", password):
        raise ValueError("Generated isolated runtime password required")
    # Generated hex is validated before literal interpolation; never put it in argv/logs.
    sql = f"""DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='noc_app') THEN CREATE ROLE noc_app LOGIN; END IF; END $$;
DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname='noc_app') THEN RAISE EXCEPTION 'Unexpected runtime role membership'; END IF; END $$;
ALTER ROLE noc_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{password}';
GRANT CONNECT ON DATABASE nwo_noc TO noc_app;
REVOKE TEMPORARY ON DATABASE nwo_noc FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO noc_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO noc_app;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO noc_app;
ALTER DEFAULT PRIVILEGES FOR ROLE noc_test IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO noc_app;
ALTER DEFAULT PRIVILEGES FOR ROLE noc_test IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO noc_app;
"""
    result = subprocess.run(
        [
            str(ROOT / "scripts/release/manage.sh"),
            str(state),
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "noc_test",
            "-d",
            "nwo_noc",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=sql,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ValueError(
            "Isolated runtime-role bootstrap failed; retain state and inspect database permissions"
        )
    print(
        "Isolated noc_app runtime role prepared without schema/role administration privileges"
    )


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except (ValueError, KeyError, OSError, IndexError):
        raise SystemExit(
            "Runtime database preparation rejected; verify generated isolated configuration. No credentials are logged."
        )
