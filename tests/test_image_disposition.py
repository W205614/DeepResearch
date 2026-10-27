from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import io
import json
import shutil
import tarfile

import pytest

from scripts.image_disposition import (
    ROOT,
    DispositionError,
    evaluate_scan,
    load_binding,
    validate_evidence,
    verify_archive,
)


@pytest.fixture
def binding():
    return load_binding(now=datetime.fromisoformat("2026-10-26T13:00:00+00:00"))


@pytest.fixture
def scan(binding):
    coverage = binding["minimum_package_coverage"]
    expected = binding["finding"]
    results = [
        {
            "Target": "fixture (redhat9.8)",
            "Type": "redhat",
            "Class": "os-pkgs",
            "Packages": [{"Name": "fixture-os", "Version": "1.0"} for _ in range(coverage["redhat"])],
        }
    ]
    for target in ("usr/bin/mcli", "usr/bin/silo"):
        results.append(
            {
                "Target": target,
                "Type": "gobinary",
                "Class": "lang-pkgs",
                "Packages": [
                    {"Name": "example.invalid/dependency", "Version": "v1.0.0"}
                    for _ in range(coverage[target])
                ],
            }
        )
    results[-1]["Vulnerabilities"] = [
        {
            "VulnerabilityID": binding["vulnerability"],
            "PkgName": expected["package"],
            "InstalledVersion": expected["version"],
            "Severity": "HIGH",
        }
    ]
    return {
        "SchemaVersion": 2,
        "Metadata": {"ImageID": binding["index_digest"], "DiffIDs": binding["diff_ids"]},
        "Results": results,
    }


@pytest.fixture
def inspection(binding):
    return {
        "Id": binding["index_digest"],
        "RootFS": {"Layers": binding["diff_ids"]},
        "Architecture": "amd64",
        "Os": "linux",
    }


def test_single_fixed_keeps_raw_finding_and_counts(scan, inspection, binding):
    before = deepcopy(scan)
    result = evaluate_scan(scan, inspection, binding, scanner_exit_code=0)
    assert result["passed"] and result["raw_counts"] == {"HIGH": 1, "CRITICAL": 0}
    assert result["verified_fixed_count"] == 1 and result["unresolved_count"] == 0
    assert scan == before and result["raw_report_modified"] is False


def test_clean_future_database_reports_both_zero_counts_without_fake_fixed(scan, inspection, binding):
    scan["Results"][-1]["Vulnerabilities"] = []
    result = evaluate_scan(scan, inspection, binding, scanner_exit_code=0)
    assert result["passed"]
    assert result["raw_counts"] == {"HIGH": 0, "CRITICAL": 0}
    assert result["verified_fixed_count"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("VulnerabilityID", "CVE-2099-NEW"),
        ("PkgName", "unreviewed/package"),
        ("InstalledVersion", "v1.0.0"),
        ("Severity", "CRITICAL"),
        ("Severity", "UNKNOWN"),
    ],
)
def test_new_finding_or_package_never_inherits_approval(scan, inspection, binding, field, value):
    finding = deepcopy(scan["Results"][-1]["Vulnerabilities"][0])
    finding[field] = value
    scan["Results"][-1]["Vulnerabilities"].append(finding)
    result = evaluate_scan(scan, inspection, binding, scanner_exit_code=0)
    assert not result["passed"] and result["unresolved_count"] == 1


def test_same_cve_in_another_binary_is_unresolved(scan, inspection, binding):
    scan["Results"][1]["Vulnerabilities"] = deepcopy(scan["Results"][-1]["Vulnerabilities"])
    result = evaluate_scan(scan, inspection, binding, scanner_exit_code=0)
    assert not result["passed"] and result["unresolved"][0]["target"] == "usr/bin/mcli"


@pytest.mark.parametrize(
    "case",
    [
        "scanner_failure",
        "missing_results",
        "unknown_schema",
        "wrong_image",
        "wrong_layer",
        "coverage_missing",
        "unknown_package",
    ],
)
def test_unknown_or_mismatched_scan_fails_closed(scan, inspection, binding, case):
    code = 0
    if case == "scanner_failure":
        code = 1
    if case == "missing_results":
        scan["Results"] = []
    if case == "unknown_schema":
        scan["SchemaVersion"] = 99
    if case == "wrong_image":
        inspection["Id"] = "sha256:" + "f" * 64
    if case == "wrong_layer":
        inspection["RootFS"]["Layers"] = ["sha256:" + "f" * 64]
    if case == "coverage_missing":
        scan["Results"][-1]["Packages"] = []
    if case == "unknown_package":
        scan["Results"][-1]["Packages"][0]["Version"] = "(devel)"
    with pytest.raises(DispositionError):
        evaluate_scan(scan, inspection, binding, scanner_exit_code=code)


def test_expired_authorization_cannot_pass_even_the_same_artifact(binding):
    expired = datetime.fromisoformat(binding["expires"]) + timedelta(seconds=1)
    with pytest.raises(DispositionError, match="expired"):
        validate_evidence(binding, ROOT, now=expired)


def test_tampered_or_missing_evidence_cannot_pass(binding, tmp_path):
    frozen = datetime.fromisoformat("2026-10-26T13:00:00+00:00")
    with pytest.raises(DispositionError, match="missing"):
        validate_evidence(binding, tmp_path, now=frozen)
    for name in {**binding["evidence_files"], **binding["recipe_files"]}:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    target = tmp_path / "deployment/vendors/minio/legacy-part-compat.patch"
    target.write_bytes(target.read_bytes() + b"\nchanged patch\n")
    with pytest.raises(DispositionError, match="digest differs"):
        validate_evidence(binding, tmp_path, now=frozen)


def test_changed_binding_requires_new_review(binding, tmp_path):
    policy = tmp_path / "deployment/security/minio-fixed-disposition.json"
    policy.parent.mkdir(parents=True)
    binding["index_digest"] = "sha256:" + "f" * 64
    policy.write_text(json.dumps({"binding": binding}), encoding="utf-8")
    with pytest.raises(DispositionError, match="renewed review"):
        load_binding(tmp_path)


def oci_fixture(tmp_path, *, omit_layer=False, corrupt_layer=False):
    def digest(data):
        return "sha256:" + hashlib.sha256(data).hexdigest()

    executable = b"reviewed executable bytes"
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode="w") as target:
        member = tarfile.TarInfo("usr/bin/silo")
        member.size = len(executable)
        target.addfile(member, io.BytesIO(executable))
    layer_data = layer.getvalue()
    config = json.dumps({"architecture": "amd64", "os": "linux", "rootfs": {"type": "layers", "diff_ids": [digest(layer_data)]}}).encode()
    blobs = {}

    def add(data, kind):
        key = digest(data)
        blobs[key] = data
        return {"digest": key, "size": len(data), "mediaType": kind}

    layer_desc = add(layer_data, "application/vnd.oci.image.layer.v1.tar")
    config_desc = add(config, "application/vnd.oci.image.config.v1+json")
    manifest = add(
        json.dumps({"config": config_desc, "layers": [layer_desc]}).encode(),
        "application/vnd.oci.image.manifest.v1+json",
    )
    platform = {**manifest, "platform": {"os": "linux", "architecture": "amd64"}}
    index = add(json.dumps({"manifests": [platform]}).encode(), "application/vnd.oci.image.index.v1+json")
    files = {
        "index.json": json.dumps({"manifests": [index]}).encode(),
        "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
    }
    for key, data in blobs.items():
        if key == layer_desc["digest"]:
            if omit_layer:
                continue
            if corrupt_layer:
                data = b"bad" + data[3:]
        files["blobs/sha256/" + key[7:]] = data
    archive = tmp_path / "candidate.oci.tar"
    with tarfile.open(archive, "w") as target:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            target.addfile(member, io.BytesIO(data))
    scope = {
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "index_digest": index["digest"],
        "platform_manifest_digest": manifest["digest"],
        "config_digest": config_desc["digest"],
        "diff_ids": [layer_desc["digest"]],
        "binary_sha256": {"usr/bin/silo": hashlib.sha256(executable).hexdigest()},
    }
    return archive, scope


def test_oci_chain_and_binary_are_actually_verified(tmp_path):
    archive, scope = oci_fixture(tmp_path)
    assert verify_archive(archive, scope)["verified_blobs"] == 4


@pytest.mark.parametrize("case", ["missing_blob", "corrupt_blob", "changed_binary"])
def test_oci_missing_or_changed_content_never_passes(tmp_path, case):
    archive, scope = oci_fixture(
        tmp_path, omit_layer=case == "missing_blob", corrupt_layer=case == "corrupt_blob"
    )
    if case == "changed_binary":
        scope["binary_sha256"]["usr/bin/silo"] = "f" * 64
    with pytest.raises(DispositionError):
        verify_archive(archive, scope)
