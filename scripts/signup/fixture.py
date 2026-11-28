"""CI-only actual signup ASGI gateway with isolated DNS, PostgreSQL and synthetic seed."""

import os, threading, socketserver, struct, secrets
from pathlib import Path


def main():
    if os.environ.get("WORDPRESS_ACCEPTANCE") != "1":
        raise RuntimeError("Disposable acceptance only")
    from app.core.config import settings

    if not (
        settings.offline_mode
        and settings.app_env == "staging"
        and settings.synthetic_monitoring_enabled
    ):
        raise RuntimeError("Explicit isolated fixtures required")
    key = Path("/tmp/noc129-pii.key")
    from cryptography.fernet import Fernet

    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    settings.signup_pii_key_file = str(key)
    settings.signup_public_enabled = True
    settings.signup_public_test_mode = True
    settings.signup_turnstile_mode = "fixture"
    settings.signup_public_origin = "https://status.noc128.test"
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")
    from app.core.database import engine, get_db
    from sqlalchemy import text, create_engine
    from sqlalchemy.orm import Session
    from app.signup.models import Plan

    with Session(engine) as db:
        db.add(
            Plan(
                name="Synthetic configured wireless",
                audience="both",
                technology="wireless",
                public=True,
                active=True,
                currency="USD",
                monthly="65.00",
                installation="0.00",
                eligibility="Synthetic review only",
                equipment=[],
            )
        )
        db.add(
            Plan(
                name="Synthetic LTE manual review",
                audience="both",
                technology="lte",
                public=True,
                active=True,
                currency="USD",
                monthly="80.00",
                installation="0.00",
                eligibility="Carrier coverage unverified; manual qualification required",
                equipment=[],
            )
        )
        if os.environ.get('CANCELLATION_ACCEPTANCE')=='1':
            from app.models.entities import User
            from app.retention.models import Policy,Reason
            from app.retention.contracts import REASONS
            user=User(username='noc131-fixture-reviewer',email='synthetic-noc131@example.invalid',password_hash='no-login',role='admin',is_active=False)
            db.add(user);db.flush()
            db.add(Policy(version=1,text='Synthetic reviewed cancellation policy: after monthly billing the full month may remain payable, subject to operator review. Acknowledgement does not establish debt.',created_by=user.id))
            for code,label in REASONS.items():db.add(Reason(code=code,label=label,voluntary='unknown'))
            settings.retention_public_enabled=True
        db.commit()
    # Dedicated actual HTTP role, with the same restricted grants as deployment.
    from sqlalchemy.engine import make_url

    owner = make_url(settings.database_url)
    password = secrets.token_urlsafe(24)
    from psycopg import connect, sql

    with connect(
        owner.render_as_string(hide_password=False).replace(
            "postgresql+psycopg://", "postgresql://"
        )
    ) as c:
        c.execute("CREATE ROLE noc_signup NOLOGIN")
        c.execute(
            sql.SQL("CREATE ROLE noc129_public LOGIN PASSWORD {}").format(
                sql.Literal(password)
            )
        )
        grants = (
            Path("/signup-public-role.sql")
            .read_text()
            .replace("CREATE ROLE noc_signup NOLOGIN;", "")
        )
        c.execute(grants)
        c.execute("GRANT noc_signup TO noc129_public")
    restricted = create_engine(owner.set(username="noc129_public", password=password))
    with restricted.connect() as c:
        assert not c.scalar(
            text("SELECT has_table_privilege(current_user,'public.users','SELECT')")
        )
        assert not c.scalar(
            text(
                "SELECT has_table_privilege(current_user,'public.signup_quotes','SELECT')"
            )
        )
    from app.signup.public_app import app

    def database():
        with Session(restricted) as db:
            yield db

    app.dependency_overrides[get_db] = database

    class DNS(socketserver.BaseRequestHandler):
        def handle(self):
            data, sock = self.request
            end = 12
            labels = []
            try:
                while data[end]:
                    n = data[end]
                    labels.append(data[end + 1 : end + 1 + n].decode("ascii"))
                    end += n + 1
                end += 1
                kind = struct.unpack("!H", data[end : end + 2])[0]
                allowed = (
                    ".".join(labels) in {"status.noc128.test", "wordpress.noc128.test"}
                    and kind == 1
                )
                answer = (
                    b"\xc0\x0c"
                    + struct.pack("!HHIH", 1, 1, 30, 4)
                    + bytes([93, 184, 216, 37])
                    if allowed
                    else b""
                )
                sock.sendto(
                    data[:2]
                    + struct.pack("!HHHHH", 0x8180, 1, int(allowed), 0, 0)
                    + data[12 : end + 4]
                    + answer,
                    self.client_address,
                )
            except (IndexError, UnicodeError, struct.error):
                return

    dns = socketserver.UDPServer(("0.0.0.0", 53), DNS)
    threading.Thread(target=dns.serve_forever, daemon=True).start()
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
