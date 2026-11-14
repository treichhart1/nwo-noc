"""Actual isolated runtime security checks; never device-network access."""

import json, os, subprocess, sys, time, urllib.request, urllib.parse, urllib.error, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/release"))
from validate_config import validate

STATE = os.environ.get("NOC_ACCEPTANCE_STATE")


@unittest.skipUnless(STATE, "Explicit generated isolated state required")
class SecurityAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = validate(Path(STATE) / "acceptance.env")
        cls.base = "http://127.0.0.1:18090"

    @classmethod
    def manage(cls, *args):
        return subprocess.run(
            [str(ROOT / "scripts/release/manage.sh"), STATE, *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=90,
        ).stdout

    @classmethod
    def request(cls, path, token=None, method="GET", data=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(
                    cls.base + path, method=method, data=data, headers=headers
                ),
                timeout=10,
            )
            return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    @classmethod
    def login(cls, name="acceptance"):
        data = urllib.parse.urlencode(
            {"username": name, "password": cls.env["ADMIN_PASSWORD"]}
        ).encode()
        status, body = cls.request("/api/auth/login", method="POST", data=data)
        assert status == 200
        return json.loads(body)["access_token"]

    def test_01_application_processes_nonroot_and_readonly(self):
        for service, uid in (
            ("backend", "10001"),
            ("worker", "10001"),
            ("frontend", "101"),
        ):
            self.assertEqual(
                self.manage("exec", "-T", service, "id", "-u").strip(), uid
            )
            ident = self.manage("ps", "-q", service).strip()
            cfg = json.loads(
                subprocess.run(
                    ["docker", "--host=unix:///var/run/docker.sock", "inspect", ident],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
            )[0]
            self.assertTrue(cfg["HostConfig"]["ReadonlyRootfs"])
            self.assertIn("ALL", cfg["HostConfig"]["CapDrop"])
            self.assertIn("no-new-privileges:true", cfg["HostConfig"]["SecurityOpt"])

    def test_02_runtime_database_cannot_administer_schema_or_roles(self):
        code = """import os
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.database import SessionLocal
assert not os.environ.get('POSTGRES_PASSWORD')
with SessionLocal() as db:
 role=db.execute(text('SELECT current_user,rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user')).one()
 assert tuple(role)==('noc_app',False,False,False)
 assert not db.scalar(text("SELECT pg_has_role(current_user,'noc_test','MEMBER')"))
 try:db.execute(text('CREATE TABLE noc94_forbidden_ddl(value integer)'))
 except DBAPIError:db.rollback()
 else:db.rollback();raise AssertionError('Runtime schema DDL unexpectedly allowed')
print('restricted runtime database verified')"""
        self.assertIn(
            "verified", self.manage("exec", "-T", "backend", "python", "-c", code)
        )

    def test_03_revoked_token_stays_revoked_after_service_restart(self):
        revoked = self.login()
        other = self.login()
        self.assertEqual(self.request("/api/auth/logout", revoked, "POST")[0], 204)
        self.manage("restart", "backend")
        for _ in range(40):
            try:
                if self.request("/api/auth/me", other)[0] == 200:
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(1)
        self.assertEqual(self.request("/api/auth/me", revoked)[0], 401)
        self.assertEqual(self.request("/api/auth/me", other)[0], 200)

    def test_04_operator_stop_is_admin_only_and_persists(self):
        viewer = self.login("acceptance-viewer")
        admin = self.login()
        self.assertEqual(
            self.request(
                "/api/monitoring/reliability/emergency-disable", viewer, "POST"
            )[0],
            403,
        )
        self.assertEqual(
            self.request(
                "/api/monitoring/reliability/emergency-disable", admin, "POST"
            )[0],
            200,
        )
        self.manage("restart", "worker", "backend")
        for _ in range(40):
            try:
                status, body = self.request("/api/monitoring/reliability", admin)
                if status == 200 and json.loads(body)["operator_disabled"]:
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(1)
        self.assertTrue(
            json.loads(self.request("/api/monitoring/reliability", admin)[1])[
                "operator_disabled"
            ]
        )
        self.assertEqual(
            self.request("/api/devices/1/monitor/check", admin, "POST")[0], 409
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
