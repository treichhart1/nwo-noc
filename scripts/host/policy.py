"""Pure release/host packaging validation; no network or device operations."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat


class Reject(ValueError):
    """Safe operator-facing validation error; never contains credentials."""


REPOSITORY = "treichhart1/nwo-noc"
BASELINE = "b85d1af7ca1e2de1d449dac6ab1c40131012b308"
SHA = re.compile(r"[a-f0-9]{40}")
DIGEST = re.compile(r"[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}")
LOCAL = re.compile(r"sha256:[a-f0-9]{64}")
LOCAL_OPTIONS = {
    "PLANNING_WORKER_ENABLED",
    "MAINTENANCE_SCHEDULER_ENABLED",
    "TOWER_HEALTH_ENABLED",
    "DOWN_DETECTOR_ENABLED",
}

DATA_DIRS = ("monitoring", "documents", "signup", "planning")
DISABLED = (
    "UISP_ENABLED",
    "MIKROTIK_ENABLED",
    "INTEGRATIONS_ADMIN_APPROVED",
    "RF_COLLECTION_ENABLED",
    "RF_SNMP_ENABLED",
    "RF_MICROWAVE_ENABLED",
    "RF_SCHEDULER_ENABLED",
    "SYNTHETIC_MONITORING_ENABLED",
    "POWERCODE_LIVE_ENABLED",
    "POWERCODE_FIXTURE_ENABLED",
    "NOTIFICATIONS_LIVE_DELIVERY_ENABLED",
    "SMS_NATIVE_TRANSPORT_ENABLED",
    "SMS_NATIVE_WORKER_ENABLED",
    "SMS_CALLBACK_GATEWAY_ENABLED",
    "SMS_EGRESS_APPROVED",
    "NOTIFICATIONS_WORKER_ENABLED",
    "NOTIFICATIONS_AUTO_DRAFTS_ENABLED",
    "SIGNUP_PUBLIC_ENABLED",
    "SIGNUP_PUBLIC_TEST_MODE",
    "FIBER_DESIGNER_WORKER_ENABLED",
    "FIBER_DESIGNER_INGEST_ENABLED",
    "GRAFANA_LIVE_HEALTH_ENABLED",
    "GRAFANA_EMBED_APPROVED",
    "IPAM_DISCOVERY_ENABLED",
    "API_DOCS_ENABLED",
    "ANALYTICS_ROLLUP_ENABLED",
    "MAINTENANCE_SCHEDULER_ENABLED",
    "PLANNING_WORKER_ENABLED",
    "RF_HISTORY_PRUNING_ENABLED",
    "RF_ANALYTICS_RETENTION_ENABLED",
    "TOWER_HEALTH_ENABLED",
    "DOWN_DETECTOR_ENABLED",
    "RF_ANALYTICS_PROJECTION_ENABLED",
)


def private_file(path, *, owner=0, max_bytes=1048576):
    path = Path(path)
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != owner
        or parent.st_mode & 0o022
    ):
        raise Reject(
            "File parent must be owned by the privileged operator and not writable by others"
        )
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        st = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != owner
            or st.st_mode & 0o077
            or st.st_size > max_bytes
        ):
            raise Reject("Private regular operator-owned file (0600) required")
        return handle.read()


def approval(path, *, controlled=False, source=None):
    p = Path(path)
    if source and p.resolve().is_relative_to(Path(source).resolve()):
        raise Reject("Release approval must be outside the source repository")
    raw = private_file(p)
    if not controlled:
        from artifacts import verify_signature
        try:
            verify_signature(raw, private_file(Path(str(p) + ".sig")),
                             private_file(Path("/etc/nwo-noc-release.pub")))
        except (OSError, ValueError):
            raise Reject("Signed release approval and protected independently trusted Ed25519 key required") from None
    data = json.loads(raw)
    if data.get("format") != 1 or data.get("repository") != REPOSITORY:
        raise Reject("Unsupported release approval contract/repository")
    if not SHA.fullmatch(data.get("commit", "")) or not SHA.fullmatch(
        data.get("tree", "")
    ):
        raise Reject("Exact approved source commit and tree required")
    if (
        not re.fullmatch(
            r"[A-Za-z0-9_.:/ -]{3,160}", data.get("approval_reference", "")
        )
        or "REPLACE" in data["approval_reference"]
    ):
        raise Reject("Explicit operator release approval reference required")
    if data.get("purpose") != ("controlled-test" if controlled else "release"):
        raise Reject(
            "Approval purpose mismatch; test artifacts cannot become production releases"
        )
    if not re.fullmatch(r"[a-zA-Z0-9_]{1,64}", data.get("migration_head", "")):
        raise Reject("Approved migration head required")
    if not isinstance(data.get("upgrade_from", []), list) or any(
        not SHA.fullmatch(v) for v in data.get("upgrade_from", [])
    ):
        raise Reject("Explicit supported upgrade source commits required")
    keys = set(data.get("images", {}))
    if keys not in ({"backend", "frontend"}, {"backend", "frontend", "postgres"}, {"backend", "frontend", "postgres", "redis"}) or (not controlled and "postgres" not in keys):
        raise Reject("Approved backend, frontend and hardened PostgreSQL images required for releases")
    if not controlled and keys != {"backend", "frontend", "postgres", "redis"}:
        raise Reject("Complete signed runtime image set including Redis required")
    for ref in data["images"].values():
        if not isinstance(ref, str) or not (
            DIGEST.fullmatch(ref) or controlled and LOCAL.fullmatch(ref)
        ):
            raise Reject(
                "Immutable release image digest required; local IDs only in explicit controlled tests"
            )
    if not controlled:
        from evidence import verify_release_evidence
        verify_release_evidence(data, p, private_file)
    return data


def validate_env(values):
    if values.get("APP_ENV") != "production" or values.get("OFFLINE_MODE") != "true":
        raise Reject(
            "Packaged core requires production application mode with external access disabled"
        )
    if any(values.get(k) != "false" for k in DISABLED if k not in LOCAL_OPTIONS):
        raise Reject(
            "Optional integrations/jobs must be explicitly disabled for this package"
        )
    if (
        values.get("SIGNUP_TURNSTILE_MODE") != "disabled"
        or values.get("SIGNUP_GEOCODER") != "disabled"
        or values.get("RF_EXPLANATION_PROVIDER") != "disabled"
    ):
        raise Reject("External providers must be disabled")
    for key in ("SECRET_KEY", "ADMIN_PASSWORD"):
        if not re.fullmatch(r"[a-f0-9]{64}", values.get(key, "")):
            raise Reject("Generated application secrets required")
    if not re.fullmatch(
        r"postgresql\+psycopg://noc_app:[a-f0-9]{48}@db:5432/nwo_noc",
        values.get("DATABASE_URL", ""),
    ):
        raise Reject("Restricted internal application database required")


def password(value):
    if not 16 <= len(value.encode("utf-8")) <= 72 or any(
        c in value for c in "\n\r\x00"
    ):
        raise Reject(
            "Administrator password must be16–72 UTF-8 bytes without control separators"
        )
    return value


def resources(*, cpus, memory, disk, rehearsal=False):
    minimum = (
        (1, 256 * 1024**2, 1024**3) if rehearsal else (2, 4 * 1024**3, 10 * 1024**3)
    )
    for label, actual, required in zip(
        ("CPU cores", "RAM bytes", "free disk bytes"), (cpus, memory, disk), minimum
    ):
        if actual < required:
            raise Reject(
                f"Insufficient {label}: need at least {required}; no installation data changed"
            )


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def safe_members(archive):
    members = archive.getmembers()
    if len(members) > 20000 or sum(m.size for m in members) > 128 * 1024**2:
        raise Reject("Source archive exceeds reviewed release budget")
    for member in members:
        if (
            Path(member.name).is_absolute()
            or ".." in Path(member.name).parts
            or not (member.isfile() or member.isdir())
        ):
            raise Reject("Unsafe source archive path or link")
    return members


def ubuntu_candidate(text):
    candidate = re.search(r"Candidate:\s*(\S+)", text)
    if not candidate or candidate[1] == "(none)":
        raise Reject("Required Ubuntu24.04 package candidate unavailable")
    block = re.search(
        r"(?ms)^\s*(?:\*\*\*\s*)?"
        + re.escape(candidate[1])
        + r"\s+\d+\n(.*?)(?=^\s*(?:\*\*\*\s*)?\S+\s+\d+\s*$|\Z)",
        text,
    )
    if not block or not re.search(
        r"https?://(?:[a-zA-Z0-9.-]+\.)?ubuntu\.com/[^\s]*\s+noble(?:-updates|-security|-backports)?/",
        block[1],
    ):
        raise Reject(
            "Candidate must come from an official signed Ubuntu noble repository; existing repository configuration is not overwritten"
        )
    return candidate[1]


def migration_environment(values, owner_password):
    if not re.fullmatch(r"[a-f0-9]{48}", owner_password) or values != {
        "DATABASE_URL": f"postgresql+psycopg://noc_owner:{owner_password}@db:5432/nwo_noc"
    }:
        raise Reject(
            "Migration credentials must target only the dedicated internal NOC owner/database"
        )


CPU_FEATURES_V2 = frozenset({"pni", "ssse3", "sse4_1", "sse4_2", "popcnt", "cx16", "lahf_lm"})


def require_cpu_features(cpuinfo):
    processors = [set(line.split(":", 1)[1].split())
                  for line in cpuinfo.splitlines()
                  if line.split(":", 1)[0].strip() == "flags" and ":" in line]
    if not processors or any(not CPU_FEATURES_V2 <= flags for flags in processors):
        raise Reject(
            "x86-64-v2 CPU required by the locked NumPy/GIS wheels; verify SSE3/SSSE3, "
            "SSE4.1/SSE4.2, POPCNT, CX16 and LAHF support on every vCPU. "
            "Select an approved compatible virtual CPU model before installation; no data changed"
        )
