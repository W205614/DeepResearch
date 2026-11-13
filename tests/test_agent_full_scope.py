import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from scripts import check_agent_full_scope as gate


@pytest.fixture(params=[name for name in gate.PROFILE_NAMES if name != gate.PREVIOUS_SOUP_PROFILE_NAME])
def reviewed_inputs(request):
    profile = gate.load_profile(request.param)
    now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    config = copy.deepcopy(profile["runtime_config"])
    image_id = "sha256:" + "a" * 64
    diff_ids = ["sha256:" + "b" * 64]
    inspection = {
        "Id": image_id,
        "Os": "linux",
        "Architecture": "amd64",
        "RootFS": {"Layers": diff_ids},
        "Config": config,
    }
    results = []
    for scope in ["os-pkgs", "lang-pkgs"]:
        package_type = "debian" if scope == "os-pkgs" else "python-pkg"
        packages = [
            {
                "Name": item["name"],
                "Version": item["version"],
                "Identifier": {"PURL": item["purl"]},
                "Arch": item["architecture"],
                "SrcName": item["source"],
                "SrcVersion": item["source_version"],
                "FilePath": item["file_path"],
            }
            for item in profile["packages"]
            if item["class"] == scope
        ]
        findings = [
            {
                "VulnerabilityID": item["tuple"]["id"],
                "PkgName": item["tuple"]["package"],
                "InstalledVersion": item["tuple"]["version"],
                "PkgID": item["tuple"]["package_id"],
                "PkgIdentifier": {"PURL": item["tuple"]["purl"]},
                "Severity": item["tuple"]["severity"],
                "Status": item["tuple"]["status"],
                "DataSource": item["tuple"]["data_source"],
            }
            for item in profile["findings"]
            if item["tuple"]["class"] == scope
        ]
        results.append(
            {
                "Target": scope,
                "Class": scope,
                "Type": package_type,
                "Packages": packages,
                "Vulnerabilities": findings,
            }
        )
    report = {
        "SchemaVersion": 2,
        "ArtifactType": "container_image",
        "ReportID": "review-fixture",
        "CreatedAt": now.isoformat(),
        "Trivy": {"Version": "0.75.0"},
        "Metadata": {
            "ImageID": image_id,
            "DiffIDs": diff_ids,
            "ImageConfig": {
                "os": "linux",
                "architecture": "amd64",
                "config": config,
                "rootfs": {"type": "layers", "diff_ids": diff_ids},
            },
        },
        "Results": results,
    }
    sqlite = profile["sqlite"]
    runtime = {
        "native_elf_closure": profile["native_elf_closure"],
        "python_source_closure": profile["python_source_closure"],
        "official_glibc_items": len(profile["official_glibc_payload"]),
        "official_glibc_mismatches": [],
        "locale_trees": [],
        "lc_messages": "C",
        "bzip2recover": [],
        "uid": 10001,
        "sqlite": {
            "version": sqlite["version"],
            "loaded": [{"path": sqlite["library"], "sha256": sqlite["sha256"]}],
            "extension_errors": ["no such table: zipfile", "no such function: zipfile", "not authorized"],
        },
    }
    actual_raw = json.dumps(report["Metadata"]["ImageConfig"]).encode()
    return [
        copy.deepcopy(report),
        copy.deepcopy(inspection),
        copy.deepcopy(runtime),
        profile,
        now,
        actual_raw,
    ]


def decide(inputs, *, scan_source="docker"):
    report, inspection, runtime, profile, now, actual_raw = inputs
    return gate.adjudicate(
        report, inspection, runtime, profile, actual_config_raw=actual_raw, scan_source=scan_source, now=now
    )


def test_exact_review_keeps_original_findings_and_is_not_raw_zero(reviewed_inputs):
    verdict = decide(reviewed_inputs)
    expected_count = (94 if reviewed_inputs[3]['name'] == gate.SOUP_PROFILE_NAME
                      else 88 if reviewed_inputs[3]['name'] == gate.PROFILE_NAME else 92)
    assert verdict["passed"] and verdict["raw_findings"] == expected_count
    assert verdict["unresolved_applicable_findings"] == 0
    assert not verdict["raw_zero_claim"] and not verdict["risk_acceptance"]


@pytest.mark.parametrize(
    "change", ["add-low", "remove-low", "severity", "package-version", "purl", "data-source"]
)
def test_every_new_or_changed_full_severity_tuple_blocks(reviewed_inputs, change):
    findings = reviewed_inputs[0]["Results"][0]["Vulnerabilities"]
    low = next(item for item in findings if item["Severity"] == "LOW")
    if change == "add-low":
        findings.append(low | {"VulnerabilityID": "CVE-2099-0001"})
    elif change == "remove-low":
        findings.remove(low)
    elif change == "severity":
        low["Severity"] = "MEDIUM"
    elif change == "package-version":
        low["InstalledVersion"] = "unreviewed"
    elif change == "purl":
        low["PkgIdentifier"]["PURL"] = "pkg:generic/unreviewed"
    else:
        low["DataSource"]["ID"] = "unreviewed"
    with pytest.raises(ValueError, match="finding tuple"):
        decide(reviewed_inputs)


def test_any_secret_blocks_even_with_all_reviewed_vulnerabilities(reviewed_inputs):
    reviewed_inputs[0]["Results"][0]["Secrets"] = [{"Severity": "LOW", "Match": "synthetic-secret"}]
    with pytest.raises(ValueError, match="secret"):
        decide(reviewed_inputs)


@pytest.mark.parametrize(
    "field", ["ImageID", "DiffIDs", "scanner", "package-inventory", "scan-coverage", "stale"]
)
def test_fresh_complete_scan_must_bind_the_actual_image(reviewed_inputs, field):
    report = reviewed_inputs[0]
    if field == "ImageID":
        report["Metadata"]["ImageID"] = "sha256:" + "f" * 64
    elif field == "DiffIDs":
        report["Metadata"]["DiffIDs"] = []
    elif field == "scanner":
        report["Trivy"]["Version"] = "unreviewed"
    elif field == "package-inventory":
        report["Results"][1]["Packages"][0]["Version"] = "unreviewed"
    elif field == "scan-coverage":
        report["Results"].pop()
    else:
        report["CreatedAt"] = (reviewed_inputs[4] - timedelta(days=2)).isoformat()
    with pytest.raises(ValueError):
        decide(reviewed_inputs)


@pytest.mark.parametrize(
    "change", ["native", "python", "glibc", "locale", "recover", "sqlite", "extension", "root"]
)
def test_actual_runtime_changes_require_new_review(reviewed_inputs, change):
    runtime = reviewed_inputs[2]
    if change == "native":
        runtime["native_elf_closure"]["sha256"] = "f" * 64
    elif change == "python":
        runtime["python_source_closure"]["files"] += 1
    elif change == "glibc":
        runtime["official_glibc_mismatches"] = ["/usr/lib/x86_64-linux-gnu/libc.so.6"]
    elif change == "locale":
        runtime["locale_trees"] = ["/usr/lib/locale"]
    elif change == "recover":
        runtime["bzip2recover"] = ["/usr/bin/bzip2recover"]
    elif change == "sqlite":
        runtime["sqlite"]["loaded"][0]["sha256"] = "f" * 64
    elif change == "extension":
        runtime["sqlite"]["extension_errors"][0] = "UNEXPECTED_SUCCESS"
    else:
        runtime["uid"] = 0
    with pytest.raises(ValueError):
        decide(reviewed_inputs)


def test_new_image_identity_requires_its_own_scan_binding_and_same_runtime(reviewed_inputs):
    report, inspection, runtime, profile, _, _ = reviewed_inputs
    replacement_id = "sha256:" + "e" * 64
    assert replacement_id not in profile["baseline_evidence_only"].values()
    inspection["Id"] = report["Metadata"]["ImageID"] = replacement_id
    inspection["RootFS"]["Layers"] = report["Metadata"]["DiffIDs"] = ["sha256:" + "d" * 64]
    report["Metadata"]["ImageConfig"]["rootfs"]["diff_ids"] = inspection["RootFS"]["Layers"]
    reviewed_inputs[5] = json.dumps(report["Metadata"]["ImageConfig"]).encode()
    assert decide(reviewed_inputs)["image_id"] == replacement_id
    runtime["native_elf_closure"]["files"] += 1
    with pytest.raises(ValueError, match="ELF"):
        decide(reviewed_inputs)


def test_changed_runtime_command_is_not_just_release_metadata(reviewed_inputs):
    reviewed_inputs[1]["Config"]["Cmd"] = ["python", "-c", "arbitrary_code()"]
    with pytest.raises(ValueError, match="config"):
        decide(reviewed_inputs)


def test_profile_is_explicit_and_source_evidence_is_pinned(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="Unknown"):
        gate.load_profile("arbitrary-waiver")
    changed = tmp_path / "profile.json"
    changed.write_text(json.dumps({"risk_acceptance": True}))
    monkeypatch.setattr(gate, "PROFILE_PATH", changed)
    with pytest.raises(ValueError, match="profile changed"):
        gate.load_profile(gate.PROFILE_NAME)


def test_retained_soupsieve_profile_rejects_changed_current_consumer():
    assert hashlib.sha256(gate.PREVIOUS_SOUP_PROFILE_PATH.read_bytes()).hexdigest() == gate.PREVIOUS_SOUP_PROFILE_SHA256
    historical = json.loads(gate.PREVIOUS_SOUP_PROFILE_PATH.read_bytes())
    evidence = historical['source_review_evidence']
    assert hashlib.sha256((gate.ROOT / evidence['path']).read_bytes()).hexdigest() == evidence['sha256']
    with pytest.raises(ValueError, match='supporting source evidence changed'):
        gate.load_profile(gate.PREVIOUS_SOUP_PROFILE_NAME)


@pytest.mark.parametrize('reviewed_inputs', [gate.SOUP_PROFILE_NAME], indirect=True)
def test_record_metadata_profile_preserves_security_scope_and_rejects_previous_python(reviewed_inputs):
    current = reviewed_inputs[3]
    historical = json.loads(gate.PREVIOUS_SOUP_PROFILE_PATH.read_bytes())
    for field in ('findings', 'packages', 'native_elf_closure', 'runtime_config', 'official_glibc_payload', 'sqlite'):
        assert current[field] == historical[field]
    assert decide(reviewed_inputs)['passed']
    reviewed_inputs[2]['python_source_closure'] = historical['python_source_closure']
    with pytest.raises(ValueError, match='Python source closure'):
        decide(reviewed_inputs)


def test_ordinary_profile_preserves_findings_and_rejects_the_old_python_closure(reviewed_inputs):
    ordinary = gate.load_profile(gate.ORDINARY_PROFILE_NAME)
    original = gate.load_profile(gate.PROFILE_NAME)
    assert ordinary['findings'][:88] == original['findings']
    assert {(row['tuple']['id'], row['tuple']['package'], row['tuple']['severity'])
            for row in ordinary['findings'][88:]} == {
        (cve, package, 'UNKNOWN')
        for cve in ('CVE-2026-100290', 'CVE-2026-115819')
        for package in ('libc6', 'libc-gconv-modules-extra')
    }
    assert all(row['upstream_package_affected'] and not row['binary_fixed_claim']
               and not row['risk_acceptance'] for row in ordinary['findings'][88:])
    assert ordinary['packages'] == original['packages']
    assert ordinary['native_elf_closure'] == original['native_elf_closure']
    other = ordinary if reviewed_inputs[3]['name'] == gate.PROFILE_NAME else original
    reviewed_inputs[2]['python_source_closure'] = copy.deepcopy(other['python_source_closure'])
    with pytest.raises(ValueError, match='Python source closure'):
        decide(reviewed_inputs)


def test_ordinary_profile_source_snapshot_matches_historical_runtime_manifest():
    profile = gate.load_profile(gate.ORDINARY_PROFILE_NAME)
    review = json.loads((gate.ROOT / profile['source_review_evidence']['path']).read_bytes())
    change = review['source_change']
    snapshot = change['source_snapshot']
    assert change['runtime_path'] == '/app/' + change['path']
    assert snapshot['git_path'] == change['path']
    assert Path(snapshot['path']).suffix == '.txt'
    assert hashlib.sha256((gate.ROOT / snapshot['path']).read_bytes()).hexdigest() == snapshot['sha256']
    assert snapshot['sha256'] == change['after_sha256'] == review['complete_backend_manifest'][change['path']]
    assert {'path': snapshot['path'], 'sha256': snapshot['sha256']} in review['additional_evidence_files']
    assert not any(row['path'] == change['path'] for row in review['additional_evidence_files'])
    assert profile['python_source_closure'] == review['actual_snapshot']['python_source_closure']


def test_current_profile_preserves_exact_five_observed_severity_transitions():
    previous = gate.load_profile(gate.ORDINARY_PROFILE_NAME)
    current = gate.load_profile(gate.CURRENT_PROFILE_NAME)
    changes = []
    for before, after in zip(previous['findings'], current['findings'], strict=True):
        if before != after:
            expected = copy.deepcopy(before)
            expected['tuple']['severity'] = 'MEDIUM'
            expected['raw_severity_unchanged'] = 'MEDIUM'
            assert before['tuple']['severity'] == 'UNKNOWN'
            assert before['tuple']['package'] == 'libssl3t64'
            assert before['tuple']['version'] == '3.5.7-1~deb13u3'
            assert after == expected
            changes.append(before['tuple']['id'])
    assert set(changes) == {
        'CVE-2026-107496', 'CVE-2026-88822', 'CVE-2026-88823',
        'CVE-2026-95691', 'CVE-2026-95692',
    }
    assert len(changes) == 5
    assert current['packages'] == previous['packages']
    assert current['native_elf_closure'] == previous['native_elf_closure']
    assert current['python_source_closure'] != previous['python_source_closure']


@pytest.mark.parametrize('change', ['old-severity', 'new-status', 'old-python'])
@pytest.mark.parametrize('reviewed_inputs', [gate.CURRENT_PROFILE_NAME], indirect=True)
def test_current_profile_cannot_accept_old_or_unreviewed_metadata(reviewed_inputs, change):
    if change == 'old-python':
        reviewed_inputs[2]['python_source_closure'] = gate.load_profile(
            gate.ORDINARY_PROFILE_NAME)['python_source_closure']
    else:
        row = next(v for r in reviewed_inputs[0]['Results'] for v in r['Vulnerabilities']
                   if v['VulnerabilityID'] == 'CVE-2026-107496')
        if change == 'old-severity':
            row['Severity'] = 'UNKNOWN'
        else:
            row['Status'] = 'fixed'
    with pytest.raises(ValueError):
        decide(reviewed_inputs)


def test_current_source_review_binds_complete_closure_and_two_source_snapshots():
    profile = gate.load_profile(gate.PROVIDER_PROFILE_NAME)
    review = json.loads((gate.ROOT / profile['source_review_evidence']['path']).read_bytes())
    assert review['inherited_native_and_ffi_review']['python_source_closure'] == profile['python_source_closure']
    assert review['inherited_native_and_ffi_review']['native_elf_closure'] == profile['native_elf_closure']
    assert set(review['source_changes']) == {
        '/app/backend/infrastructure/providers.py', '/app/backend/research/evidence_policy.py',
    }
    assert review['inherited_native_and_ffi_review']['unchanged_python_files'] == 5970
    for path, delta in review['source_changes'].items():
        source = next(item for item in review['additional_evidence_files']
                      if item['path'].endswith(Path(path).name + '.txt'))
        assert source['sha256'] == delta['after']
        assert hashlib.sha256((gate.ROOT / source['path']).read_bytes()).hexdigest() == delta['after']


def test_reference_locator_profile_preserves_all_retained_technical_facts():
    previous = gate.load_profile(gate.PROVIDER_PROFILE_NAME)
    current = gate.load_profile(gate.REFERENCE_PROFILE_NAME)
    expected = copy.deepcopy(previous)
    for field in ('name', 'python_source_closure', 'source_review_evidence', 'review_scope', 'baseline_evidence_only'):
        expected[field] = current[field]
    assert current == expected
    review = json.loads((gate.ROOT / current['source_review_evidence']['path']).read_bytes())
    assert set(review['source_changes']) == {'/app/backend/research/evidence_policy.py'}
    assert review['inherited_native_and_ffi_review']['unchanged_python_files'] == 5971
    assert review['inherited_native_and_ffi_review']['complete_python_feature_matches_exact_equal'] is True
    change = review['source_changes']['/app/backend/research/evidence_policy.py']
    source = next(item for item in review['additional_evidence_files']
                  if item['path'].startswith('deployment/security/agent-reference-locator-20261113-evidence/')
                  and item['path'].endswith('evidence_policy.py.txt'))
    assert source['sha256'] == change['after']
    assert hashlib.sha256((gate.ROOT / source['path']).read_bytes()).hexdigest() == change['after']
    assert current['python_source_closure']['files'] == previous['python_source_closure']['files'] == 5972
    assert current['python_source_closure'] != previous['python_source_closure']


@pytest.mark.parametrize('reviewed_inputs', [gate.REFERENCE_PROFILE_NAME], indirect=True)
def test_reference_locator_review_rejects_previous_python_closure(reviewed_inputs):
    reviewed_inputs[2]['python_source_closure'] = gate.load_profile(gate.PROVIDER_PROFILE_NAME)['python_source_closure']
    with pytest.raises(ValueError, match='Python source closure'):
        decide(reviewed_inputs)


def test_structured_locator_profile_retains_exact_technical_facts_and_bounds():
    previous = gate.load_profile(gate.REFERENCE_PROFILE_NAME)
    current = gate.load_profile(gate.STRUCTURED_PROFILE_NAME)
    expected = copy.deepcopy(previous)
    for field in ('name', 'python_source_closure', 'source_review_evidence', 'review_scope', 'baseline_evidence_only'):
        expected[field] = current[field]
    assert current == expected
    review = json.loads((gate.ROOT / current['source_review_evidence']['path']).read_bytes())
    assert set(review['source_changes']) == {'/app/backend/research/evidence_policy.py'}
    assert review['inherited_native_and_ffi_review']['unchanged_python_files'] == 5971
    comparison_file = next(item for item in review['additional_evidence_files']
                           if item['path'].startswith('deployment/security/agent-structured-locator-20261113-evidence/')
                           and item['path'].endswith('source-native-comparison.json'))
    comparison = json.loads((gate.ROOT / comparison_file['path']).read_bytes())
    assert comparison['complete_python_feature_matches_exact_equal'] is True
    assert comparison['all_515_native_files_exact_equal'] is True
    assert comparison['structured_delta_AST_review']['replaced_assignments'] == ['locator', 'citation_field']
    assert comparison['structured_delta_AST_review']['new_imports_or_native_FFI_calls'] is False
    assert current['python_source_closure']['files'] == previous['python_source_closure']['files'] == 5972
    assert current['python_source_closure'] != previous['python_source_closure']


@pytest.mark.parametrize('reviewed_inputs', [gate.STRUCTURED_PROFILE_NAME], indirect=True)
def test_structured_locator_review_rejects_previous_python_closure(reviewed_inputs):
    reviewed_inputs[2]['python_source_closure'] = gate.load_profile(gate.REFERENCE_PROFILE_NAME)['python_source_closure']
    with pytest.raises(ValueError, match='Python source closure'):
        decide(reviewed_inputs)


def test_local_attribution_profile_keeps_all_prior_technical_facts():
    previous = gate.load_profile(gate.STRUCTURED_PROFILE_NAME)
    current = gate.load_profile(gate.CURRENT_PROFILE_NAME)
    expected = copy.deepcopy(previous)
    for field in ('name', 'python_source_closure', 'source_review_evidence', 'review_scope', 'baseline_evidence_only'):
        expected[field] = current[field]
    assert current == expected
    review = json.loads((gate.ROOT / current['source_review_evidence']['path']).read_bytes())
    assert set(review['source_changes']) == {'/app/backend/research/evidence_policy.py'}
    assert review['inherited_native_and_ffi_review']['unchanged_python_files'] == 5971
    assert current['native_elf_closure']['files'] == 515
    assert current['python_source_closure']['files'] == 5972
    assert current['python_source_closure'] != previous['python_source_closure']


def test_ordinary_profile_rejects_modified_historical_source_snapshot(monkeypatch):
    profile = gate.load_profile(gate.ORDINARY_PROFILE_NAME)
    review = json.loads((gate.ROOT / profile['source_review_evidence']['path']).read_bytes())
    snapshot = gate.ROOT / review['source_change']['source_snapshot']['path']
    original = Path.read_bytes

    def read_bytes(path):
        raw = original(path)
        return raw + b'changed historical snapshot' if path == snapshot else raw

    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    with pytest.raises(ValueError, match='supporting source evidence changed'):
        gate.load_profile(gate.ORDINARY_PROFILE_NAME)


def test_ordinary_profile_does_not_pin_the_moving_checkout_as_old_runtime_evidence(monkeypatch):
    profile = gate.load_profile(gate.ORDINARY_PROFILE_NAME)
    original = Path.read_bytes
    current_policy = gate.ROOT / 'backend/research/evidence_policy.py'

    def read_bytes(path):
        raw = original(path)
        return raw + b'legitimate later checkout change' if path == current_policy else raw

    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    assert gate.load_profile(gate.ORDINARY_PROFILE_NAME) == profile


@pytest.mark.parametrize('reviewed_inputs', [gate.ORDINARY_PROFILE_NAME], indirect=True)
def test_changed_policy_in_the_measured_python_manifest_cannot_use_old_review(reviewed_inputs):
    profile = reviewed_inputs[3]
    review = json.loads((gate.ROOT / profile['source_review_evidence']['path']).read_bytes())
    projection = review['source_change']['unapproved_policy_projection']
    assert not projection['candidate_built'] and not projection['model_tested'] and not projection['security_approved']
    assert projection['policy_sha256'] != review['source_change']['after_sha256']
    assert projection['python_source_closure']['files'] == profile['python_source_closure']['files'] == 5972
    reviewed_inputs[2]['python_source_closure'] = projection['python_source_closure']
    with pytest.raises(ValueError, match='Python source closure'):
        decide(reviewed_inputs)


@pytest.mark.parametrize("target", ["review", "support"])
def test_new_openssl_review_and_supporting_evidence_are_pinned(monkeypatch, target):
    profile = gate.load_profile(gate.PROFILE_NAME)
    review_path = gate.ROOT / profile["source_review_evidence"]["path"]
    review = json.loads(review_path.read_bytes())
    changed = review_path if target == "review" else gate.ROOT / review["additional_evidence_files"][0]["path"]
    original = Path.read_bytes

    def read_bytes(path):
        raw = original(path)
        return raw + b"changed" if path == changed else raw

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with pytest.raises(ValueError, match="source evidence changed"):
        gate.load_profile(gate.PROFILE_NAME)


@pytest.mark.parametrize("where", ["report", "result"])
@pytest.mark.parametrize("marker", ["Errors", "ModifiedFindings", "SuppressedFindings"])
@pytest.mark.parametrize("value", [["synthetic error or filtered finding"], None, False])
def test_error_and_suppression_markers_cannot_pass(reviewed_inputs, where, marker, value):
    report = reviewed_inputs[0]
    record = report if where == "report" else report["Results"][0]
    record[marker] = value
    with pytest.raises(ValueError, match="Incomplete|filtered"):
        decide(reviewed_inputs)


@pytest.mark.parametrize(
    "field,value", [("ArtifactType", "filesystem"), ("ReportID", ""), ("ReportID", None)]
)
def test_original_container_report_identity_is_required(reviewed_inputs, field, value):
    reviewed_inputs[0][field] = value
    with pytest.raises(ValueError):
        decide(reviewed_inputs)


def test_dependency_errors_do_not_disappear_in_package_tuple_projection(reviewed_inputs):
    reviewed_inputs[0]["Results"][0]["Packages"][0]["DepsErrors"] = ["incomplete"]
    with pytest.raises(ValueError, match="dependency"):
        decide(reviewed_inputs)


@pytest.mark.parametrize(
    "change", [None, "name", "image", "actual-image", "purpose", "project", "network", "business-bind"]
)
def test_probe_cleanup_checks_exact_ownership_before_removing(monkeypatch, change):
    name, image = "dr-agent-fullscope-test", "sha256:" + "a" * 64
    container = {
        "Id": "owned-container-id",
        "Image": image,
        "Name": "/" + name,
        "Config": {
            "Image": image,
            "Labels": {"org.deepresearch.purpose": gate.PROBE_PURPOSE, "com.docker.compose.project": name},
        },
        "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Binds": []},
        "Mounts": [{"Type": "tmpfs", "Destination": "/tmp"}],
    }
    if change == "name":
        container["Name"] = "/daily-container"
    elif change == "image":
        container["Config"]["Image"] = "another-image"
    elif change == "actual-image":
        container["Image"] = "another-image"
    elif change == "purpose":
        container["Config"]["Labels"]["org.deepresearch.purpose"] = "daily"
    elif change == "project":
        container["Config"]["Labels"]["com.docker.compose.project"] = "deepresearch"
    elif change == "network":
        container["HostConfig"]["NetworkMode"] = "daily-network"
    elif change == "business-bind":
        container["Mounts"] = [{"Type": "bind", "Destination": "/data"}]
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps([container]), stderr="")

    monkeypatch.setattr(gate.subprocess, "run", run)
    if change:
        with pytest.raises(ValueError, match="Refusing cleanup"):
            gate.cleanup_probe(name, image)
        assert len(calls) == 1
    else:
        gate.cleanup_probe(name, image)
        assert calls[-1] == ["docker", "rm", "-f", "owned-container-id"]


def test_already_removed_probe_is_not_removed_by_name(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="No such container: own-probe")

    monkeypatch.setattr(gate.subprocess, "run", run)
    gate.cleanup_probe("own-probe", "sha256:" + "a" * 64)
    assert len(calls) == 1


def test_archive_scan_uses_actual_portable_config_identity_without_rewriting_engine_id(reviewed_inputs):
    inspection = copy.deepcopy(reviewed_inputs[1])
    portable = "sha256:" + hashlib.sha256(reviewed_inputs[5]).hexdigest()
    reviewed_inputs[0]["Metadata"]["ImageID"] = portable
    verdict = decide(reviewed_inputs, scan_source="archive")
    assert verdict["image_id"] == inspection["Id"] != portable
    assert verdict["portable_config_digest"] == portable
    assert reviewed_inputs[1] == inspection


@pytest.mark.parametrize(
    "change", ["raw-user", "raw-diffs", "raw-platform", "report-history", "claimed-digest"]
)
def test_raw_config_and_decoded_scan_identity_cannot_drift(reviewed_inputs, change):
    report = reviewed_inputs[0]
    actual = json.loads(reviewed_inputs[5])
    if change == "raw-user":
        actual["config"]["User"] = "0"
    elif change == "raw-diffs":
        actual["rootfs"]["diff_ids"] = []
    elif change == "raw-platform":
        actual["architecture"] = "arm64"
    elif change == "report-history":
        report["Metadata"]["ImageConfig"]["history"] = [{"created_by": "unreviewed"}]
    else:
        report["Metadata"]["ImageID"] = "sha256:" + "f" * 64
    reviewed_inputs[5] = json.dumps(actual).encode()
    with pytest.raises(ValueError):
        decide(reviewed_inputs)


@pytest.mark.parametrize("layout", ["classic", "containerd", "wrong-name"])
def test_actual_config_capture_reads_manifest_and_hashes_original_bytes(reviewed_inputs, monkeypatch, layout):
    raw, inspection = reviewed_inputs[5], reviewed_inputs[1]
    digest = hashlib.sha256(raw).hexdigest()
    name = digest + ".json" if layout == "classic" else "blobs/sha256/" + digest
    if layout == "wrong-name":
        name = "claimed-wrong-config.json"
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as archive:
        for path, data in [(name, raw), ("manifest.json", json.dumps([{"Config": name}]).encode())]:
            info = tarfile.TarInfo(path)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

    class SaveProcess:
        def __init__(self, command, **kwargs):
            assert command == ["docker", "image", "save", inspection["Id"]]
            self.stdout = io.BytesIO(content.getvalue())
            self.stderr = io.BytesIO()

        def wait(self, **kwargs):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(gate.subprocess, "Popen", SaveProcess)
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, json.dumps(inspection), ""),
    )
    if layout == "wrong-name":
        with pytest.raises(ValueError, match="archive digest"):
            gate.capture_actual_config(inspection)
    else:
        assert gate.capture_actual_config(inspection) == raw


@pytest.mark.parametrize("mode", ["docker", "archive"])
def test_history_precision_is_source_specific_and_preserves_actual_raw_bytes(reviewed_inputs, mode):
    actual = json.loads(reviewed_inputs[5])
    actual["created"] = "2026-11-10T10:11:12.987654321Z"
    actual["history"] = [{"created": "2026-11-10T10:11:12.123456789Z", "created_by": "known"}]
    raw = json.dumps(actual).encode()
    reviewed_inputs[5] = raw
    reviewed_inputs[0]["Metadata"]["ImageConfig"] = copy.deepcopy(actual)
    if mode == "docker":
        reviewed_inputs[0]["Metadata"]["ImageConfig"]["history"][0]["created"] = "2026-11-10T10:11:12Z"
    else:
        reviewed_inputs[0]["Metadata"]["ImageID"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    assert decide(reviewed_inputs, scan_source=mode)["passed"]
    assert reviewed_inputs[5] == raw


@pytest.mark.parametrize("change", ["docker-second", "docker-field", "top-created", "archive-truncation"])
def test_history_projection_does_not_ignore_any_other_field_or_time(reviewed_inputs, change):
    actual = json.loads(reviewed_inputs[5])
    actual["created"] = "2026-11-10T10:11:12.987654321Z"
    actual["history"] = [{"created": "2026-11-10T10:11:12.123456789Z", "created_by": "known"}]
    reviewed_inputs[5] = json.dumps(actual).encode()
    reported = copy.deepcopy(actual)
    reported["history"][0]["created"] = "2026-11-10T10:11:12Z"
    mode = "docker"
    if change == "docker-second":
        reported["history"][0]["created"] = "2026-11-10T10:11:13Z"
    elif change == "docker-field":
        reported["history"][0]["comment"] = "unreviewed-field"
    elif change == "top-created":
        reported["created"] = "2026-11-10T10:11:12Z"
    else:
        mode = "archive"
        reviewed_inputs[0]["Metadata"]["ImageID"] = "sha256:" + hashlib.sha256(reviewed_inputs[5]).hexdigest()
    reviewed_inputs[0]["Metadata"]["ImageConfig"] = reported
    with pytest.raises(ValueError, match="Raw scan"):
        decide(reviewed_inputs, scan_source=mode)
