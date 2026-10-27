"""Record exact Grafana artifact facts for review; never authorize or suppress CVEs."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def docker(*args):
    value = subprocess.run(["docker", *args], capture_output=True, timeout=120)
    if value.returncode:
        raise RuntimeError(value.stderr.decode(errors="replace")[-1000:])
    return value.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--expected-index", required=True)
    parser.add_argument("--expected-config", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--trivy-raw", type=Path)
    parser.add_argument("--syft-raw", type=Path)
    parser.add_argument("--grype-raw", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Previous evidence must be retained")
    root = Path(__file__).resolve().parents[1]
    p = args.evidence_dir
    info = json.loads(docker("image", "inspect", args.image))[0]
    assert info["Id"] == args.expected_index
    version = docker("run", "--rm", "--network", "none", "--entrypoint",
                     "/usr/share/grafana/bin/grafana", args.image, "server", "--version").decode().strip()
    assert "13.2.4" in version and "af4a33e3786594ad72f72d97e6744010330c2977" in version
    raw = docker("run", "--rm", "--network", "none", "--entrypoint", "sh", args.image,
                 "-c", "find /usr/share/grafana/public /usr/share/grafana/data/plugins-bundled -type f -exec sha256sum {} +; sha256sum /usr/share/grafana/bin/grafana")
    hashes = dict((line.split(None, 1)[1].strip(), line.split(None, 1)[0])
                  for line in raw.decode().splitlines())
    original_public = {}
    for line in (p / "grafana-original-frontend-sha256.txt").read_text(encoding="utf-8").splitlines():
        sha, name = line.split(None, 1)
        original_public[name.strip()] = sha
    assert original_public and all(hashes.get(k) == v for k, v in original_public.items())
    manifest = read(p / "grafana-official-assets-hashes.json")
    provenance = read(p / "grafana-main-build-context/rebuild-provenance.json")
    overrides = {x["plugin_directory"] + "/" + x["binary"]: x["rebuilt_backend_sha256"]
                 for x in provenance["backends"]}
    assert len(overrides) == 8
    expected_plugins = {"/usr/share/grafana/data/plugins-bundled/" + k: overrides.get(k, v)
                        for k, v in manifest.items()}
    actual_plugins = {k: v for k, v in hashes.items() if "/plugins-bundled/" in k}
    assert actual_plugins == expected_plugins, "Unknown, missing or modified plugin artifact"
    assert hashes["/usr/share/grafana/bin/grafana"] == digest(p / "grafana-derived-main.bin")
    syft_path = args.syft_raw or p / "grafana-final-native-syft.json"
    syft = read(syft_path)
    binary = [x for x in syft["artifacts"] if x["type"] == "binary" and x["name"] == "grafana"]
    assert len(binary) == 1 and binary[0]["version"] == "13.2.4"
    assert any("grafana:grafana:13.2.4:" in c["cpe"] for c in binary[0]["cpes"])
    linked = read(p / "grafana-tempo-linked-functions.json")
    tempo_path = "/usr/share/grafana/data/plugins-bundled/tempo/gpx_grafana-tempo-datasource_linux_amd64"
    assert hashes[tempo_path] == linked["elf_sha256"]
    names = linked["linked_functions"]
    assert len(names) > 30000
    proto = [x for x in names if x.startswith("github.com/grafana/tempo/pkg/tempopb")]
    excluded = ["github.com/grafana/tempo/modules/frontend", "github.com/grafana/tempo/tempodb/backend/s3"]
    assert proto and not any(any(package in name for package in excluded) for name in names)
    fixes = {}
    module_commit = read(p / "grafana-tempo-exact-module-commit.json")["sha"]
    assert module_commit.startswith("bcfe9f230c1d")
    for cve in ("CVE-2026-21728", "CVE-2026-28377"):
        ancestry = read(p / ("grafana-tempo-ancestry-" + cve + ".json"))
        assert ancestry["status"] == "ahead" and ancestry["behind_by"] == 0
        assert ancestry["html_url"].endswith("...bcfe9f230c1d")
        assert ancestry["merge_base_commit"]["sha"] == ancestry["base_commit"]["sha"]
        fixes[cve] = {"fix_commit": ancestry["base_commit"]["sha"],
                      "module_commit": module_commit,
                      "ancestry": "fix is an ancestor of the exact module commit"}
    for item in read(p / "tempo-fixed-source/receipt.json"):
        assert digest(p / "tempo-fixed-source" / item["path"]) == item["sha256"]
    trivy_path = args.trivy_raw or p / "grafana-final-trivy-raw.json"
    trivy = read(trivy_path)
    assert trivy["Metadata"]["ImageID"] in (args.expected_index, args.expected_config)
    assert syft["source"]["metadata"]["imageID"] == args.expected_config
    assert trivy["Metadata"]["DiffIDs"] == [x["digest"] for x in syft["source"]["metadata"]["layers"]]
    findings = [{"target": result["Target"], **{k: v.get(k) for k in
                ("VulnerabilityID", "PkgName", "InstalledVersion", "FixedVersion", "Severity")}}
                for result in trivy["Results"] for v in result.get("Vulnerabilities", [])]
    grype_path = args.grype_raw or p / "grafana-final-native-grype-raw.json"
    grype = read(grype_path)
    grype_high = [{"id": m["vulnerability"]["id"], "package": m["artifact"]["name"],
                   "version": m["artifact"]["version"]} for m in grype["matches"]
                  if m["vulnerability"]["severity"] in ("High", "Critical")]
    inputs = [trivy_path, syft_path, grype_path, p / "grafana-tempo-linked-functions.json"]
    receipt = {"status": "facts_verified_security_review_pending", "image": args.image,
               "index_digest": info["Id"], "cli": version,
               "scanner_configuration_digest": args.expected_config,
               "scanner_rootfs_diffids_equal": True,
               "main_elf_sha256": hashes["/usr/share/grafana/bin/grafana"],
               "source": read(root / "deployment/vendors/grafana/main-source.lock.json"),
               "upstream_build_contract": "the pinned upstream Makefile uses -buildvcs=false; this candidate retains real vcs metadata and +dirty",
               "native_binary_cataloger": {k: binary[0][k] for k in ("name", "version", "type", "cpes")},
               "core_frontend_files_byte_identical_to_official": len(original_public),
               "complete_catalog_files_checked": len(expected_plugins),
               "compiled_backend_overrides": len(overrides),
               "tempo": {"elf_sha256": hashes[tempo_path], "pclntab_functions": len(names),
                         "protobuf_functions_present": len(proto), "affected_packages_absent": excluded,
                         "limitation": linked["limitation"], "fixed_source_ancestry": fixes},
               "trivy_raw_counts": dict(Counter(x["Severity"] for x in findings)),
               "trivy_raw_findings": findings, "grype_raw_high_critical": grype_high,
               "grype_raw_all_severity_counts": dict(Counter(m["vulnerability"]["severity"] for m in grype["matches"])),
               "grype_raw_unknown": [{"id": m["vulnerability"]["id"], "package": m["artifact"]["name"],
                                       "version": m["artifact"]["version"],
                                       "targets": [x["path"] for x in m["artifact"]["locations"]]}
                                      for m in grype["matches"] if m["vulnerability"]["severity"] == "Unknown"],
               "raw_input_sha256": {x.name: digest(x) for x in inputs},
               "authorization": {"granted": False, "vex_enabled": False,
                                 "ci_suppression_enabled": False, "security_pass": False}}
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "image": info["Id"],
                      "trivy": receipt["trivy_raw_counts"], "grype_high_critical": len(grype_high)}))


if __name__ == "__main__":
    main()
