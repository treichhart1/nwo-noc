"""Read-only exact Git release/feature manifest; never connects to a device or database."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from integration_manifest import audit, ancestor, git, ROOT, REPLAY

BASELINE = "d46ace336bce97ce52dffdcc4be0abbc203e3f84"
INPUTS = ROOT / "docs/release/NOC-115"
FEATURE_IDS = {
    "mikrotik",
    "rf",
    "ubiquiti-cambium",
    "racom-aviat",
    "tower-health",
    "outages",
    "ipam",
    "equipment",
    "qr",
    "work-orders",
    "maintenance",
    "documents",
    "signup",
    "unified",
    "planning",
    "fiber-designer",
    "roi",
    "grafana",
    "public-status",
    "notifications",
}


def resolve(ref):
    return subprocess.check_output(
        [
            "git",
            "-c",
            f"safe.directory={ROOT}",
            "rev-parse",
            "--verify",
            "--end-of-options",
            ref + "^{commit}",
        ],
        cwd=ROOT,
        text=True,
    ).strip()


def file_record(candidate, path):
    if Path(path).is_absolute() or ".." in Path(path).parts:
        raise ValueError("Only tracked repository paths are permitted")
    data = subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT}", "show", candidate + ":" + path],
        cwd=ROOT,
    )
    entry = git("ls-tree", candidate, "--", path).split()
    if len(entry) != 4 or entry[1] != "blob" or entry[0] not in ("100644", "100755"):
        raise ValueError("Manifest requires regular tracked source files")
    return {
        "path": path,
        "mode": entry[0],
        "blob": entry[2],
        "sha256": hashlib.sha256(data).hexdigest(),
        "last_changed_commit": git("log", "-1", "--format=%H", candidate, "--", path),
    }


def build(candidate="HEAD", *, inputs=INPUTS):
    candidate = resolve(candidate)
    if not ancestor(BASELINE, candidate):
        raise ValueError("Release must preserve the exact reviewed NOC114 candidate")
    legacy = audit(candidate)
    snapshots = json.loads((inputs / "pr-snapshot.json").read_text())
    if {p["number"] for p in snapshots} != set(range(1, 119)):
        raise ValueError("Complete independently reviewed PR1–118 snapshot required")
    prs = []
    for p in snapshots:
        direct = ancestor(p["sha"], candidate)
        if not direct and p["number"] != 86:
            raise ValueError("Missing dependency PR " + str(p["number"]))
        prs.append(
            {
                **p,
                "included_by": "direct_ancestry" if direct else "equivalent_replay",
                "replay": None if direct else REPLAY,
                "default_contains_tip": ancestor(p["sha"], legacy["default_sha"]),
            }
        )
    source_prs = {p["number"]: p for p in prs}
    catalog = json.loads((inputs / "feature-catalog.json").read_text())["features"]
    if len(catalog) != 20 or {f["id"] for f in catalog} != FEATURE_IDS:
        raise ValueError("Complete unique20-module release feature inventory required")
    features = []
    migration_paths = {m["file"] for m in legacy["migrations"]}
    for item in catalog:
        if not set(item["migrations"]) <= migration_paths:
            raise ValueError("Feature references an unreviewed migration")
        p = source_prs[item["source_pr"]]
        paths = {}
        for kind in ("backend", "frontend", "migrations", "tests"):
            if not item[kind]:
                raise ValueError(
                    "Feature inventory cannot omit implementation or test references"
                )
            paths[kind] = [file_record(candidate, path) for path in item[kind]]
        features.append(
            {
                **item,
                "source_commit": p["sha"],
                "source_branch": p["head"],
                "source_tip_scope": "Milestone foundation; per-file last_changed_commit identifies later integration corrections at the selected candidate",
                "files": paths,
                "evidence_scope": "Source presence/provenance only; execution results are separately pinned in evidence.json and final CI",
            }
        )
    commits = []
    for row in git(
        "rev-list", "--reverse", "--topo-order", "--parents", candidate
    ).splitlines():
        parts = row.split()
        commits.append({"sha": parts[0], "parents": parts[1:]})
    wrappers = [
        file_record(candidate, name)
        for name in ("install.sh", "upgrade.sh", "verify.sh")
    ]
    if any(w["mode"] != "100755" for w in wrappers):
        raise ValueError("Documented installer wrappers must be executable")
    return {
        "format": 1,
        "repository": "treichhart1/nwo-noc",
        "candidate": candidate,
        "tree": git("rev-parse", candidate + "^{tree}"),
        "audited_baseline": BASELINE,
        "default_sha": legacy["default_sha"],
        "default_merge_base": legacy["default_merge_base"],
        "commit_count": len(commits),
        "commits": commits,
        "pull_requests": prs,
        "review_order": legacy["review_order"] + [114, 115, 116, 117, 118],
        "pr86_replay_already_present": True,
        "pr86_merge_tree_unchanged": legacy["pr86_merge_tree_unchanged"],
        "features": features,
        "migrations": [
            {**m, **file_record(candidate, m["file"])} for m in legacy["migrations"]
        ],
        "configuration": legacy["configuration"],
        "installer_wrappers": wrappers,
        "audit_input_hashes": {
            name: hashlib.sha256((inputs / name).read_bytes()).hexdigest()
            for name in ("pr-snapshot.json", "feature-catalog.json")
        },
        "external_calls": 0,
        "decision": "NO-GO: high operational/supply-chain gates remain; source presence and green CI are not release approval",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", default="HEAD")
    args = p.parse_args()
    print(json.dumps(build(args.candidate), indent=2))
