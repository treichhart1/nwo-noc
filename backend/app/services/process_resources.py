"""Measured Linux polling-process resources; excludes database/other containers."""

import resource, time
from pathlib import Path


def snapshot():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss = None
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            rss = int(line.split()[1])
            break
    return {
        "cpu_seconds": time.process_time(),
        "rss_kib": rss,
        "process_peak_rss_kib": int(usage.ru_maxrss),
    }


def elapsed(start, wall_seconds):
    end = snapshot()
    cpu = end["cpu_seconds"] - start["cpu_seconds"]
    return {
        "cpu_seconds": round(cpu, 3),
        "cpu_one_core_percent": (
            round(cpu / wall_seconds * 100, 2) if wall_seconds else None
        ),
        "rss_kib": end["rss_kib"],
        "process_peak_rss_kib": end["process_peak_rss_kib"],
        "scope": "Python polling process; peak RSS since process start; excludes PostgreSQL/other containers",
    }
