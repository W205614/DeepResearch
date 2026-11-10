"""Read only runtime identity and component contracts in an isolated Agent image."""

import hashlib
import json
import locale
import os
from pathlib import Path
import sqlite3
import stat
import sys


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    expected = json.load(sys.stdin)
    payload_mismatches = []
    for item in expected["official_glibc_payload"]:
        path = Path(item["path"])
        if "target" in item:
            matches = path.is_symlink() and os.readlink(path) == item["target"]
        else:
            matches = (
                path.is_file()
                and not path.is_symlink()
                and digest(path) == item["sha256"]
                and stat.S_IMODE(path.stat().st_mode) == item["mode"]
            )
        if not matches:
            payload_mismatches.append(item["path"])

    native = []
    locale_trees = []
    recover = []
    for directory, subdirs, files in os.walk("/", followlinks=False):
        if directory == "/":
            subdirs[:] = [name for name in subdirs if name not in {"proc", "sys", "dev", "tmp"}]
        if directory in {"/usr/lib/locale", "/usr/share/locale"} or directory.endswith("/LC_MESSAGES"):
            locale_trees.append(directory)
        for name in files:
            path = Path(directory) / name
            if name == "locale-archive":
                locale_trees.append(str(path))
            if "bzip2recover" in name:
                recover.append(str(path))
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as stream:
                is_elf = stream.read(4) == b"\x7fELF"
            if is_elf:
                native.append({"path": str(path), "sha256": digest(path)})
    native.sort(key=lambda item: item["path"])

    python = []
    for root in ["/app", "/opt/venv/lib/python3.11/site-packages", "/usr/local/lib/python3.11"]:
        for directory, subdirs, files in os.walk(root, followlinks=False):
            subdirs[:] = [name for name in subdirs if name != "__pycache__"]
            for name in files:
                path = Path(directory) / name
                if name.endswith(".py") and path.is_file() and not path.is_symlink():
                    python.append({"path": str(path), "sha256": digest(path)})
    python.sort(key=lambda item: item["path"])

    connection = sqlite3.connect(":memory:")
    extension_errors = []
    for query in [
        "SELECT * FROM zipfile('/tmp/nonexistent.zip')",
        "SELECT zipfile('x')",
        "SELECT load_extension('/tmp/nonexistent.so')",
    ]:
        try:
            connection.execute(query).fetchall()
        except sqlite3.OperationalError as error:
            extension_errors.append(str(error))
        else:
            extension_errors.append("UNEXPECTED_SUCCESS")
    connection.close()
    loaded = {line.split()[-1] for line in Path("/proc/self/maps").read_text().splitlines()}
    sqlite_libraries = sorted(
        [
            {"path": path, "sha256": digest(Path(path))}
            for path in loaded
            if Path(path).name.startswith("libsqlite3.so.")
        ],
        key=lambda item: item["path"],
    )
    print(
        json.dumps(
            {
                "native_elf_closure": {"files": len(native), "sha256": canonical_digest(native)},
                "python_source_closure": {"files": len(python), "sha256": canonical_digest(python)},
                "official_glibc_items": len(expected["official_glibc_payload"]),
                "official_glibc_mismatches": payload_mismatches,
                "locale_trees": locale_trees,
                "lc_messages": locale.setlocale(locale.LC_MESSAGES, None),
                "bzip2recover": recover,
                "sqlite": {
                    "version": sqlite3.sqlite_version,
                    "loaded": sqlite_libraries,
                    "extension_errors": extension_errors,
                },
                "uid": os.getuid(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
