"""One-shot private spool ownership migration; never reads/rewrites payloads."""

import argparse, os, re, stat
from pathlib import Path

ALLOWED = re.compile(
    r"^(?:[a-f0-9]{64}\.json|\.writing-[a-zA-Z0-9_]+|(?:integrations|notifications)\.disabled)$"
)


def scan(root, owners=(0, 10001)):
    p = Path(root)
    s = p.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid not in owners:
        raise ValueError("Private owned spool directory required")
    entries = list(p.iterdir())
    if entries and s.st_mode & 0o077:
        raise ValueError("Nonempty spool must already be private")
    if len(entries) > 10000:
        raise ValueError(
            "Spool exceeds bounded migration size; operator review required"
        )
    for item in entries:
        st = item.lstat()
        if (
            not ALLOWED.fullmatch(item.name)
            or not stat.S_ISREG(st.st_mode)
            or st.st_uid not in owners
            or st.st_mode & 0o077
        ):
            raise ValueError(
                "Unexpected spool entry; preserve data for operator review"
            )
    return p, entries


def migrate(root, uid):
    if os.geteuid() != 0 or uid not in (0, 10001):
        raise ValueError("Explicit root helper with approved target UID required")
    p, entries = scan(root)
    for item in entries:
        os.chown(item, uid, uid, follow_symlinks=False)
        os.chmod(item, 0o600, follow_symlinks=False)
    os.chown(p, uid, uid, follow_symlinks=False)
    os.chmod(p, 0o700)
    fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return len(entries)


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("--uid", type=int, required=True)
    args = a.parse_args()
    try:
        print(
            "Private spool metadata migrated; payloads retained:",
            migrate("/var/lib/nwo-noc/monitoring", args.uid),
        )
    except (ValueError, OSError):
        raise SystemExit(
            "Spool migration rejected: retain files; inspect owner/mode/symlinks before retrying"
        )
