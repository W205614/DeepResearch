import hashlib
import json
from pathlib import Path
import types

import pytest


def helper():
    path = Path(__file__).resolve().parents[1] / "deployment/vendors/grafana/prepare_rebuild.py"
    # The approved recipe inventory includes exact frozen bytes. Importing via
    # SourceFileLoader can rewrite its timestamped cache on a new checkout.
    # Compile the actual source in memory so this reader cannot mutate inputs.
    module = types.ModuleType("grafana_rebuild")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def test_reader_preserves_all_frozen_recipe_bytes():
    root = Path(__file__).resolve().parents[1] / "deployment/vendors/grafana"
    def fingerprint():
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*") if p.is_file()}
    before = fingerprint()
    helper()
    assert fingerprint() == before


def inputs(tmp_path):
    modules = []
    for i in range(8):
        name, binary = f"fixture{i}", "gpx_fixture_linux_amd64"
        upstream, rebuilt = tmp_path / "upstream" / name, tmp_path / "rebuilt" / name
        upstream.mkdir(parents=True)
        rebuilt.mkdir(parents=True)
        manifest = {"plugin": name, "files": {binary: hashlib.sha256(b"original").hexdigest()}}
        (upstream / "MANIFEST.txt").write_text("-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\n" + json.dumps(manifest) + "\n-----BEGIN PGP SIGNATURE-----\nfixture", encoding="utf-8")
        (rebuilt / binary).write_bytes(b"\x7fELFfixture")
        modules.append({"plugin_directory": name, "binary": binary, "module": "official/fixture", "version": "fixed"})
    inventory = tmp_path / "modules.json"
    inventory.write_text(json.dumps(modules), encoding="utf-8")
    source = tmp_path / "manifest.go"
    source.write_text("\tmanifestFiles := make(map[string]struct{}, len(manifest.Files))\n"
                      "\t\terr = verifyHash(s.log, plugin, p, hash)\n\t\tif err != nil {\n"
                      "\treturn plugins.Signature{\n\t\tStatus:     plugins.SignatureStatusValid,\n\t\tType:       manifest.SignatureType,\n\t\tSigningOrg: manifest.SignedByOrgName,\n\t}, nil", encoding="utf-8")
    return inventory, tmp_path / "upstream", tmp_path / "rebuilt", source, tmp_path / "out"


def test_rebuild_maps_exact_manifest_backend_and_maintainer_identity(tmp_path):
    args = inputs(tmp_path)
    helper().generate(*args)
    result = json.loads((args[-1] / "rebuild-provenance.json").read_text())
    assert len(result["backends"]) == 8
    entry = result["backends"][0]
    assert entry["rebuilt_backend_sha256"] == hashlib.sha256(b"\x7fELFfixture").hexdigest()
    assert entry["original_manifest_sha256"] == hashlib.sha256((args[1] / "fixture0/MANIFEST.txt").read_bytes()).hexdigest()
    assert 'result.SigningOrg = "DeepResearch maintainer rebuild"' in (args[-1] / "manifest.go").read_text()


def test_rebuild_fails_on_unreviewed_upstream_source(tmp_path):
    args = inputs(tmp_path)
    args[-2].write_text("new upstream implementation", encoding="utf-8")
    with pytest.raises(ValueError, match="source shape"):
        helper().generate(*args)


def test_rebuild_fails_on_missing_or_non_executable_backend(tmp_path):
    args = inputs(tmp_path)
    (args[2] / "fixture0/gpx_fixture_linux_amd64").write_bytes(b"not an ELF")
    with pytest.raises(ValueError, match="ELF"):
        helper().generate(*args)
