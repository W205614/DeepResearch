"""Verify every retained signed asset byte before replacing all thirteen backends."""
import hashlib
import json
from pathlib import Path

root = Path("/reviewed-plugins")
inventory = json.loads(Path("/tools/plugin-modules.json").read_bytes())
result = {}
if {p.name for p in root.iterdir()} != {m["plugin_directory"] for m in inventory}:
    raise ValueError("Full plugin inventory changed")
for module in inventory:
    directory = root / module["plugin_directory"]
    raw = (directory / "MANIFEST.txt").read_text()
    manifest = json.loads(raw.split("\n\n", 1)[1].split("\n-----BEGIN PGP SIGNATURE-----", 1)[0])
    actual = {p.relative_to(directory).as_posix(): p for p in directory.rglob("*") if p.is_file()}
    if set(actual) != set(manifest["files"]) | {"MANIFEST.txt"}:
        raise ValueError("Complete signed plugin asset inventory changed")
    for name, path in actual.items():
        digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
        if name not in ("MANIFEST.txt", module["binary"]) and digest != manifest["files"][name]:
            raise ValueError("Unmodified signed plugin asset hash mismatch")
        result[module["plugin_directory"] + "/" + name] = digest
Path("/official-asset-hashes.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
