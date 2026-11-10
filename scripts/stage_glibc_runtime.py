"""Stage the matched official glibc runtime packages without upgrading the builder."""
from pathlib import Path
import argparse
import shutil
import subprocess
import tempfile

PACKAGES = ("libc6", "libc-gconv-modules-extra")
VERSION = "2.43-7"


def stage(root: Path, archives: Path) -> None:
    # glibc moved its converters into a separate, exactly-versioned package.
    # Mixing the old modules/cache with the new loader is not a valid upgrade.
    for package in PACKAGES:
        archive = archives / f"{package}_{VERSION}_amd64.deb"
        identity = subprocess.check_output(
            ["dpkg-deb", "--show", "--showformat=${Package}\n${Version}\n${Architecture}", str(archive)],
            text=True,
        )
        if identity.splitlines() != [package, VERSION, "amd64"]:
            raise ValueError("Unexpected glibc runtime package identity")
    converters = root / "usr/lib/x86_64-linux-gnu/gconv"
    if converters.exists():
        shutil.rmtree(converters)
    records = root / "var/lib/dpkg/status.d"
    records.mkdir(parents=True, exist_ok=True)
    for package in PACKAGES:
        archive = archives / f"{package}_{VERSION}_amd64.deb"
        subprocess.run(["dpkg-deb", "--extract", str(archive), str(root)], check=True)
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["dpkg-deb", "--control", str(archive), directory], check=True)
            control = Path(directory)
            status = (control / "control").read_text()
            (records / package).write_text(status + "Status: install ok installed\n")
            shutil.copyfile(control / "md5sums", records / (package + ".md5sums"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--archives", required=True, type=Path)
    args = parser.parse_args()
    stage(args.runtime_root, args.archives)


if __name__ == "__main__":
    main()
