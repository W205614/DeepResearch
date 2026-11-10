"""Bind the custom BusyBox output to its exact sources, config and ELF dependencies."""

import hashlib
import json
from pathlib import Path
import re


def configuration(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if line.startswith("CONFIG_"))


recipe = Path("/recipe")
out = Path("/out")
assert configuration(recipe / "busybox.config") == configuration(out / "metadata/busybox.config"), (
    "The build changed configured BusyBox features"
)
needed = re.findall(r"\(NEEDED\).*\[(.*?)\]", (out / "metadata/elf-dynamic.txt").read_text())
assert needed == ["libc.musl-x86_64.so.1"], needed
lock = json.loads((recipe / "source.lock.json").read_text())
metadata = {
    "component": lock["component"],
    "architecture": lock["architecture"],
    "custom_build": True,
    "official_alpine_revision": False,
    "source_date_epoch": lock["source_date_epoch"],
    "busybox_sha256": hashlib.sha256((out / "busybox").read_bytes()).hexdigest(),
    "config_sha256": hashlib.sha256((out / "metadata/busybox.config").read_bytes()).hexdigest(),
    "patched_wget_sha256": hashlib.sha256(
        Path("/work/source-static/networking/wget.c").read_bytes()
    ).hexdigest(),
    "source_lock_sha256": hashlib.sha256((recipe / "source.lock.json").read_bytes()).hexdigest(),
    "dt_needed": needed,
    "licenses": ["GPL-2.0-only", "ISC (utmps, skalibs)"],
    "corresponding_source": "../corresponding-source/",
    "scope": "Fixed code for CVE-2025-60876; not an assertion that raw scanner findings are zero.",
}
(out / "metadata/build.json").write_text(json.dumps(metadata, indent=2) + "\n")
