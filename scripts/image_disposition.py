"""One human-authorized FIXED artifact; raw scanner results are never filtered."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.oci_artifact import OCIArtifactError, verify_oci_archive  # noqa: E402

POLICY = Path("deployment/security/minio-fixed-disposition.json")
APPROVED_BINDING_SHA256 = "2d8518f9ab275f97f0c98c68f3a94d42217b292a2fae50e0ce6461f46919d68e"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


class DispositionError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def hash_stream(stream):
    digest = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hash_stream(stream)


def checked_file(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise DispositionError("Evidence path is not a repository-relative path")
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise DispositionError("Required evidence is missing or outside the repository")
    return path


def load_binding(root=ROOT, *, now=None):
    policy = json.loads(checked_file(root, POLICY.as_posix()).read_text(encoding="utf-8"))
    binding = policy["binding"]
    if hashlib.sha256(canonical(binding)).hexdigest() != APPROVED_BINDING_SHA256:
        raise DispositionError(
            "Approved artifact/source/evidence binding changed; renewed review is required"
        )
    validate_evidence(binding, root, now=now)
    return binding


def validate_evidence(binding, root, *, now=None):
    now = now or datetime.now(timezone.utc)
    expires = datetime.fromisoformat(binding["expires"])
    if now.tzinfo is None or now > expires:
        raise DispositionError("Exact-artifact FIXED approval expired or clock lacks timezone")
    for name, digest in {**binding["evidence_files"], **binding["recipe_files"]}.items():
        if file_hash(checked_file(root, name)) != digest:
            raise DispositionError("Approved source/patch/build evidence digest differs")
    authorization = checked_file(root, "deployment/security/minio-fixed-evidence/authorization.json")
    if file_hash(authorization) != binding["authorization_sha256"]:
        raise DispositionError("Human authorization does not match the approved record")
    agreement = json.loads(authorization.read_text(encoding="utf-8-sig"))
    fields = ("index_digest", "platform_manifest_digest", "config_digest", "source_commit", "vulnerability")
    if (
        agreement.get("accepted") is not True
        or any(agreement.get(key) != binding[key] for key in fields)
        or agreement["policy"]["expires"] != binding["expires"]
        or binding["disposition"] != "FIXED"
    ):
        raise DispositionError("Human authorization has a different artifact or scope")


def verify_archive(path, binding):
    # MinIO keeps its original fixed policy and512MiB ceiling. Shared code verifies
    # integrity only; neither another caller nor a larger cap grants approval.
    try:
        facts = verify_oci_archive(path, binding, max_archive_bytes=MAX_ARCHIVE_BYTES)
    except OCIArtifactError as error:
        raise DispositionError(str(error)) from None
    return {key: facts[key] for key in (
        "index_digest", "platform_manifest_digest", "config_digest",
        "archive_sha256", "binary_sha256", "verified_blobs",
    )}


def evaluate_scan(report, inspection, binding, *, scanner_exit_code):
    if scanner_exit_code != 0 or report.get("SchemaVersion") != 2 or not report.get("Results"):
        raise DispositionError("Scanner failed, is missing, or returned an unknown report format")
    image_id = inspection.get("Id")
    if (
        image_id
        not in {binding["index_digest"], binding["platform_manifest_digest"], binding["config_digest"]}
        or inspection.get("RootFS", {}).get("Layers") != binding["diff_ids"]
        or inspection.get("Architecture") != binding["architecture"]
        or inspection.get("Os") != binding["os"]
        or report.get("Metadata", {}).get("ImageID") != image_id
        or report.get("Metadata", {}).get("DiffIDs") != binding["diff_ids"]
    ):
        raise DispositionError("Loaded/scanned image does not bind to the approved OCI/config/filesystem")
    coverage = {}
    raw, fixed, unresolved = Counter({"HIGH": 0, "CRITICAL": 0}), [], []
    for result in report["Results"]:
        if any(
            not package.get("Name")
            or not package.get("Version")
            or package.get("Version") in {"(devel)", "unknown"}
            for package in result.get("Packages", [])
        ):
            raise DispositionError("Runtime package identity/version is unknown")
        key = result.get("Type") if result.get("Class") == "os-pkgs" else result.get("Target")
        coverage[key] = len(result.get("Packages", []))
        for finding in result.get("Vulnerabilities", []):
            severity = finding.get("Severity")
            if severity not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
                unresolved.append({"reason": "unknown_severity", "finding": finding.get("VulnerabilityID")})
                continue
            if severity not in {"HIGH", "CRITICAL"}:
                continue
            raw[severity] += 1
            expected = binding["finding"]
            if (
                finding.get("VulnerabilityID") == binding["vulnerability"]
                and result.get("Target") == expected["target"]
                and finding.get("PkgName") == expected["package"]
                and finding.get("InstalledVersion") == expected["version"]
                and severity == expected["severity"]
            ):
                fixed.append(
                    {
                        "vulnerability": finding["VulnerabilityID"],
                        "target": result["Target"],
                        "disposition": "FIXED",
                    }
                )
            else:
                unresolved.append(
                    {
                        "vulnerability": finding.get("VulnerabilityID"),
                        "package": finding.get("PkgName"),
                        "target": result.get("Target"),
                        "severity": severity,
                    }
                )
    if any(coverage.get(key, 0) < count for key, count in binding["minimum_package_coverage"].items()):
        raise DispositionError(
            "Runtime package coverage is missing or lower than the reviewed complete inventory"
        )
    if len(fixed) > 1:
        raise DispositionError("More than the single authorized finding was matched")
    return {
        "passed": not unresolved,
        "raw_counts": dict(raw),
        "verified_fixed_count": len(fixed),
        "verified_fixed": fixed,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "coverage": coverage,
        "raw_report_modified": False,
        "approval_expires": binding["expires"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--scan", type=Path)
    parser.add_argument("--inspect", type=Path)
    parser.add_argument("--scanner-exit-code", type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        binding = load_binding()
        archive = verify_archive(args.archive, binding)
        result = {"archive": archive, "passed": True, "mode": "archive_only_no_scan_gate_claim"}
        if args.scan or args.inspect or args.scanner_exit_code is not None:
            if args.scan is None or args.inspect is None or args.scanner_exit_code is None:
                raise DispositionError(
                    "A full scanner report, actual inspection and exit status are all required"
                )
            inspection = json.loads(args.inspect.read_bytes())
            if isinstance(inspection, list):
                if len(inspection) != 1:
                    raise DispositionError("Exactly one actual image inspection is required")
                inspection = inspection[0]
            result = {
                **evaluate_scan(
                    json.loads(args.scan.read_bytes()),
                    inspection,
                    binding,
                    scanner_exit_code=args.scanner_exit_code,
                ),
                "archive": archive,
                "raw_scan_sha256": file_hash(args.scan),
                "mode": "authorized_exact_artifact_FIXED",
            }
    except (DispositionError, KeyError, ValueError, OSError, tarfile.TarError) as error:
        result = {"passed": False, "mode": "failed_closed", "error": str(error)[:220]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "passed",
                    "mode",
                    "raw_counts",
                    "verified_fixed_count",
                    "unresolved_count",
                    "error",
                )
                if key in result
            }
        )
    )
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
