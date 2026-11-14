"""Sample only the explicitly validated isolated project's Docker resources."""

import json, os, subprocess, sys, time, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def memory_mib(value):
    number, unit = re.fullmatch(r"([0-9.]+)\s*([A-Za-z]+)", value.strip()).groups()
    return (
        float(number)
        * {
            "B": 1 / 1048576,
            "KiB": 1 / 1024,
            "MiB": 1,
            "GiB": 1024,
            "kB": 1000 / 1048576,
            "MB": 1000000 / 1048576,
            "GB": 1000000000 / 1048576,
        }[unit]
    )


def run(state, output, argv):
    from validate_config import validate

    validate(Path(state) / "acceptance.env")
    if not re.fullmatch(r"noc84-[a-z0-9-]+", os.environ.get("NOC_PROJECT", "")):
        raise ValueError("Explicit isolated project required")
    if not argv:
        raise ValueError("Acceptance command required")
    start = time.monotonic()
    samples = {}
    errors = 0
    child = subprocess.Popen(argv, cwd=ROOT)
    while child.poll() is None:
        try:
            ids = subprocess.run(
                [str(ROOT / "scripts/release/manage.sh"), state, "ps", "-q"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            ).stdout.split()
            if ids:
                p = subprocess.run(
                    [
                        "docker",
                        "--host=unix:///var/run/docker.sock",
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{json .}}",
                        *ids,
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                )
                for line in p.stdout.splitlines():
                    d = json.loads(line)
                    name = d["Name"]
                    row = samples.setdefault(
                        name,
                        {
                            "samples": 0,
                            "max_cpu_one_core_percent": 0.0,
                            "max_observed_memory_mib": 0.0,
                        },
                    )
                    row["samples"] += 1
                    row["max_cpu_one_core_percent"] = max(
                        row["max_cpu_one_core_percent"], float(d["CPUPerc"].rstrip("%"))
                    )
                    row["max_observed_memory_mib"] = max(
                        row["max_observed_memory_mib"],
                        round(memory_mib(d["MemUsage"].split("/")[0]), 3),
                    )
        except (subprocess.SubprocessError, ValueError, KeyError, AttributeError):
            errors += 1
        time.sleep(1)
    result = {
        "duration_seconds": round(time.monotonic() - start, 3),
        "command_exit_code": child.returncode,
        "sampling_errors": errors,
        "containers": samples,
        "scope": "isolated Compose during synthetic acceptance; sampled observed maxima, not lifetime peaks or production sizing",
    }
    with open(output, "x", opener=lambda p, f: os.open(p, f, 0o600)) as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result), flush=True)
    if child.returncode:
        raise SystemExit(child.returncode)
    if not samples:
        raise ValueError("No isolated container resource measurements recorded")


if __name__ == "__main__":
    try:
        run(sys.argv[1], sys.argv[2], sys.argv[3:])
    except (ValueError, OSError, IndexError):
        raise SystemExit(
            "Resource acceptance failed; inspect isolated state and retained measurement output"
        )
