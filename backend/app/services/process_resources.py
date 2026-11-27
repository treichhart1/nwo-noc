"""Measured Linux polling-process resources; excludes database/other containers."""

import resource, time
from pathlib import Path


def counters(root=Path("/sys/fs/cgroup"), proc=Path("/proc")):
    """Optional Linux diagnostics; absence is explicit, never fabricated zeros.

    Cgroup files describe this polling container. /proc/pressure describes the
    host and cannot establish which other process caused a stall.
    """
    result = {"cgroup_v2": {}, "host_pressure": {}}
    for name in ("cpu.stat", "memory.events", "memory.current", "memory.peak", "io.stat"):
        try:
            lines = (root / name).read_text().splitlines()
            if name == "io.stat":
                value = {row.split()[0]: dict(item.split("=", 1) for item in row.split()[1:]) for row in lines}
                value = {device: {k: int(v) for k, v in values.items()} for device, values in value.items()}
            elif name in {"memory.current", "memory.peak"}:
                value = int(lines[0])
            else:
                value = {k: int(v) for k, v in (row.split() for row in lines)}
            result["cgroup_v2"][name] = value
        except (OSError, ValueError, IndexError):
            result["cgroup_v2"][name] = None
    for name in ("cpu", "io", "memory"):
        try:
            result["host_pressure"][name] = {
                row.split()[0]: int(dict(item.split("=", 1) for item in row.split()[1:])["total"])
                for row in (proc / "pressure" / name).read_text().splitlines()
            }
        except (OSError, ValueError, KeyError):
            result["host_pressure"][name] = None
    return result


def counter_delta(before, after):
    if isinstance(before, dict) and isinstance(after, dict):
        return {key: counter_delta(before.get(key), value) for key, value in after.items()}
    if isinstance(before, int) and isinstance(after, int) and after >= before:
        return after - before
    return None


def snapshot():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss = None
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            rss = int(line.split()[1])
            break
    return {
        "diagnostics": counters(),
        "cpu_seconds": time.process_time(),
        "rss_kib": rss,
        "process_peak_rss_kib": int(usage.ru_maxrss),
    }


def elapsed(start, wall_seconds):
    end = snapshot()
    cpu = end["cpu_seconds"] - start["cpu_seconds"]
    return {
        "diagnostic_start": start["diagnostics"],
        "diagnostic_end": end["diagnostics"],
        "diagnostic_delta": counter_delta(start["diagnostics"], end["diagnostics"]),
        "cpu_seconds": round(cpu, 3),
        "cpu_one_core_percent": (
            round(cpu / wall_seconds * 100, 2) if wall_seconds else None
        ),
        "rss_kib": end["rss_kib"],
        "process_peak_rss_kib": end["process_peak_rss_kib"],
        "scope": "Python polling process; peak RSS since process start; excludes PostgreSQL/other containers",
    }
