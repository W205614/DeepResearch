"""Verify package-specific OpenPGP attribution; never authorize a scan exclusion."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package_objects(path):
    text = path.read_text(encoding="utf-8")
    decoder, offset = json.JSONDecoder(), 0
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset == len(text):
            break
        item, offset = decoder.raw_decode(text, offset)
        if item.get("Incomplete") or item.get("Error"):
            raise ValueError("Incomplete import graph is not package absence evidence")
        yield item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--subject-facts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Retain previous evidence and use a new output")
    p = args.evidence_dir
    work = p / "grafana-unknown-attribution"
    facts = read(args.subject_facts)
    advisory = read(work / "go-advisory.json")
    assert advisory["id"] == "GO-2026-5932"
    imports = [x["path"] for x in advisory["affected"][0]["ecosystem_specific"]["imports"]]
    assert len(imports) == 7 and all(x.startswith("golang.org/x/crypto/openpgp") for x in imports)
    linked = read(work / "exact-binary-linked-packages.json")
    assert len(linked) == 5
    original = read(p / "grafana-official-assets-hashes.json")
    overrides = read(p / "grafana-main-build-context/rebuild-provenance.json")["backends"]
    original.update({x["plugin_directory"] + "/" + x["binary"]: x["rebuilt_backend_sha256"] for x in overrides})
    graphs = {}
    evidence = {}
    main_graph = work / "main-import-graph/main-deps.json"
    main_objects = list(package_objects(main_graph))
    graphs["grafana"] = [x["ImportPath"] for x in main_objects]
    evidence["grafana"] = {"go_list": "CGO_ENABLED=0 GOOS=linux GOARCH=amd64; -mod=readonly ./pkg/cmd/grafana; no tags",
                            "raw_sha256": sha(main_graph)}
    assert read(work / "main-import-graph/main-env.json") == {
        "CGO_ENABLED": "0", "GOARCH": "amd64", "GOFLAGS": "-p=2", "GOOS": "linux"}
    for name in ("elasticsearch", "stackdriver"):
        folder = work / "official-plugin-import-graphs/out"
        path = folder / (name + "-deps.json")
        objects = list(package_objects(path))
        graphs[name] = [x["ImportPath"] for x in objects]
        source = read(folder / (name + "-source.json"))
        evidence[name] = {"go_list": "CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOAMD64=v1; -tags=arrow_json_stdlib -mod=readonly ./pkg",
                          "source": {k: source[k] for k in ("Path", "Version", "Sum", "Origin")},
                          "raw_sha256": sha(path)}
    for name in ("loki", "mssql"):
        path = p / "grafana-eight-built-v3/out/build-info" / (name + ".packages.txt")
        graphs[name] = path.read_text(encoding="utf-8").splitlines()
        evidence[name] = {"go_list": "actual backend build emitted go list -deps -mod=readonly ./pkg; CGO_ENABLED=0 linux/amd64; no tags",
                          "raw_sha256": sha(path)}
    rows = []
    for item in linked:
        target = item["target"]
        name = "grafana" if target == "/usr/share/grafana/bin/grafana" else target.split("/plugins-bundled/")[1].split("/")[0]
        expected_sha = facts["main_elf_sha256"] if name == "grafana" else original[target.split("/plugins-bundled/")[1]]
        assert item["sha256"] == expected_sha
        build_info = (work / (item["binary"] + ".build-info.txt")).read_text(encoding="utf-8")
        assert all("\tbuild\t" + setting in build_info for setting in
                   ("CGO_ENABLED=0", "GOOS=linux", "GOARCH=amd64"))
        if name in ("elasticsearch", "stackdriver"):
            assert "\tbuild\t-tags=arrow_json_stdlib\n" in build_info
            assert "vcs.revision=" + evidence[name]["source"]["Origin"]["Hash"] in build_info
            actual_modules = {parts[1]: parts[2] for line in build_info.splitlines()
                              if (parts := line.strip().split()) and parts[0] == "dep"}
            objects = package_objects(work / "official-plugin-import-graphs/out" / (name + "-deps.json"))
            modules = {x["Module"]["Path"]: x["Module"]["Version"] for x in objects
                       if x.get("Module", {}).get("Version")}
            assert all(actual_modules.get(k) == v for k, v in modules.items())
            evidence[name]["effective_module_versions_equal_actual_ELF_deps"] = True
        else:
            assert "\tbuild\t-tags=" not in build_info
        functions = read(work / (item["artifact_id"] + "-pclntab.json"))
        assert functions["elf_sha256"] == expected_sha
        graph = graphs[name]
        old = [x for x in graph if re.search(r"(^|/)golang\.org/x/crypto/openpgp(?:/|$)", x)]
        assert not old
        assert not any("golang.org/x/crypto/openpgp" in x for x in functions["linked_functions"])
        maintained = [x for x in graph if x.startswith("github.com/ProtonMail/go-crypto/openpgp")]
        if name == "grafana":
            assert maintained and item["protonmail_openpgp_functions_count"] > 500
        rows.append({"advisory": "GO-2026-5932", "package": "golang.org/x/crypto", "version": "v0.57.0",
                     "target": target, "elf_sha256": expected_sha,
                     "go_list_package_count": len(graph), "excluded_exact_old_packages": imports,
                     "old_openpgp_packages_in_graph": old,
                     "maintained_protonmail_packages": maintained,
                     "pclntab_function_count": functions["function_count"],
                     "old_openpgp_linked_functions": 0, "other_xcrypto_functions_retained": item["other_xcrypto_functions_count"],
                     "compile_evidence": evidence[name], "proposed_disposition": "not_affected:vulnerable_code_not_present",
                     "authorization_granted": False})
    receipt = {"status": "exact_package_facts_verified_independent_review_required", "index_digest": facts["index_digest"],
               "configuration_digest": facts["scanner_configuration_digest"], "rows": rows,
               "official_go_osv_sha256": sha(work / "go-advisory.json"),
               "scope_limit": "only the7 advisory-listed old openpgp packages in these5 exact ELFs; other x/crypto packages remain fully scanned",
               "severity": "Unknown; official Go advisory has no CVSS/severity or CVE alias, not reassigned",
               "raw_unknown_rows_retained": 5, "authorized_unknown_dispositions": 0,
               "unknown_gate_blocked_pending_authorization": True, "vex_enabled": False}
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "exact_targets": len(rows), "unknown_authorizations": 0}))


if __name__ == "__main__":
    main()
