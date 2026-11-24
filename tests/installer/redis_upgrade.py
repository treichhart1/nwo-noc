"""Actual Redis7 old-to-patched library upgrade, durable AOF and restart regression."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


def command(*args, check=True):
    result = subprocess.run(list(args), capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise RuntimeError("Disposable Redis compatibility command failed")
    return result.stdout.strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", default="noc120-redis:rc")
    p.add_argument("--execute-disposable-test", action="store_true")
    args = p.parse_args()
    if not args.execute_disposable_test or os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("Explicit disposable CI execution required")
    base = json.loads((Path(__file__).resolve().parents[2] / "deploy/image-lock.json").read_text())["images"]["redis:7-alpine"]
    command("docker", "pull", base)  # Exact immutable baseline; BuildKit cache is not a loaded image.
    identities = {role: command("docker", "image", "inspect", "--format", "{{.Id}}", ref)
                  for role, ref in (("baseline", base), ("candidate", args.candidate))}
    command("docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--entrypoint", "sh", identities["candidate"], "/usr/local/bin/noc-redis-verify")
    hashes = [command("docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                      "--entrypoint", "sha256sum", ref, "/usr/local/bin/redis-server", "/usr/local/bin/redis-cli")
              for ref in identities.values()]
    if hashes[0] != hashes[1]:
        raise ValueError("Redis binaries changed across library-only update")
    name = "noc120-redis-compat-" + uuid.uuid4().hex[:12]
    volume = name + "-data"
    existing = subprocess.run(["docker", "volume", "inspect", volume], capture_output=True, timeout=15)
    if existing.returncode == 0:
        raise ValueError("Existing volume retained; refusing compatibility test")
    command("docker", "volume", "create", volume)
    containers = []
    passed = False
    def start(role, suffix):
        container = name + suffix
        command("docker", "run", "-d", "--name", container, "--network", "none", "--read-only",
                "--user", "999:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--cpus", "0.5", "--memory", "128m", "--pids-limit", "128",
                "--mount", "type=volume,src=" + volume + ",dst=/data",
                identities[role], "redis-server", "--appendonly", "yes", "--appendfsync", "always")
        containers.append(container)
        for _ in range(60):
            if command("docker", "exec", container, "redis-cli", "PING", check=False) == "PONG":
                return container
            time.sleep(0.5)
        raise RuntimeError("Disposable Redis did not become ready")
    def cli(container, *argv):
        return command("docker", "exec", container, "redis-cli", "--raw", *argv)
    try:
        old = start("baseline", "-old")
        assert cli(old, "SET", "noc120:sentinel", "synthetic-only") == "OK"
        assert cli(old, "RPUSH", "noc120:queue", "job-1", "job-2") == "2"
        assert cli(old, "SAVE") == "OK"
        command("docker", "stop", "--time", "30", old)
        new = start("candidate", "-patched")
        assert cli(new, "GET", "noc120:sentinel") == "synthetic-only"
        assert cli(new, "LRANGE", "noc120:queue", "0", "-1") == "job-1\njob-2"
        assert cli(new, "RPUSH", "noc120:queue", "job-3") == "3"
        assert cli(new, "SAVE") == "OK"
        command("docker", "stop", "--time", "30", new)
        check = start("candidate", "-restarted")
        assert cli(check, "GET", "noc120:sentinel") == "synthetic-only"
        assert cli(check, "LRANGE", "noc120:queue", "0", "-1") == "job-1\njob-2\njob-3"
        assert cli(check, "DBSIZE") == "2"
        passed = True
        print(json.dumps({"redis7_library_upgrade": "PASS", "images": identities,
                          "server_client_binary_inventory_sha256": hashlib.sha256(hashes[0].encode()).hexdigest(),
                          "aof_and_rdb": "preserved", "queue_order_and_count": 3,
                          "restarted_with_preserved_sentinel": True, "network": "none",
                          "scope": "Disposable synthetic data; no production rollback authorization"}))
    finally:
        if passed:
            for container in containers:
                command("docker", "rm", "-f", container)
            command("docker", "volume", "rm", volume)
        else:
            print(json.dumps({"redis7_library_upgrade": "FAIL", "disposable_state_retained": True}))
            # Preserve failed state for diagnosis; never remove existing infrastructure.


if __name__ == "__main__":
    main()
