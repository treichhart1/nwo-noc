"""Gate every Bandit result against an exact reviewed source fingerprint; no broad ignores."""

import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]


def fingerprint(result):
    path = Path(result["filename"]).resolve().relative_to(ROOT).as_posix()
    code = re.sub(r"(?m)^\s*\d+ ", "", result["code"]).strip()
    value = "\n".join([path, result["test_id"], result["issue_severity"], code])
    return hashlib.sha256(value.encode()).hexdigest()


def check_report(report, reviews):
    if report.get("errors"):
        raise ValueError("Static scan omitted files; audit incomplete")
    accepted = {
        r["fingerprint"] for r in reviews["reviewed_results"] if r.get("justification")
    }
    seen = set()
    for result in report["results"]:
        key = fingerprint(result)
        if result["issue_severity"] == "HIGH" or key not in accepted:
            raise ValueError(
                "New or high-severity static result requires source review"
            )
        seen.add(key)
    if seen != accepted:
        raise ValueError(
            "Stale triage inventory; review removed or changed source findings"
        )
    return {
        "reviewed_static_results": len(seen),
        "unreviewed": 0,
        "high_severity_results": 0,
    }


if __name__ == "__main__":
    report = json.loads(Path(sys.argv[1]).read_text())
    reviews = json.loads((ROOT / "docs/audits/NOC-111-bandit-review.json").read_text())
    print(json.dumps(check_report(report, reviews)))
