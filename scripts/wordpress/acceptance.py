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
WP_CURRENT = "wordpress:6-php8.3-apache@sha256:5d2c212561c4b5442ebc4d98933a9cbadcf3dee8888ed3fd9ed44667c27cc905"
WP_MIN = "wordpress:6.5-php8.3-apache@sha256:a9ea04325159fcf4a724c7fdd2d2db5454befb2f99fa59934838a46dbe348398"
DB = "mariadb:11.4@sha256:a8992b5e5404d11e03c54a31d0c1bdea3fd116d283aa6d1a627551f29045309b"


def run(args, **kw):
    return subprocess.run(
        args, check=True, text=True, timeout=kw.pop("timeout", 180), **kw
    )


def main():
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
        "subjectAltName=DNS:status.noc128.test\nextendedKeyUsage=serverAuth\n"
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
            str(ROOT / "scripts/wordpress/build.py"),
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
        "NOC128_WORDPRESS_ORIGIN": "http://93.184.216.36",
    }
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
                str(ROOT / "scripts/wordpress/fixture_gateway.py") + ":/fixture.py:ro",
                "--entrypoint",
                "python",
                "noc128-backend:test",
                "/fixture.py",
            ],
            stdout=subprocess.DEVNULL,
        )
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
        versions = []
        for image in [WP_MIN, WP_CURRENT]:
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
                    'WORDPRESS_CONFIG_EXTRA=define("FS_METHOD","direct"); define("DISALLOW_FILE_EDIT",true);',
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
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(30):
                try:
                    with opener.open(
                        env["NOC128_WORDPRESS_ORIGIN"] + "/wp-login.php", timeout=2
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
                    "wordpress-plugin.spec.ts",
                    "--workers=1",
                ],
                cwd=ROOT / "frontend",
                env=env,
                timeout=600,
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
                    "scope": "actual disposable WordPress; synthetic HTTPS/DNS public feed; no website deployment",
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        for name in ["noc128-wordpress", "noc128-db", "noc128-fixture"]:
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
