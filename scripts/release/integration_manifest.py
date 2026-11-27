"""Read-only pinned stack audit. No merges, network calls or configuration values."""

import argparse
import ast
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "docs/audits/NOC-110-pr-snapshot.json"
REPLAY = "ad61cde731a7707af44734a7261474af3409671e"


def git_argv(*args):
    # Per-command exact checkout trust, matching the approved host installer.
    # Never persist global configuration or trust wildcard/unrelated repositories.
    return ["git", "-c", f"safe.directory={ROOT}", *args]


def git(*args):
    return subprocess.check_output(git_argv(*args), cwd=ROOT, text=True).strip()


def ancestor(a, b):
    r = subprocess.run(git_argv("merge-base", "--is-ancestor", a, b), cwd=ROOT)
    if r.returncode not in (0, 1):
        raise ValueError("Missing history; fetch the reviewed commits before auditing")
    return r.returncode == 0


def patch_id(sha):
    result = subprocess.check_output(
        git_argv("patch-id", "--stable"),
        input=git("show", "--pretty=format:", "--no-ext-diff", sha),
        text=True,
        cwd=ROOT,
    ).split()
    if not result:
        raise ValueError("Expected a nonempty reviewed audit patch")
    return result[0]


def source_text(path, candidate=None):
    return (
        git("show", candidate + ":" + path) if candidate else (ROOT / path).read_text()
    )


def configuration_inventory(candidate=None):
    """Names/types only; never serialize defaults or environment/secret values."""
    tree = ast.parse(source_text("backend/app/core/config.py", candidate))
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Settings"
    )
    return [
        {"environment": n.target.id.upper(), "type": ast.unparse(n.annotation)}
        for n in cls.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    ]


def migration_inventory(candidate=None):
    rows = {}
    files = (
        git(
            "ls-tree", "-r", "--name-only", candidate, "--", "backend/alembic/versions"
        ).splitlines()
        if candidate
        else [
            str(p.relative_to(ROOT))
            for p in (ROOT / "backend/alembic/versions").glob("*.py")
        ]
    )
    for file in sorted(p for p in files if p.endswith(".py")):
        values = {}
        for n in ast.parse(source_text(file, candidate)).body:
            if isinstance(n, ast.Assign):
                for target in n.targets:
                    if isinstance(target, ast.Name) and target.id in (
                        "revision",
                        "down_revision",
                    ):
                        values[target.id] = ast.literal_eval(n.value)
        if not values:
            continue
        revision = values["revision"]
        if revision in rows:
            raise ValueError("Duplicate migration revision")
        rows[revision] = {
            "revision": revision,
            "parent": values["down_revision"],
            "file": file,
        }
    children = {}
    for row in rows.values():
        parent = row["parent"]
        if parent is not None and parent not in rows:
            raise ValueError("Missing migration parent")
        children.setdefault(parent, []).append(row["revision"])
    if any(len(v) != 1 for v in children.values()):
        raise ValueError("Migration branch requires explicit integration review")
    ordered = []
    current = None
    while current in children:
        current = children[current][0]
        if current in [r["revision"] for r in ordered]:
            raise ValueError("Migration cycle")
        ordered.append(rows[current])
    if len(ordered) != len(rows) or current not in {"0028_customer_status", "0029_multi_provider_sms", "0030_native_slack", "0031_wordpress_status"}:
        raise ValueError("Unexpected or disconnected migration history")
    return ordered


def audit(candidate="HEAD", snapshot=SNAPSHOT):
    data = json.loads(Path(snapshot).read_text())
    candidate = git("rev-parse", candidate + "^{commit}")
    baseline = data["baseline"]
    if not ancestor(baseline, candidate):
        raise ValueError("Integration candidate omits the reviewed NOC-109 baseline")
    original = next(p["sha"] for p in data["pull_requests"] if p["number"] == 86)
    if patch_id(original) != patch_id(REPLAY) or not ancestor(REPLAY, candidate):
        raise ValueError("PR86 logical replay differs or is absent")
    # Computes a tree object only: it never updates a branch or creates a merge commit.
    merge_tree = git("merge-tree", "--write-tree", baseline, original).splitlines()[0]
    if merge_tree != git("rev-parse", baseline + "^{tree}"):
        raise ValueError("PR86 has unreconciled source changes")
    rows = []
    for p in data["pull_requests"]:
        direct = ancestor(p["sha"], candidate)
        if not direct and p["number"] != 86:
            raise ValueError("Candidate omits PR " + str(p["number"]))
        rows.append(
            p
            | {
                "direct_ancestor": direct,
                "replay": REPLAY if not direct else None,
                "merge_base_with_candidate": git("merge-base", p["sha"], candidate),
                "default_contains_tip": ancestor(p["sha"], data["default_sha"]),
            }
        )
    return {
        "repository": data["repository"],
        "candidate": candidate,
        "baseline": baseline,
        "default_sha": data["default_sha"],
        "default_merge_base": git("merge-base", data["default_sha"], candidate),
        "baseline_commits_beyond_default": int(
            git("rev-list", "--count", data["default_sha"] + ".." + baseline)
        ),
        "pull_requests": rows,
        "direct_ancestors": sum(r["direct_ancestor"] for r in rows),
        "logical_replays": 1,
        "pr86_merge_tree_unchanged": True,
        "integration_method": "branch from complete NOC109 ancestry; no PR merges or duplicate cherry-picks",
        "review_order": [r["number"] for r in rows],
        "migrations": migration_inventory(candidate),
        "configuration": configuration_inventory(candidate),
        "external_calls": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", default="HEAD")
    args = parser.parse_args()
    print(json.dumps(audit(args.candidate), indent=2))
