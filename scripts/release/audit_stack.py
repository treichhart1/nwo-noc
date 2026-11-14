"""Read-only ancestry/patch-equivalence audit of the reviewed GitHub snapshot."""

import json, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "docs/audits/NOC-93-pr-snapshot.json"


def git(*args, check=True):
    return subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=check
    )


def patch_id(sha):
    diff = git("show", "--pretty=format:", "--no-ext-diff", sha).stdout
    p = subprocess.run(
        ["git", "patch-id", "--stable"],
        input=diff,
        text=True,
        capture_output=True,
        check=True,
    )
    return p.stdout.split()[0] if p.stdout.strip() else None


def audit(candidate="HEAD"):
    snapshot = json.loads(SNAPSHOT.read_text())
    rows = []
    replay = "ad61cde"
    original = next(p["sha"] for p in snapshot["pull_requests"] if p["number"] == 86)
    equivalent = patch_id(original) == patch_id(replay)
    if not equivalent:
        raise ValueError("NOC-82 replay differs; release ancestry requires review")
    for p in snapshot["pull_requests"]:
        fetched = git("rev-parse", "refs/remotes/origin/" + p["head"], check=False)
        if fetched.returncode or fetched.stdout.strip() != p["sha"]:
            raise ValueError(
                "Upstream branch moved or is missing; refresh and review the pinned snapshot"
            )
        included = (
            git(
                "merge-base", "--is-ancestor", p["sha"], candidate, check=False
            ).returncode
            == 0
        )
        logical = (
            p["number"] == 86
            and equivalent
            and git(
                "merge-base", "--is-ancestor", replay, candidate, check=False
            ).returncode
            == 0
        )
        rows.append(
            p
            | {
                "direct_ancestor": included,
                "patch_equivalent_replay": (
                    git("rev-parse", replay).stdout.strip() if logical else None
                ),
                "included": included or logical,
            }
        )
    if not all(p["included"] for p in rows):
        raise ValueError("Candidate omits reviewed upstream work")
    return {
        "candidate": git("rev-parse", candidate).stdout.strip(),
        "baseline": snapshot["baseline"],
        "prs": len(rows),
        "direct_ancestors": sum(p["direct_ancestor"] for p in rows),
        "logical_replays": sum(bool(p["patch_equivalent_replay"]) for p in rows),
        "rows": rows,
        "merge_order": [p["number"] for p in rows],
        "warning": "#86 is patch-equivalent, not a direct ancestor. Reconcile its ancestry before merging; do not reapply its audit. No merges performed.",
    }


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
