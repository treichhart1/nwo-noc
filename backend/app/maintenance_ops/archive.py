"""Offline private backup bundle and restore into a NEW directory; never overwrites."""

import hashlib, json, os, re, tarfile, io
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def backup(dump, store, out):
    dump, store, out = map(Path, (dump, store, out))
    if out.exists() or store.is_symlink() or not store.is_dir() or dump.is_symlink():
        raise ValueError("New backup and private regular source paths required")
    files = {}
    for p in store.glob("*/*"):
        if p.name.startswith(".upload-"):
            continue
        if (
            p.is_symlink()
            or not p.is_file()
            or not re.fullmatch(
                "[0-9a-f]{2}/[0-9a-f]{64}", p.relative_to(store).as_posix()
            )
        ):
            raise ValueError("Unexpected file in document store")
        if digest(p) != p.name:
            raise ValueError("Document integrity failure")
        files["blobs/" + p.relative_to(store).as_posix()] = {
            "sha256": p.name,
            "size": p.stat().st_size,
        }
    manifest = {"version": 1, "database_sha256": digest(dump), "files": files}
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f, tarfile.open(fileobj=f, mode="w:gz") as tar:
        data = json.dumps(manifest, sort_keys=True).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        info.mode = 0o600
        tar.addfile(info, io.BytesIO(data))
        tar.add(dump, arcname="database.dump", recursive=False)
        for name in files:
            tar.add(store / name.removeprefix("blobs/"), arcname=name, recursive=False)
    return {"files": len(files), "bundle_sha256": digest(out)}


def restore(bundle, target):
    target = Path(target)
    if target.exists():
        raise ValueError("Restore requires a new directory")
    with tarfile.open(bundle, "r:gz") as tar:
        members = tar.getmembers()
        if (
            len(members) > 100002
            or any(not m.isfile() for m in members)
            or len({m.name for m in members}) != len(members)
        ):
            raise ValueError("Invalid archive entries")
        info = tar.getmember("manifest.json")
        if info.size > 20000000:
            raise ValueError("Manifest bound exceeded")
        manifest = json.load(tar.extractfile(info))
        if manifest.get("version") != 1:
            raise ValueError("Unknown bundle version")
        expected = {"manifest.json", "database.dump", *manifest["files"]}
        if {m.name for m in members} != expected:
            raise ValueError("Unexpected archive paths")
        for name, meta in manifest["files"].items():
            if (
                not re.fullmatch("blobs/[0-9a-f]{2}/[0-9a-f]{64}", name)
                or name.split("/")[1] != name.rsplit("/", 1)[-1][:2]
                or meta["sha256"] != name.rsplit("/", 1)[-1]
                or tar.getmember(name).size != meta["size"]
                or meta["size"] > 10485760
            ):
                raise ValueError("Unsafe document path/size")
        if tar.getmember("database.dump").size > 10737418240:
            raise ValueError("Database backup size bound")
        target.mkdir(mode=0o700, parents=False)
        for name in ["database.dump", *manifest["files"]]:
            dest = target / name
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with open(
                dest, "xb", opener=lambda p, f: os.open(p, f, 0o600)
            ) as out, tar.extractfile(name) as source:
                for chunk in iter(lambda: source.read(1048576), b""):
                    out.write(chunk)
            expected_sha = (
                manifest["database_sha256"]
                if name == "database.dump"
                else manifest["files"][name]["sha256"]
            )
            if digest(dest) != expected_sha:
                raise ValueError(
                    "Restored checksum mismatch; retain failed target and retry at new path"
                )
        (target / "database.dump.sha256").write_text(
            digest(target / "database.dump")
            + "  "
            + str((target / "database.dump").absolute())
            + "\n"
        )
    return {
        "files": len(manifest["files"]),
        "target": str(target),
        "database_restore": "Use existing isolated restore.sh with database.dump and a new restore_NAME database",
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["backup", "restore"])
    p.add_argument("--dump")
    p.add_argument("--store")
    p.add_argument("--bundle", required=True)
    p.add_argument("--target")
    a = p.parse_args()
    print(
        json.dumps(
            backup(a.dump, a.store, a.bundle)
            if a.action == "backup"
            else restore(a.bundle, a.target)
        )
    )
