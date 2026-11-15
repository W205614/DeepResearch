"""Derive a new exact Agent profile from the measured SoupSieve4.1 candidate.

Historical profiles/reports stay unchanged. This producer does not approve
redistribution or replace the parent agent's independent review.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import gzip
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_agent_full_scope as gate  # noqa: E402

NAME = "agent-soupsieve41-20261115"
OLD = "deployment/security/agent-record-metadata-profile-20261113.json"
OLD_SHA = "737170a810dabc61edb9bcedfd02b949f9f447b746051cb828f4ab439170dd6b"
EVIDENCE = ROOT / "deployment/security/agent-soupsieve41-20261115-evidence"
REVIEW = ROOT / "deployment/security/agent-soupsieve41-20261115-review.json"
PROFILE = ROOT / "deployment/security/agent-soupsieve41-20261115-profile.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def write(path, value):
    data = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
    if path.exists() and path.read_bytes() != data:
        raise ValueError("Preserve a prior generated evidence file: " + str(path))
    path.write_bytes(data)


def copy_bytes(source, name, compress=False):
    raw = Path(source).read_bytes()
    if compress:
        raw = gzip.compress(raw, mtime=0)
    destination = EVIDENCE / name
    if destination.exists() and destination.read_bytes() != raw:
        raise ValueError("Generated evidence changed: " + str(destination))
    destination.write_bytes(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    args = parser.parse_args()
    directory = args.candidate_dir.resolve()
    assert directory.is_relative_to((ROOT / ".cache").resolve())
    assert sha(ROOT / OLD) == OLD_SHA
    old = read(ROOT / OLD)
    old_review = read(ROOT / old["source_review_evidence"]["path"])
    assert sha(ROOT / old["source_review_evidence"]["path"]) == old["source_review_evidence"]["sha256"]
    receipt = read(directory / "candidate-receipt.json")
    base = read(directory / "base-runtime-inventory.json")
    current = read(directory / "runtime-inventory.json")
    runtime = read(directory / "runtime-probe.json")
    scan = read(directory / "scan/raw.json")
    findings = [
        gate.finding_tuple(result, row)
        for result in scan["Results"]
        for row in result.get("Vulnerabilities", [])
    ]
    packages = [gate.package_tuple(result, row) for result in scan["Results"] for row in result["Packages"]]
    assert scan["Metadata"]["ImageID"] == receipt["engine_id"]
    assert (
        receipt["all515_native_files_byte_identical"] and receipt["complete_filesystem_expected_delta_only"]
    )
    assert base["native_files"] == current["native_files"]
    assert (
        runtime["native_elf_closure"]
        == current["native_elf_closure"]
        == base["native_elf_closure"]
        == old["native_elf_closure"]
    )
    assert base["python_source_closure"] == old["python_source_closure"]
    assert (
        current["python_source_closure"]
        == runtime["python_source_closure"]
        == receipt["python_source_closure"]
    )
    old_counter = Counter(gate.canonical(row["tuple"]) for row in old["findings"])
    new_counter = Counter(map(gate.canonical, findings))
    removed = [json.loads(raw) for raw in (old_counter - new_counter).elements()]
    assert not new_counter - old_counter and len(findings) == 92 and len(removed) == 2
    assert {(row["id"], row["package"], row["version"], row["severity"]) for row in removed} == {
        ("CVE-2026-126190", "soupsieve", "2.9.2", "HIGH"),
        ("CVE-2026-126192", "soupsieve", "2.9.2", "MEDIUM"),
    }
    before_packages = Counter(map(gate.canonical, old["packages"]))
    after_packages = Counter(map(gate.canonical, packages))
    removed_packages = [json.loads(p) for p in (before_packages - after_packages).elements()]
    added_packages = [json.loads(p) for p in (after_packages - before_packages).elements()]
    assert len(removed_packages) == len(added_packages) == 1 and len(packages) == 125
    assert removed_packages[0]["name"] == added_packages[0]["name"] == "soupsieve"
    assert removed_packages[0]["version"] == "2.9.2" and added_packages[0]["version"] == "4.1"
    before_python = {r["path"]: r["sha256"] for r in base["python_files"]}
    after_python = {r["path"]: r["sha256"] for r in current["python_files"]}
    delta = {
        p: {"before": before_python.get(p), "after": after_python.get(p)}
        for p in before_python.keys() | after_python.keys()
        if before_python.get(p) != after_python.get(p)
    }
    assert delta == receipt["python_file_delta"]
    assert all(p.startswith("/opt/venv/lib/python3.11/site-packages/soupsieve/") for p in delta)
    fs_delta = read(directory / "filesystem-delta.json")
    assert not fs_delta["unexpected_paths"]
    manifest = json.loads(
        gzip.decompress((directory / "candidate-filesystem/runtime-manifest.json.gz").read_bytes())
    )
    actual_files = {row["path"]: row for row in manifest}
    (wheel,) = (directory / "context").glob("soupsieve-4.1-*.whl")
    assert sha(wheel) == receipt["wheel_sha256"]
    matches = []
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.endswith("/") or name.endswith("/RECORD"):
                continue
            raw = archive.read(name)
            path = "opt/venv/lib/python3.11/site-packages/" + name
            assert actual_files[path]["sha256"] == hashlib.sha256(raw).hexdigest(), path
            matches.append({"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
    assert any("/licenses/" in row["path"] for row in matches)
    EVIDENCE.mkdir(exist_ok=True)
    for source, name, compress in [
        ("candidate-receipt.json", "candidate-receipt.json", False),
        ("base-image-config.raw.json", "base-image-config.raw.json", False),
        ("image-config.raw.json", "image-config.raw.json", False),
        ("base-runtime-inventory.json", "base-runtime-inventory.json.gz", True),
        ("runtime-inventory.json", "runtime-inventory.json.gz", True),
        ("runtime-probe.json", "runtime-probe.json", False),
        ("filesystem-delta.json", "filesystem-delta.json", False),
        ("scan/raw.json", "raw-trivy.json.gz", True),
        ("scan/scan-receipt.json", "scan-receipt.json", False),
        ("scan/scanner-version.log", "scanner-version.log", False),
        ("wheel-source.json", "wheel-source.json", False),
    ]:
        copy_bytes(directory / source, name, compress)
    write(
        EVIDENCE / "official-wheel-runtime-match.json",
        {
            "wheel_sha256": sha(wheel),
            "all_wheel_payload_files_except_generated_RECORD_match": True,
            "matched_files": matches,
            "native_payloads_added": False,
            "application_imported": False,
        },
    )
    write(
        EVIDENCE / "fresh-scan-delta.json",
        {
            "old_profile": OLD,
            "old_profile_sha256": OLD_SHA,
            "old_findings": 94,
            "new_findings": 92,
            "removed_exact_old_soupsieve_rows": removed,
            "new_unreviewed_rows": [],
            "other92_complete_tuples_retained": True,
            "removed_package": removed_packages[0],
            "added_package": added_packages[0],
            "raw_scan_sha256": sha(directory / "scan/raw.json"),
            "raw_report_modified": False,
            "scanner_secrets": 0,
        },
    )
    additional = copy.deepcopy(old_review["additional_evidence_files"])
    changed = []
    for item in additional:
        actual = sha(ROOT / item["path"])
        if actual != item["sha256"]:
            changed.append(item["path"])
            assert item["path"] == "uv.lock"
            item["sha256"] = actual
    assert changed == ["uv.lock"]
    extra = [
        ROOT / OLD,
        ROOT / old["source_review_evidence"]["path"],
        ROOT / "pyproject.toml",
        ROOT / "scripts/prepare_agent_soupsieve_20261115.py",
        ROOT / "scripts/generate_agent_soupsieve41_profile_20261115.py",
        *sorted(EVIDENCE.iterdir()),
    ]
    for path in extra:
        name = path.relative_to(ROOT).as_posix()
        if name not in {r["path"] for r in additional}:
            additional.append({"path": name, "sha256": sha(path)})
    review = {
        "schema": "agent-soupsieve41-measured-fixed-package-review-v1",
        "actor": "/root/milvus_build",
        "actor_kind": "automated_technical_source_review",
        "parent_independent_review_required_before_publication": True,
        "risk_acceptance": False,
        "binary_redistribution_approved": False,
        "retained_profile": {"path": OLD, "sha256": OLD_SHA},
        "retained_profile_is_historical_evidence_not_current_approval": True,
        "new_python_source_closure": current["python_source_closure"],
        "retained_native_elf_closure": old["native_elf_closure"],
        "all515_actual_native_files_byte_identical": True,
        "only_pure_soupsieve_and_its_dist_info_changed": True,
        "application_input_metadata_changes": ["pyproject.toml", "uv.lock", "source-fingerprint.json"],
        "upstream_package_upgraded": "2.9.2 -> official locked 4.1 wheel",
        "older_primary_advisories": "deployment/security/agent-soupsieve-paths-20261113-evidence/primary-sources.json",
        "css_unreachable_exception_no_longer_used": True,
        "all92_other_prior_exact_dispositions_retained": True,
        "raw_counts_are_not_zero": True,
        "application_behavior_acceptance_separate": True,
        "layered_snapshot_not_distribution_artifact": True,
        "final_scratch_flatten_and_identity_bound_scan_required": True,
        "additional_evidence_files": additional,
    }
    write(REVIEW, review)
    profile = copy.deepcopy(old)
    profile.update(
        name=NAME,
        review_scope="Exact measured SoupSieve4.1 package upgrade over the accepted515-ELF native base; complete92 original findings preserved with exact old dispositions. No CSS-unreachable exception. Final merged bytes and all inventory remain strict.",
        python_source_closure=current["python_source_closure"],
        packages=packages,
        findings=[row for row in old["findings"] if row["tuple"]["package"] != "soupsieve"],
        source_review_evidence={"path": REVIEW.relative_to(ROOT).as_posix(), "sha256": sha(REVIEW)},
    )
    assert Counter(gate.canonical(row["tuple"]) for row in profile["findings"]) == new_counter
    write(PROFILE, profile)
    print(
        json.dumps(
            {
                "profile": NAME,
                "profile_path": PROFILE.relative_to(ROOT).as_posix(),
                "profile_sha256": sha(PROFILE),
                "review_sha256": sha(REVIEW),
                "findings": len(findings),
                "packages": len(packages),
                "old_profile_preserved": sha(ROOT / OLD) == OLD_SHA,
                "paid_requests": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
