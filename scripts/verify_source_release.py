"""Verify a Git source archive; never build, upload or publish container images."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile


def verify(root: Path, ref: str, output: Path) -> dict:
    sha = subprocess.check_output(["git", "rev-parse", "--verify", ref + "^{commit}"], cwd=root, text=True).strip()
    archive = subprocess.check_output(["git", "archive", "--format=zip", sha], cwd=root)
    required = {"LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", ".env.example", "scripts/local.py"}
    private_names = {".git", ".cache", ".local", ".venv", "node_modules", ".idea"}
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        files = {item.filename: item for item in source.infolist() if not item.is_dir()}
        if not required <= files.keys():
            raise ValueError("Source archive is missing required installation or license files")
        for name, item in files.items():
            path = PurePosixPath(name)
            if (path.is_absolute() or ".." in path.parts or set(path.parts) & private_names
                    or (path.name.startswith(".env") and path.name != ".env.example")
                    or ((item.external_attr >> 16) & 0o170000) == 0o120000):
                raise ValueError("Private path or symbolic link in source archive: " + name)
        digests = {name: hashlib.sha256(source.read(name)).hexdigest() for name in sorted(files)}
    output.mkdir(parents=True, exist_ok=True)
    report = {"source_sha": sha, "file_count": len(files), "archive_sha256": hashlib.sha256(archive).hexdigest(),
              "source_only": True, "file_sha256": digests,
              "boundary": "Tracked source archive only; third-party runtime image distribution is evaluated separately"}
    (output / "source.zip").write_bytes(archive)
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--output", type=Path, default=Path(".cache/source-release"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = verify(root, args.ref, args.output.resolve())
    print(json.dumps({key: value for key, value in report.items() if key != "file_sha256"}))


if __name__ == "__main__":
    main()
