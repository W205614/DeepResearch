"""Unpack fixed official packages and verify every file against its signed manifest.

Grafana still performs the actual upstream PGP verification when loading plugins.
This helper never substitutes for that check or permits unsigned packages.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile


def checksum(raw):
    return hashlib.sha256(raw).hexdigest()


def unpack(lock, downloads, output):
    packages = json.loads(lock.read_text(encoding="utf-8"))
    if len(packages) != 13 or len({x["slug"] for x in packages}) != 13:
        raise ValueError("Expected the fixed inventory of 13 official plugins")
    all_hashes = {}
    output.mkdir(parents=True, exist_ok=False)
    for package in packages:
        raw = (downloads / package["filename"]).read_bytes()
        if checksum(raw) != package["sha256"]:
            raise ValueError("Official asset checksum mismatch")
        with zipfile.ZipFile(downloads / package["filename"]) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(x.file_size for x in entries) > 512 * 1024 * 1024:
                raise ValueError("Archive exceeds the reviewed extraction bound")
            names = [x.filename for x in entries]
            if len(set(names)) != len(names):
                raise ValueError("Archive contains duplicate paths")
            manifests = [x for x in entries if x.filename.endswith("/MANIFEST.txt")]
            if len(manifests) != 1:
                raise ValueError("Expected one upstream signed manifest")
            prefix = PurePosixPath(manifests[0].filename).parent
            members = {}
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if path.is_absolute() or ".." in path.parts or ":" in entry.filename:
                    raise ValueError("Invalid archive member path")
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError("Unexpected archive symlink")
                if entry.is_dir():
                    continue
                relative = path.relative_to(prefix)
                members[relative.as_posix()] = archive.read(entry)
            text = members["MANIFEST.txt"].decode().replace("\r\n", "\n")
            payload = text.split("\n\n", 1)[1].split("\n-----BEGIN PGP SIGNATURE-----", 1)[0]
            manifest = json.loads(payload)
            if manifest["plugin"] != package["slug"] or manifest["version"] != package["version"]:
                raise ValueError("Plugin identity/version differs from the fixed catalog inventory")
            expected = manifest["files"]
            if set(members) != set(expected) | {"MANIFEST.txt"}:
                raise ValueError("Archive file set differs from the signed manifest")
            for name, raw_member in members.items():
                if name != "MANIFEST.txt" and checksum(raw_member) != expected[name]:
                    raise ValueError("Plugin file checksum mismatch")
                path = output / package["slug"] / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw_member)
                path.chmod(0o755 if name.endswith("_linux_amd64") else 0o644)
                all_hashes[package["slug"] + "/" + name] = checksum(raw_member)
    return all_hashes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("lock", "downloads", "output", "hashes"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = unpack(args.lock, args.downloads, args.output)
    args.hashes.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
