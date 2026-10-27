"""Bind reviewed backend replacements to exact original manifests and build hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Upstream source shape changed; review the patch again")
    return text.replace(old, new, 1)


def generate(inventory, upstream, rebuilt, source, output):
    modules = json.loads(inventory.read_text(encoding="utf-8"))
    if len(modules) != 8:
        raise ValueError("The reviewed patch set contains exactly 8 backend plugins")
    entries, receipt = {}, []
    for module in modules:
        directory, binary = module["plugin_directory"], module["binary"]
        if not re.fullmatch(r"[a-z0-9_-]+", directory) or not re.fullmatch(r"gpx_[a-z0-9_-]+_linux_amd64", binary):
            raise ValueError("Unexpected reviewed backend path")
        manifest_file = upstream / directory / "MANIFEST.txt"
        raw = manifest_file.read_text(encoding="utf-8")
        # Signature verification remains in Grafana before this override is used.
        plaintext = raw.split("\n\n", 1)[1].split("\n-----BEGIN PGP SIGNATURE-----", 1)[0]
        manifest = json.loads(plaintext)
        original = manifest["files"][binary]
        replacement = rebuilt / directory / binary
        if replacement.is_symlink() or not replacement.is_file():
            raise ValueError("Missing regular rebuilt backend")
        with replacement.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                raise ValueError("Replacement is not an ELF executable")
        key = manifest["plugin"] + "/" + binary
        if key in entries:
            raise ValueError("Duplicate backend override")
        entries[key] = [original, digest(replacement), digest(manifest_file)]
        receipt.append({**module, "plugin_id": manifest["plugin"],
                        "original_backend_sha256": original,
                        "rebuilt_backend_sha256": entries[key][1],
                        "original_manifest_sha256": entries[key][2]})
    generated = "package signature\n\nvar reviewedBackendHashes = map[string]reviewedBackend{\n"
    for key, hashes in sorted(entries.items()):
        generated += json.dumps(key) + ": {" + ", ".join(map(json.dumps, hashes)) + "},\n"
    generated += "}\n"
    output.mkdir(parents=True, exist_ok=True)
    (output / "reviewed_backend_hashes.go").write_text(generated, encoding="utf-8")
    text = source.read_text(encoding="utf-8")
    text = replace_once(text, "\tmanifestFiles := make(map[string]struct{}, len(manifest.Files))",
                        "\tmaintainerRebuilt := false\n\tmanifestFiles := make(map[string]struct{}, len(manifest.Files))")
    text = replace_once(text, "\t\terr = verifyHash(s.log, plugin, p, hash)\n\t\tif err != nil {",
                        "\t\terr = verifyHash(s.log, plugin, p, hash)\n"
                        "\t\tif err != nil {\n"
                        "\t\t\tif replacement, ok := reviewedBackendHash(plugin, p, hash); ok {\n"
                        "\t\t\t\terr = verifyHash(s.log, plugin, p, replacement)\n"
                        "\t\t\t\tif err == nil { maintainerRebuilt = true }\n"
                        "\t\t\t}\n\t\t}\n\t\tif err != nil {")
    old = "\treturn plugins.Signature{\n\t\tStatus:     plugins.SignatureStatusValid,\n\t\tType:       manifest.SignatureType,\n\t\tSigningOrg: manifest.SignedByOrgName,\n\t}, nil"
    new = "\tresult := plugins.Signature{\n\t\tStatus: plugins.SignatureStatusValid,\n\t\tType: manifest.SignatureType,\n\t\tSigningOrg: manifest.SignedByOrgName,\n\t}\n\tif maintainerRebuilt {\n\t\tresult.Type = plugins.SignatureTypePrivate\n\t\tresult.SigningOrg = \"DeepResearch maintainer rebuild\"\n\t\ts.log.Info(\"Maintainer backend hash accepted; upstream signature covers original assets only\", \"pluginId\", plugin.JSONData.ID)\n\t}\n\treturn result, nil"
    text = replace_once(text, old, new)
    (output / "manifest.go").write_text(text, encoding="utf-8")
    (output / "rebuild-provenance.json").write_text(json.dumps({"trust_boundary": "original upstream PGP assets plus compiled maintainer backend hashes; modified binaries are not upstream signed", "backends": receipt}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inventory", "upstream", "rebuilt", "source", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    generate(args.inventory, args.upstream, args.rebuilt, args.source, args.output)
