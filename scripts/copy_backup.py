"""Copy an existing cold backup to an explicitly selected mounted remote path.

Never stops services, overwrites a backup, or sends data to an inferred location.
The caller must mount/protect the destination and verify it is a different host.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_verified(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_dir() or not (source / "manifest.json").is_file():
        raise ValueError("Source must be a completed backup with manifest.json")
    if destination.exists() or source == destination or source in destination.parents:
        raise ValueError("Destination must be a new directory outside the source")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != 2 or not manifest.get("files"):
        raise ValueError("A nonempty version 2 manifest is required")
    for name, digest in manifest["files"].items():
        path = (source / name).resolve()
        if path.parent != source or not path.is_file() or checksum(path) != digest:
            raise ValueError("Source backup checksum/path validation failed")
    files = list(source.rglob("*"))
    if any(path.is_symlink() for path in files):
        raise ValueError("Backup symlinks are not allowed")
    shutil.copytree(source, destination)
    for path in files:
        if path.is_file() and checksum(path) != checksum(destination / path.relative_to(source)):
            raise RuntimeError("Backup copy checksum mismatch; destination is not valid")
    return len([path for path in files if path.is_file()])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    print(f"Verified {copy_verified(args.source, args.destination)} copied files")
