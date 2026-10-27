"""Stage Python's native libraries and their package records for distroless."""

from pathlib import Path
import json
import platform
import shutil
import sqlite3
import ssl
import subprocess
from xml.parsers import expat


PACKAGES = (
    "libbz2-1.0",
    "libcrypt1",
    "libdb5.3t64",
    "libffi8",
    "libgdbm6t64",
    "liblzma5",
    "libsqlite3-0",
    "libzstd1",
    "zlib1g",
)


def main() -> None:
    target = Path("/runtime")
    records = target / "var/lib/dpkg/status.d"
    records.mkdir(parents=True)
    for package in PACKAGES:
        files = subprocess.check_output(["dpkg-query", "-L", package], text=True)
        for name in files.splitlines():
            source = Path(name)
            if ".so" not in source.name or not source.is_file():
                continue
            destination = target / source.relative_to("/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination, follow_symlinks=False)
        status = subprocess.check_output(["dpkg-query", "-s", package], text=True)
        (records / package).write_text(status)

    # Interactive terminal bindings need libraries the server does not use.
    extensions = Path("/usr/local/lib/python3.11/lib-dynload")
    for module in ("readline", "_curses", "_curses_panel", "_uuid", "_tkinter"):
        for extension in extensions.glob(f"{module}.*.so"):
            extension.unlink()

    # CPython is built from signed upstream source, not a Debian package. Keep
    # its real identity separate from the OS/PyPI packages Trivy can enumerate.
    identity = {"python_version": platform.python_version(), "openssl": ssl.OPENSSL_VERSION,
                "expat": expat.EXPAT_VERSION, "sqlite": sqlite3.sqlite_version,
                "native_package_records": list(PACKAGES),
                "scanner_boundary": "CPython interpreter/source security is verified independently of OS/PyPI findings"}
    source = Path("/usr/local/share/deepresearch-python-source.json")
    if source.exists():
        identity["signed_source"] = json.loads(source.read_text(encoding="utf-8"))
        if identity["python_version"] != identity["signed_source"]["python_version"]:
            raise RuntimeError("Compiled interpreter differs from the pinned source release")
    Path("/usr/local/share/deepresearch-python-runtime.json").write_text(
        json.dumps(identity, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
