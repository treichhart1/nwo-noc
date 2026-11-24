"""Verify retained NOC119 ZIP bytes and inspect bounded JSON without extraction."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile


def inspect_archive(path, expected):
    raw = Path(path).read_bytes()
    if len(raw) != expected["size_in_bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        raise ValueError("Historical artifact digest/size mismatch")
    report = {"artifact_id": expected["id"], "name": expected["name"], "sha256": expected["sha256"],
              "archive_digest_verified": True, "json_files": [], "acceptance": [], "benchmarks": [], "scan_summaries": []}
    with zipfile.ZipFile(Path(path)) as archive:
        entries = archive.infolist()
        names = set()
        if len(entries) > 1000 or sum(i.file_size for i in entries) > 256 * 1024 * 1024:
            raise ValueError("Historical artifact resource limit")
        for entry in entries:
            name = PurePosixPath(entry.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in entry.filename or entry.filename in names or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Unsafe historical artifact path")
            names.add(entry.filename)
            if entry.file_size > 64 * 1024 * 1024:
                raise ValueError("Historical JSON resource limit")
            if not entry.filename.endswith(".json"):
                continue
            data = archive.read(entry)
            doc = json.loads(data)
            report["json_files"].append({"path": entry.filename, "sha256": hashlib.sha256(data).hexdigest()})
            if isinstance(doc, dict) and {"installation", "recovery", "source_powered_off_during_restore"} <= doc.keys():
                report["acceptance"].append({k: doc.get(k) for k in ("installation", "recovery", "source_powered_off_during_restore", "source_reboot", "receiver_reboot")})
            if isinstance(doc, dict) and "records" in doc and "target_status" in doc:
                rounds = [r for record in doc["records"] if record.get("devices") == 1000 for r in record["rounds"]]
                report["benchmarks"].append({"rounds": len(rounds), "min_seconds": min(r["elapsed_seconds"] for r in rounds),
                    "max_seconds": max(r["elapsed_seconds"] for r in rounds), "over15": sum(r["elapsed_seconds"] > 15 for r in rounds),
                    "target_status": doc["target_status"],
                    "counts_preserved": all(r["samples_and_unique_receipts"] == r["devices"] * 6 for r in doc["records"])})
            if isinstance(doc, list) and doc and all(isinstance(v, dict) and {"image_id", "high", "critical", "total"} <= v.keys() for v in doc):
                report["scan_summaries"].append({"path": entry.filename, "images": [{k:v[k] for k in ("image_id", "high", "critical", "total")} for v in doc]})
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    ledger = json.loads((Path(__file__).resolve().parents[2] / "docs/release/NOC-120/NOC-119-EVIDENCE.json").read_text())
    reports = [inspect_archive(args.directory / (str(a["id"]) + ".zip"), a) for a in ledger["artifacts"]]
    result = {k: ledger[k] for k in ("repository", "source_commit", "run_id", "run_number", "scope")}
    result["archives"] = reports
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
