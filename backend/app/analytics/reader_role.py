"""Explicit provisioning of a private, sanitized-view-only PostgreSQL login.

No passwords in argv, returned data or logs. Run using a privileged migration
connection against a disposable database or an explicitly approved deployment.
"""

import re
from sqlalchemy import text
from .service import private_secret, VIEW_NAMES


def install(db, password):
    if not re.fullmatch(r"[a-f0-9]{48,128}", password):
        raise ValueError("Generated hex reader password required")
    if db.scalar(
        text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='noc_grafana')")
    ):
        existing = db.execute(
            text(
                "SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolreplication FROM pg_roles WHERE rolname='noc_grafana'"
            )
        ).one()
        if any(existing):
            raise ValueError(
                "Existing reader role has unexpected privilege; operator review required"
            )
        if db.scalar(
            text(
                "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname='noc_grafana'"
            )
        ):
            raise ValueError("Reader role membership requires review")
    else:
        db.execute(
            text(
                "CREATE ROLE noc_grafana LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4"
            )
        )
    # Generated validated hex only; SQL role password cannot be a bind parameter.
    db.execute(text("ALTER ROLE noc_grafana PASSWORD '" + password + "'"))
    db.execute(
        text(
            "ALTER ROLE noc_grafana SET default_transaction_read_only=on; ALTER ROLE noc_grafana SET statement_timeout='5s'; ALTER ROLE noc_grafana SET idle_in_transaction_session_timeout='10s'; ALTER ROLE noc_grafana SET search_path=noc_analytics,pg_catalog;"
        )
    )
    database = db.scalar(text("SELECT current_database()"))
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise ValueError("Reviewed database identifier required")
    db.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO noc_grafana'))
    db.execute(text("GRANT USAGE ON SCHEMA noc_analytics TO noc_grafana"))
    for name in VIEW_NAMES:
        db.execute(text("GRANT SELECT ON noc_analytics." + name + " TO noc_grafana"))
    # PUBLIC grants and user-callable SECURITY DEFINER functions can bypass an otherwise restricted role.
    unsafe = db.scalar(
        text(
            "SELECT count(*) FROM information_schema.tables t WHERE t.table_schema='public' AND has_table_privilege('noc_grafana',quote_ident(t.table_schema)||'.'||quote_ident(t.table_name),'SELECT,INSERT,UPDATE,DELETE')"
        )
    )
    if unsafe:
        raise ValueError(
            "Reader inherits protected table privileges; rollback and review grants"
        )
    unsafe_functions = db.scalar(
        text(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND p.prosecdef AND has_function_privilege('noc_grafana',p.oid,'EXECUTE') AND p.prorettype<>'trigger'::regtype"
        )
    )
    if unsafe_functions:
        raise ValueError("User-callable SECURITY DEFINER privilege requires review")
    return {
        "role": "noc_grafana",
        "view_grants": list(VIEW_NAMES),
        "protected_tables_readable": False,
        "password_returned": False,
        "query_limits": "Defaults and connection caps; not an adversarial SQL sandbox",
    }


if __name__ == "__main__":
    import argparse, json
    from app.core.database import SessionLocal

    p = argparse.ArgumentParser()
    p.add_argument("--password-file", required=True)
    p.add_argument("--approve-private-reader", action="store_true")
    a = p.parse_args()
    if not a.approve_private_reader:
        raise SystemExit("Explicit --approve-private-reader required")
    with SessionLocal() as db:
        result = install(db, private_secret(a.password_file))
        db.commit()
        print(json.dumps(result))
