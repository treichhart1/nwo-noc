#!/usr/bin/env python3
"""Actual disposable WordPress/PHP acceptance against a synthetic TLS gateway.

Docker internal networks intentionally isolate fixture public-range IP addresses.
The production plugin retains public-IP validation, DNS pinning and TLS checks.
PostgreSQL approval/view tests run separately in the required RF PostgreSQL job.
"""

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
WP_SIX = "wordpress:6-php8.3-apache@sha256:5d2c212561c4b5442ebc4d98933a9cbadcf3dee8888ed3fd9ed44667c27cc905"
WP_CURRENT = "wordpress:php8.3-apache@sha256:9b46af6a41fd0dcb2e5cf85033a6350d05010356847ede1cfcac5cf94292eb26"
WP_MIN = "wordpress:6.5-php8.3-apache@sha256:a9ea04325159fcf4a724c7fdd2d2db5454befb2f99fa59934838a46dbe348398"
DB = "mariadb:11.4@sha256:a8992b5e5404d11e03c54a31d0c1bdea3fd116d283aa6d1a627551f29045309b"


def run(args, **kw):
    return subprocess.run(
        args, check=True, text=True, timeout=kw.pop("timeout", 180), **kw
    )


def main():
    signup_mode = "--signup" in sys.argv[2:]
    evidence = Path(sys.argv[1]).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    fixtures = evidence / "fixtures"
    fixtures.mkdir()
    (fixtures / "mode").write_text("healthy")
    run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "2",
            "-subj",
            "/CN=NOC128 disposable test CA",
            "-keyout",
            str(fixtures / "ca-key.pem"),
            "-out",
            str(fixtures / "ca.pem"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    run(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=status.noc128.test",
            "-keyout",
            str(fixtures / "key.pem"),
            "-out",
            str(fixtures / "request.csr"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (fixtures / "extensions").write_text(
        ("subjectAltName=DNS:status.noc128.test,DNS:wordpress.noc128.test\nextendedKeyUsage=serverAuth\n" if signup_mode else "subjectAltName=DNS:status.noc128.test\nextendedKeyUsage=serverAuth\n")
    )
    run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(fixtures / "request.csr"),
            "-CA",
            str(fixtures / "ca.pem"),
            "-CAkey",
            str(fixtures / "ca-key.pem"),
            "-CAcreateserial",
            "-days",
            "2",
            "-extfile",
            str(fixtures / "extensions"),
            "-out",
            str(fixtures / "cert.pem"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for f in ["key.pem", "ca-key.pem"]:
        (fixtures / f).chmod(0o600)
    run(
        [
            "python3",
            str(ROOT / ("scripts/signup/build.py" if signup_mode else "scripts/wordpress/build.py")),
            "--output",
            str(evidence / "plugin"),
        ],
        stdout=subprocess.DEVNULL,
    )
    password = secrets.token_urlsafe(30)
    env = {
        **os.environ,
        "NOC128_PASSWORD": password,
        "MYSQL_ROOT_PASSWORD": password,
        "WORDPRESS_DB_PASSWORD": password,
        "MYSQL_PASSWORD": password,
        "MYSQL_PWD": password,
        "WORDPRESS_ACCEPTANCE": "1",
        "NOC128_EVIDENCE": str(evidence),
        "NOC128_WORDPRESS_ORIGIN": "https://wordpress.noc128.test" if signup_mode else "http://93.184.216.36",
        "NOC129_SIGNUP_WORDPRESS": "true" if signup_mode else "false",
    }
    for name in ["noc128-wordpress", "noc128-db", "noc128-fixture", "noc128-proxy"] + (["noc129-postgres"] if signup_mode else []):
        check = subprocess.run(
            ["docker", "container", "inspect", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if check.returncode == 0:
            raise RuntimeError("Refusing existing acceptance container: " + name)
    run(
        [
            "docker",
            "network",
            "create",
            "--internal",
            "--subnet",
            "93.184.216.0/24",
            "noc128-wp",
        ]
    )
    try:
        if signup_mode:
            run(["docker","run","-d","--name","noc129-postgres","--network","noc128-wp","-e","POSTGRES_USER=synthetic","-e","POSTGRES_PASSWORD=synthetic-ci-only","-e","POSTGRES_DB=nwo_noc","noc117-postgres:rc"],stdout=subprocess.DEVNULL)
            for _ in range(60):
                if subprocess.run(["docker","exec","noc129-postgres","pg_isready","-h","127.0.0.1","-U","synthetic","-d","nwo_noc"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
                time.sleep(1)
            else:raise RuntimeError("Synthetic PostgreSQL did not become ready")
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                "noc128-fixture",
                "--user",
                "0:0",
                "--network",
                "noc128-wp",
                "--ip",
                "93.184.216.34",
                "-v",
                str(fixtures) + ":/fixtures:ro",
                "-v",
                str(ROOT / ("scripts/signup/fixture.py" if signup_mode else "scripts/wordpress/fixture_gateway.py")) + ":/fixture.py:ro",
                *(["-v",str(ROOT / "examples/signup/public-role.sql")+":/signup-public-role.sql:ro",
                   "-e","PYTHONPATH=/app","-e","WORDPRESS_ACCEPTANCE=1","-e","APP_ENV=staging","-e","OFFLINE_MODE=true","-e","SYNTHETIC_MONITORING_ENABLED=true",
                   "-e","SECRET_KEY=synthetic-noc129-fixture-secret-synthetic","-e","ADMIN_PASSWORD=synthetic-ci-only",
                   "-e","DATABASE_URL=postgresql+psycopg://synthetic:synthetic-ci-only@noc129-postgres:5432/nwo_noc"] if signup_mode else []),
                *(["-v",str(ROOT / "scripts/signup/quote_fixture.py")+":/quote-fixture.py:ro"] if signup_mode else []),
                "--entrypoint",
                "python",
                "noc128-backend:test",
                "/fixture.py",
            ],
            stdout=subprocess.DEVNULL,
        )
        if signup_mode:
            # A container ID and a frontend health response are not proof that
            # the actual restricted-role ASGI process migrated and started.
            for _ in range(90):
                ready=subprocess.run(["docker","exec","noc128-fixture","python","-c","import urllib.request;urllib.request.urlopen('http://127.0.0.1:8002/public/signup/health',timeout=2)"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                if ready.returncode==0:break
                state=run(["docker","inspect","--format","{{.State.Running}}","noc128-fixture"],capture_output=True).stdout.strip()
                if state!='true':
                    # Only synthetic fixtures/credentials exist in this container.
                    logs=subprocess.run(["docker","logs","noc128-fixture"],capture_output=True,text=True)
                    print(logs.stderr[-12000:],file=sys.stderr)
                    raise RuntimeError("Synthetic restricted signup gateway exited before readiness")
                time.sleep(1)
            else:raise RuntimeError("Synthetic restricted signup gateway did not become ready")
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                "noc128-db",
                "--network",
                "noc128-wp",
                "--ip",
                "93.184.216.35",
                "-e",
                "MYSQL_ROOT_PASSWORD",
                "-e",
                "MYSQL_DATABASE=wordpress",
                "-e",
                "MYSQL_USER=wordpress",
                "-e",
                "MYSQL_PASSWORD",
                DB,
            ],
            env=env,
            stdout=subprocess.DEVNULL,
        )
        # Test the actual minimal proxy as its non-root image user; only disposable
        # certificate ownership and test listener hostname/port differ from example.
        run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0:0",
                "-v",
                str(fixtures) + ":/fixtures",
                "--entrypoint",
                "python",
                "noc128-backend:test",
                "-c",
                'import os;os.chown("/fixtures/key.pem",101,101)',
            ]
        )
        proxy_conf = (
            (ROOT / "deploy/customer-status/nginx-v2.container.conf")
            .read_text()
            .replace("listen 8443 ssl;", "listen 443 ssl;")
            .replace("status.example.invalid", "status.noc128.test")
        )
        if signup_mode:proxy_conf=(ROOT / "scripts/signup/proxy.conf").read_text()
        (fixtures / "proxy.conf").write_text(proxy_conf)
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                "noc128-proxy",
                "--network",
                "noc128-wp",
                "--ip",
                "93.184.216.37",
                "--add-host",
                "status-gateway:93.184.216.34",
                *(["--add-host","signup-gateway:93.184.216.34"] if signup_mode else []),
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16m",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--memory",
                "64m",
                "--cpus",
                "0.5",
                "-v",
                str(fixtures / "proxy.conf") + ":/etc/nginx/conf.d/status.conf:ro",
                "-v",
                str(fixtures / "cert.pem") + ":/run/secrets/status_tls_certificate:ro",
                "-v",
                str(fixtures / "key.pem") + ":/run/secrets/status_tls_private_key:ro",
                "noc129-signup-web:test" if signup_mode else "noc128-status-proxy:test",
            ],
            stdout=subprocess.DEVNULL,
        )
        run(
            ["docker", "exec", "noc128-proxy", "nginx", "-t"], stdout=subprocess.DEVNULL
        )
        run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--fail",
                "--retry",
                "10",
                "--retry-connrefused",
                "--retry-delay",
                "1",
                "--max-time",
                "2",
                "--noproxy",
                "*",
                "--resolve",
                "status.noc128.test:443:93.184.216.37",
                "--cacert",
                str(fixtures / "ca.pem"),
                "https://status.noc128.test/health",
            ],
            stdout=subprocess.DEVNULL,
        )
        for path in [
            "/api/customer-status/v1/cases",
            "/api",
            "/private",
            "/public/network-status/v1",
        ]:
            result = run(
                [
                    "curl",
                    "--silent",
                    "--show-error",
                    "--noproxy",
                    "*",
                    "--resolve",
                    "status.noc128.test:443:93.184.216.37",
                    "--cacert",
                    str(fixtures / "ca.pem"),
                    "--output",
                    "/dev/null",
                    "--write-out",
                    "%{http_code}",
                    "https://status.noc128.test" + path,
                ],
                capture_output=True,
            )
            if result.stdout != "404":
                raise RuntimeError("Public proxy exposed a non-allowlisted route")
        (evidence / "proxy-isolation.json").write_text(
            json.dumps(
                {
                    "private_paths": "404",
                    "tls_verification": True,
                    "user": 101,
                    "read_only": True,
                    "capabilities": "none",
                    "scope": "actual proxy image; isolated synthetic upstream",
                },
                indent=2,
            )
        )
        for _ in range(60):
            check = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-e",
                    "MYSQL_PWD",
                    "noc128-db",
                    "mariadb-admin",
                    "-uroot",
                    "ping",
                    "--silent",
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if check.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable MariaDB did not become ready")
        if signup_mode:
            nss=Path.home()/'.pki/nssdb';nss.mkdir(parents=True,exist_ok=True)
            if not (nss/'cert9.db').exists():run(['certutil','-N','--empty-password','-d','sql:'+str(nss)])
            run(['certutil','-A','-d','sql:'+str(nss),'-n','noc129-disposable-ca','-t','C,,','-i',str(fixtures/'ca.pem')])
        versions = []
        for image in [WP_MIN, WP_SIX, WP_CURRENT]:
            (fixtures / "mode").write_text("healthy")
            run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    "noc128-wordpress",
                    "--network",
                    "noc128-wp",
                    "--ip",
                    "93.184.216.36",
                    "--dns",
                    "93.184.216.34",
                    "-e",
                    "NOC128_PASSWORD",
                    "-e",
                    "NOC128_WORDPRESS_ORIGIN",
                    "-e",
                    "WORDPRESS_DB_HOST=noc128-db",
                    "-e",
                    "WORDPRESS_DB_USER=wordpress",
                    "-e",
                    "WORDPRESS_DB_PASSWORD",
                    "-e",
                    'WORDPRESS_CONFIG_EXTRA=define("FS_METHOD","direct"); define("DISALLOW_FILE_EDIT",true); if(isset($_SERVER["HTTP_X_FORWARDED_PROTO"]) && $_SERVER["HTTP_X_FORWARDED_PROTO"]==="https"){$_SERVER["HTTPS"]="on";}',
                    "-v",
                    str(ROOT / "scripts/wordpress/tests") + ":/tests:ro",
                    "-v",
                    str(fixtures / "ca.pem")
                    + ":/usr/local/share/ca-certificates/noc128-fixture.crt:ro",
                    image,
                ],
                env=env,
                stdout=subprocess.DEVNULL,
            )
            run(
                ["docker", "exec", "noc128-wordpress", "update-ca-certificates"],
                stdout=subprocess.DEVNULL,
            )
            for _ in range(60):
                result = subprocess.run(
                    [
                        "docker",
                        "exec",
                        "noc128-wordpress",
                        "php",
                        "-r",
                        "exit(is_file('/var/www/html/wp-config.php')?0:1);",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if result.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("WordPress initialization did not complete")
            result = run(
                ["docker", "exec", "noc128-wordpress", "php", "/tests/setup.php"],
                capture_output=True,
            )
            versions.append(json.loads(result.stdout))
            versions[-1]["image"] = image
            versions[-1]["image_id"] = run(
                ["docker", "inspect", "--format", "{{.Image}}", "noc128-wordpress"],
                capture_output=True,
            ).stdout.strip()
            if signup_mode:
                run(["docker","cp",str(ROOT / "scripts/signup/tests"),"noc128-wordpress:/signup-tests"])
                run(["docker","exec","noc129-postgres","psql","-U","synthetic","-d","nwo_noc","-c","DELETE FROM signup_public_budgets"],stdout=subprocess.DEVNULL)
            import ssl
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(fixtures / "ca.pem"))))
            for _ in range(30):
                try:
                    with opener.open(
                        ("http://93.184.216.36/wp-includes/js/wp-embed.min.js" if signup_mode else env["NOC128_WORDPRESS_ORIGIN"]+"/wp-login.php"), timeout=2
                    ) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                time.sleep(1)
            else:
                state = run(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{json .State}}",
                        "noc128-wordpress",
                    ],
                    capture_output=True,
                )
                raise RuntimeError("Isolated WordPress HTTP not ready: " + state.stdout)

            run(
                [
                    "docker",
                    "cp",
                    str(ROOT / "scripts/wordpress/tests/theme"),
                    "noc128-wordpress:/var/www/html/wp-content/themes/noc128classic",
                ]
            )
            run(
                [
                    "docker",
                    "exec",
                    "noc128-wordpress",
                    "sh",
                    "-c",
                    'find /tests -name "*.php" -exec php -l {} \\;',
                ],
                stdout=subprocess.DEVNULL,
            )
            run(
                [
                    "npx",
                    "playwright",
                    "test",
                    "signup-wordpress.spec.ts" if signup_mode else "wordpress-plugin.spec.ts",
                    "--workers=1",
                ],
                cwd=ROOT / "frontend",
                env=env,
                timeout=600,
            )
            print(
                json.dumps({"wordpress_compatibility": versions[-1], "result": "PASS"}),
                flush=True,
            )
            run(["docker", "rm", "-f", "noc128-wordpress"], stdout=subprocess.DEVNULL)
            # New independent WP schema for the second compatibility target.
            run(
                [
                    "docker",
                    "exec",
                    "-e",
                    "MYSQL_PWD",
                    "noc128-db",
                    "mariadb",
                    "-uroot",
                    "-e",
                    "DROP DATABASE wordpress; CREATE DATABASE wordpress; GRANT ALL ON wordpress.* TO wordpress;",
                ],
                env=env,
                stdout=subprocess.DEVNULL,
            )
        (evidence / "compatibility.json").write_text(
            json.dumps(
                {
                    "targets": versions,
                    "scope": ("actual disposable WordPress/PHP and restricted PostgreSQL signup ASGI; synthetic same-site HTTPS/DNS; no website deployment" if signup_mode else "actual disposable WordPress; synthetic HTTPS/DNS public feed; no website deployment"),
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        if signup_mode:
            subprocess.run(["certutil","-D","-d","sql:"+str(Path.home()/".pki/nssdb"),"-n","noc129-disposable-ca"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for name in ["noc128-wordpress", "noc128-db", "noc128-fixture", "noc128-proxy"] + (["noc129-postgres"] if signup_mode else []):
            subprocess.run(
                ["docker", "rm", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        subprocess.run(
            ["docker", "network", "rm", "noc128-wp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Disposable private keys never enter uploaded evidence.
        for name in ["key.pem", "ca-key.pem", "request.csr"]:
            (fixtures / name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
