"""Independent human-authorized exact Grafana artifact; no raw findings are removed."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.image_disposition import DispositionError, canonical, checked_file, file_hash  # noqa: E402
from scripts.oci_artifact import verify_oci_archive  # noqa: E402

POLICY = Path("deployment/security/grafana-reviewed-disposition.json")
APPROVED_BINDING_SHA256 = "7e0ae656477b23d4cd3692987360ada987c40aa6f4c13e37dbb6655495b3e35d"
MAX_ARCHIVE_BYTES = 2 * 1024**3
EVIDENCE = Path("deployment/security/grafana-fixed-evidence")


def normalized_target(value):
    if not isinstance(value, str) or not value or ".." in value.split("/") or "\\" in value:
        raise DispositionError("Finding target path is unknown or unsafe")
    return value.lstrip("/")


def load_binding(root=ROOT, *, now=None):
    policy = json.loads(checked_file(root, POLICY.as_posix()).read_text(encoding="utf-8"))
    binding = policy["binding"]
    if hashlib.sha256(canonical(binding)).hexdigest() != APPROVED_BINDING_SHA256:
        raise DispositionError("Grafana artifact/source/evidence binding changed; refreeze review binding")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now > datetime.fromisoformat(binding["expires"]):
        raise DispositionError("Grafana exact-artifact approval expired or clock is invalid")
    if binding["archive_limit_bytes"] != MAX_ARCHIVE_BYTES or binding["artifact"] != "grafana":
        raise DispositionError("Grafana artifact namespace/cap differs")
    for name, digest in {**binding["evidence_files"], **binding["recipe_files"]}.items():
        if file_hash(checked_file(root, name)) != digest:
            raise DispositionError("Grafana approved source or evidence digest differs")
    current = {p.relative_to(root).as_posix() for p in (root / "deployment/vendors/grafana").rglob("*") if p.is_file()}
    expected = {p for p in binding["recipe_files"] if p.startswith("deployment/vendors/grafana/")}
    if current != expected:
        raise DispositionError("Grafana recipe file inventory changed")
    auth_path, review_path = root / EVIDENCE / "authorization.json", root / EVIDENCE / "independent-review.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8-sig"))
    review = json.loads(review_path.read_text(encoding="utf-8-sig"))
    subject = auth.get("subject", {})
    if (auth.get("accepted") is not True or file_hash(auth_path) != binding["authorization_sha256"]
            or file_hash(review_path) != binding["review_sha256"]
            or auth.get("request_sha256") != binding["request_sha256"]
            or review.get("request_sha256") != binding["request_sha256"]
            or review.get("subject") != subject or review.get("approval_pending") is not False
            or subject.get("oci_index") != binding["index_digest"]
            or subject.get("linux_amd64_manifest") != binding["platform_manifest_digest"]
            or subject.get("config") != binding["config_digest"]
            or auth.get("target_advisory_rows_count") != 18
            or auth["policy"].get("expires") != binding["expires"]
            or not all(auth["policy"].get(k) is True for k in ("keep_all_raw", "unknown_or_new_or_failed_or_missing_blocks", "any_source_or_binary_or_image_change_invalidates", "no_medium_low_exclusions", "no_broad_module_ignore"))):
        raise DispositionError("Grafana human authorization/review has a different subject or scope")
    baseline = json.loads((root / EVIDENCE / "musl-fixed-trivy-full-raw.json").read_bytes())
    validate_authorized_rows(auth, baseline, binding)
    for row in binding["compiled_graphs"]:
        hashed, size = hashlib.sha256(), 0
        with gzip.open(root / row["compressed_evidence"], "rb") as stream:
            while block := stream.read(1024 * 1024):
                size += len(block)
                if size > row["raw_bytes"]:
                    raise DispositionError("Bound compile graph exceeds its reviewed size")
                hashed.update(block)
        if size != row["raw_bytes"] or hashed.hexdigest() != row["raw_sha256"]:
            raise DispositionError("Bound compile graph differs from independently reviewed raw bytes")
    validate_local_sources(binding, root)
    return binding


def validate_authorized_rows(auth, baseline, binding):
    frozen = [(v["VulnerabilityID"], normalized_target(r["Target"]), v["PkgName"], v["InstalledVersion"], v["Severity"]) for r in baseline["Results"] for v in r.get("Vulnerabilities", [])]
    rebuilt = set()
    for row in auth["exact_target_advisory_rows"]:
        advisory = row.get("cve", row.get("advisory_id"))
        found = [v for v in frozen if v[0] == advisory and v[2] == row["component"] and v[3] == row["raw_module_version"] and (not row.get("target") or v[1] == normalized_target(row["target"]))]
        if len(found) != 1 or found[0][4] != row["raw_severity"]:
            raise DispositionError("Grafana authorization cannot map to one exact frozen finding")
        rebuilt.add((*found[0], row["proposed_disposition"].upper()))
    allowed = {(r["id"], r["target"], r["package"], r["version"], r["severity"], r["disposition"]) for r in binding["allowed_findings"]}
    if len(rebuilt) != 18 or rebuilt != allowed:
        raise DispositionError("Grafana exact18 target/advisory rows differ")

def validate_local_sources(binding, root=ROOT):
    rows = binding["inventory_identity"]["local_source_modules"]
    seen = set()
    for row in rows:
        key = (row["target"], row["module"])
        if key in seen or row["elf_sha256"] != binding["binary_sha256"].get(row["target"]):
            raise DispositionError("Local source inventory target/ELF identity differs")
        seen.add(key)
        module_lines = checked_file(root, row["source_go_mod"]).read_text(encoding="utf-8").splitlines()
        if row["module_directive"] not in [line.strip() for line in module_lines]:
            raise DispositionError("Local source module directive differs")
        replacement = row["replace_directive"]
        actual = [line.strip() for line in checked_file(root, row["replace_file"]).read_text(encoding="utf-8").splitlines()]
        tokens = replacement.split()
        if tokens and tokens[0] == "replace":
            tokens = tokens[1:]
        if (replacement not in actual or len(tokens) != 3 or tokens[1] != "=>" or not tokens[2].startswith("./")
                or tokens[0] != row["module_directive"].removeprefix("module ")
                or row["module"] not in {tokens[0], tokens[2]}):
            raise DispositionError("Local source replace directive is not the exact reviewed mapping")


def recognized_local_identity(target, package, binding):
    return any(row["target"] == target and row["module"] == package for row in binding["inventory_identity"]["local_source_modules"])


def verify_archive(path, binding):
    return verify_oci_archive(path, binding, max_archive_bytes=MAX_ARCHIVE_BYTES)


def validate_loaded(inspection, binding):
    if (inspection.get("Id") not in {binding["index_digest"], binding["platform_manifest_digest"], binding["config_digest"]}
            or inspection.get("RootFS", {}).get("Layers") != binding["diff_ids"]
            or inspection.get("Architecture") != "amd64" or inspection.get("Os") != "linux"):
        raise DispositionError("Loaded Grafana image differs from the approved OCI/config/filesystem")


def validate_sbom(sbom, binding):
    source = sbom.get("source", {}).get("metadata", {})
    raw = base64.b64decode(source.get("config", ""), validate=True)
    manifest_raw = base64.b64decode(source.get("manifest", ""), validate=True)
    config, manifest = json.loads(raw), json.loads(manifest_raw)
    if ("sha256:" + hashlib.sha256(raw).hexdigest() != binding["config_digest"]
            or source.get("imageID") != binding["config_digest"]
            or "sha256:" + hashlib.sha256(manifest_raw).hexdigest() != source.get("manifestDigest")
            or manifest.get("config", {}).get("digest") != binding["config_digest"]
            or config.get("rootfs", {}).get("diff_ids") != binding["diff_ids"]):
        raise DispositionError("Grafana native/full SBOM source config chain differs")
    artifacts = sbom.get("artifacts", [])
    if not artifacts or any(not a.get("name") or not a.get("version") or a["version"] in {"unknown", "(devel)"} for a in artifacts):
        raise DispositionError("Grafana full package identity/version is unknown")
    counts = Counter(a.get("type") for a in artifacts)
    if any(counts[k] < count for k, count in binding["minimum_syft_types"].items()):
        raise DispositionError("Grafana complete native inventory coverage is missing")
    native = binding["expected_native_application"]
    found = [a for a in artifacts if a.get("name") == native["name"] and a.get("type") == native["type"] and a.get("version") == native["version"] and any(c.get("cpe") == native["cpe"] for c in a.get("cpes", []) if isinstance(c, dict))]
    if len(found) != 1:
        raise DispositionError("Actual Grafana13.2.4 native application/CPE is missing")
    return dict(counts)


def evaluate_scans(trivy, grype, sbom, inspection, binding, *, trivy_exit_code, grype_exit_code):
    validate_loaded(inspection, binding)
    inventory = validate_sbom(sbom, binding)
    if trivy_exit_code != 0 or grype_exit_code != 0 or trivy.get("SchemaVersion") != 2 or not trivy.get("Results"):
        raise DispositionError("Full operational scanner success/reports are required")
    if trivy.get("Metadata", {}).get("ImageID") != inspection["Id"] or trivy["Metadata"].get("DiffIDs") != binding["diff_ids"]:
        raise DispositionError("Trivy report does not bind to actual loaded Grafana")
    if (not isinstance(grype.get("matches"), list) or grype.get("descriptor", {}).get("db", {}).get("status", {}).get("valid") is not True
            or grype.get("source", {}).get("target", {}).get("imageID") != binding["config_digest"] or grype.get("ignoredMatches")):
        raise DispositionError("Full Grype native report/database/source is missing or ignored")
    allowed = {(r["id"], r["target"], r["package"], r["version"], r["severity"]): r for r in binding["allowed_findings"]}
    tr_counts = Counter(dict.fromkeys(("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"), 0))
    gr_counts = Counter(dict.fromkeys(("Critical", "High", "Medium", "Low", "Unknown", "Negligible"), 0))
    coverage, seen, matched, unresolved, remaining = {}, {"Trivy": set(), "Grype": set()}, {"Trivy": [], "Grype": []}, [], []
    def observe(scanner, advisory, target, package, version, severity):
        key = (advisory, normalized_target(target), package, version, severity.upper())
        if key in seen[scanner]:
            raise DispositionError("Duplicate Grafana target/advisory scan rows are ambiguous")
        seen[scanner].add(key)
        canonical_key = key
        for alias in binding["scanner_exact_aliases"]:
            if scanner == alias["scanner"] and key == (alias["raw_id"], alias["target"], alias["package"], alias["version"], alias["severity"]):
                canonical_key = (alias["canonical_id"], *key[1:])
        if canonical_key in allowed:
            matched[scanner].append({"id": advisory, "canonical_id": canonical_key[0], "target": key[1], "disposition": allowed[canonical_key]["disposition"]})
        else:
            unresolved.append({"scanner": scanner, "id": advisory, "target": key[1], "package": package, "version": version, "severity": severity})
    for result in trivy["Results"]:
        packages = result.get("Packages", [])
        for package in packages:
            if not package.get("Name") or package.get("Version") in {"unknown", "(devel)", ""}:
                raise DispositionError("Trivy package identity/version is unknown")
            if package.get("Version") is None and not recognized_local_identity(result["Target"], package["Name"], binding):
                raise DispositionError("Unrecognized missing-version package/source tuple")
        key = result.get("Type") if result.get("Class") == "os-pkgs" else result.get("Target")
        if key in coverage:
            raise DispositionError("Repeated Trivy inventory target is ambiguous")
        coverage[key] = len(packages)
        for finding in result.get("Vulnerabilities", []):
            severity = finding.get("Severity")
            if severity not in tr_counts:
                raise DispositionError("Unknown Trivy severity value")
            tr_counts[severity] += 1
            if severity in {"HIGH", "CRITICAL", "UNKNOWN"}:
                observe("Trivy", finding.get("VulnerabilityID"), result["Target"], finding.get("PkgName"), finding.get("InstalledVersion"), severity)
            else:
                remaining.append({"scanner":"Trivy", "id":finding.get("VulnerabilityID"), "severity":severity, "package":finding.get("PkgName")})
    if any(coverage.get(key, 0) < count for key, count in binding["minimum_trivy_coverage"].items()):
        raise DispositionError("Trivy complete runtime package coverage is missing")
    for match in grype["matches"]:
        vuln, artifact = match.get("vulnerability", {}), match.get("artifact", {})
        severity = vuln.get("severity")
        if severity not in gr_counts:
            raise DispositionError("Unknown Grype severity value")
        gr_counts[severity] += 1
        if severity in {"High", "Critical", "Unknown"}:
            targets = {p.get("path") for p in artifact.get("locations", []) if p.get("path")}
            if not targets:
                raise DispositionError("Blocking Grype finding has no exact target")
            for target in sorted(targets):
                observe("Grype", vuln.get("id"), target, artifact.get("name"), artifact.get("version"), severity)
        else:
            remaining.append({"scanner":"Grype", "id":vuln.get("id"), "severity":severity, "package":artifact.get("name"), "fix":vuln.get("fix")})
    return {"passed":not unresolved, "raw_counts":{"Trivy":dict(tr_counts),"Grype":dict(gr_counts)}, "verified_fixed_counts":{k:sum(r["disposition"]=="FIXED" for r in v) for k,v in matched.items()}, "verified_not_affected_counts":{k:sum(r["disposition"]=="NOT_AFFECTED" for r in v) for k,v in matched.items()}, "verified_dispositions":matched, "unresolved_count":len(unresolved), "unresolved":unresolved, "remaining_unexcluded_medium_low":remaining, "trivy_coverage":coverage, "syft_types":inventory, "raw_reports_modified":False, "identified_local_source_tuple_count":len(binding["inventory_identity"]["local_source_modules"]), "local_inventory_identity_is_not_a_vulnerability_disposition":True, "approval_expires":binding["expires"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive",required=True,type=Path)
    parser.add_argument("--scan",required=True,type=Path)
    parser.add_argument("--grype-scan",required=True,type=Path)
    parser.add_argument("--sbom",required=True,type=Path)
    parser.add_argument("--inspect",required=True,type=Path)
    parser.add_argument("--scanner-exit-code",required=True,type=int)
    parser.add_argument("--grype-exit-code",required=True,type=int)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    try:
        binding=load_binding()
        facts=verify_archive(args.archive,binding)
        inspection=json.loads(args.inspect.read_bytes())
        if isinstance(inspection,list):
            if len(inspection)!=1:
                raise DispositionError("Exactly one actual image inspection is required")
            inspection=inspection[0]
        result=evaluate_scans(json.loads(args.scan.read_bytes()),json.loads(args.grype_scan.read_bytes()),json.loads(args.sbom.read_bytes()),inspection,binding,trivy_exit_code=args.scanner_exit_code,grype_exit_code=args.grype_exit_code)
        result.update(mode="human_authorized_exact_Grafana_per_finding_dispositions",archive=facts,raw_scan_sha256=file_hash(args.scan),raw_grype_sha256=file_hash(args.grype_scan))
    except Exception as error:
        result={"passed":False,"mode":"failed_closed","error_type":type(error).__name__}
        if isinstance(error,ValueError):
            result["error"]=str(error)[:240]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:result[k] for k in ("passed","mode","raw_counts","verified_fixed_counts","verified_not_affected_counts","unresolved_count","error") if k in result}))
    raise SystemExit(0 if result["passed"] else 1)


if __name__=="__main__":
    main()
