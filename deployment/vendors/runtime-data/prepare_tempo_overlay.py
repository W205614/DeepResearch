"""Package genuine Debian 12 timezone data without changing Tempo's base layers."""
from pathlib import Path
import shutil

from prepare_runtime_trust import prepare, tree_manifest


root = Path("/runtime-root")
source = Path("/package-source")
output = Path("/runtime-overlay")

# COPY merges directories. Prove the new complete tree has no obsolete base
# paths before using an overlay, so old timezone names cannot survive unnoticed.
old_paths = tree_manifest(root, ("usr/share/zoneinfo",))
new_paths = tree_manifest(source, ("usr/share/zoneinfo",))
removed = old_paths.keys() - new_paths.keys()
if removed:
    raise ValueError("Timezone overlay would retain obsolete paths: " + ", ".join(sorted(removed)))

prepare(root, source, "tz", Path("/package-control/control"), "2026f-0+deb12u1")
paths = ["usr/share/zoneinfo", "etc/timezone", "etc/localtime",
         "var/lib/dpkg/status.d/tzdata", "var/lib/dpkg/status.d/tzdata.md5sums",
         "usr/share/deepresearch/tzdata-closure.json"]
if (root / "var/lib/dpkg/status").is_file():
    paths.append("var/lib/dpkg/status")
for relative in paths:
    original, destination = root / relative, output / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if original.is_symlink():
        destination.symlink_to(original.readlink())
    elif original.is_dir():
        shutil.copytree(original, destination, symlinks=True)
    else:
        shutil.copy2(original, destination)
