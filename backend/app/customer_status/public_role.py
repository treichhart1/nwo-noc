"""Explicit least-privilege public gateway DB login; approved views and rate counters only."""

import re, json
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.analytics.service import private_secret


def install(db, password):
    if not re.fullmatch(r"[a-f0-9]{48,128}", password):
        raise ValueError("Generated hex gateway password required")
    role = "noc_status_public"
    if db.scalar(
        text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='noc_status_public')")
    ):
        flags = db.execute(
            text(
                "SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolreplication FROM pg_roles WHERE rolname='noc_status_public'"
            )
        ).one()
        if any(flags) or db.scalar(
            text(
                "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname='noc_status_public'"
            )
        ):
            raise ValueError(
                "Unexpected public gateway role privileges; review before provisioning"
            )
    else:
        db.execute(
            text(
                "CREATE ROLE noc_status_public LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4"
            )
        )
    try:
        db.execute(text("ALTER ROLE noc_status_public PASSWORD '" + password + "'"))
    except SQLAlchemyError:
        raise ValueError(
            "Gateway credential update rejected; no credential details logged"
        ) from None
    db.execute(
        text(
            "ALTER ROLE noc_status_public SET statement_timeout='2s'; ALTER ROLE noc_status_public SET idle_in_transaction_session_timeout='5s'; ALTER ROLE noc_status_public SET search_path=public,pg_catalog;"
        )
    )
    name = db.scalar(text("SELECT current_database()"))
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError("Reviewed database identifier required")
    db.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO noc_status_public'))
    db.execute(
        text(
            "GRANT USAGE ON SCHEMA public,noc_public_status TO noc_status_public; GRANT SELECT ON noc_public_status.configuration,noc_public_status.announcements,noc_public_status.areas TO noc_status_public; GRANT SELECT,INSERT,UPDATE,DELETE ON public.customer_status_public_budgets TO noc_status_public;"
        )
    )
    if db.scalar(
        text(
            "SELECT count(*) FROM information_schema.tables t WHERE t.table_schema='public' AND t.table_name<>'customer_status_public_budgets' AND has_table_privilege('noc_status_public',quote_ident(t.table_schema)||'.'||quote_ident(t.table_name),'SELECT,INSERT,UPDATE,DELETE')"
        )
    ):
        raise ValueError(
            "Gateway inherits private-table access; rollback and review PUBLIC grants"
        )
    if db.scalar(
        text(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND p.prosecdef AND has_function_privilege('noc_status_public',p.oid,'EXECUTE') AND p.prorettype<>'trigger'::regtype"
        )
    ):
        raise ValueError("Gateway callable SECURITY DEFINER functions require review")
    return {
        "role": role,
        "source_tables_accessible": False,
        "writes": "public rate counters only",
        "password_returned": False,
    }


if __name__ == "__main__":
    import argparse
    from app.core.database import SessionLocal

    p = argparse.ArgumentParser()
    p.add_argument("--password-file", required=True)
    p.add_argument("--approve-public-gateway", action="store_true")
    a = p.parse_args()
    if not a.approve_public_gateway:
        raise SystemExit("Explicit public gateway provisioning approval required")
    try:
        with SessionLocal() as db:
            result = install(db, private_secret(a.password_file))
            db.commit()
        print(json.dumps(result))
    except (ValueError, OSError, SQLAlchemyError):
        raise SystemExit(
            "Gateway provisioning failed; review private file ownership, migration privileges and inherited grants. No credentials are logged."
        ) from None
