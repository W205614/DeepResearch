"""Stage Python's native libraries and their package records for distroless."""

from pathlib import Path
import shutil
import subprocess


PACKAGES = (
    "libbz2-1.0",
    "libcrypt1",
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


if __name__ == "__main__":
    main()
