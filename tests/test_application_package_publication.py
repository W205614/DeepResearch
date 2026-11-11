import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

from scripts import publish_application_packages as publication
from scripts.publish_application_packages import archive_image_id, publication_scan_verdict, validate_manifest


def test_archive_identity_uses_actual_config_bytes_for_both_docker_stores(tmp_path):
    raw = b'{"architecture":"amd64","os":"linux","config":{"Cmd":["true"]}}'
    digest = hashlib.sha256(raw).hexdigest()
    tag = "dr-package-candidate-web:v0.1.1"
    for config_name in [digest + ".json", "blobs/sha256/" + digest]:
        path = tmp_path / ("containerd.tar" if "/" in config_name else "classic.tar")
        with tarfile.open(path, "w") as archive:
            for name, content in [(config_name, raw), ("manifest.json", json.dumps([
                    {"Config": config_name, "RepoTags": [tag], "Layers": []}]).encode())]:
                item = tarfile.TarInfo(name)
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
        assert archive_image_id(path, tag) == "sha256:" + digest
        with pytest.raises(ValueError, match="accepted application tag"):
            archive_image_id(path, "another:tag")


def test_archive_identity_rejects_extra_tags_before_loading(tmp_path):
    raw = b'{"architecture":"amd64","os":"linux"}'
    tag = 'dr-package-candidate-web:v0.1.2-' + 'c' * 12
    path = tmp_path / 'extra-tag.tar'
    with tarfile.open(path, 'w') as archive:
        for name, content in [('config.json', raw), ('manifest.json', json.dumps([
                {'Config': 'config.json', 'RepoTags': [tag, 'dr-package-candidate-web:v0.1.2'],
                 'Layers': []}]).encode())]:
            item = tarfile.TarInfo(name)
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
    with pytest.raises(ValueError, match='only the accepted application tag'):
        archive_image_id(path, tag)


def manifest():
    def archive(name):
        return {"archive_sha256": "a" * 64,
                "parts": [{"name": name + ".part000", "sha256": "b" * 64, "bytes": 1024}]}

    return {"schema": "deepresearch-application-packages-v1", "source_sha": "c" * 40,
            "repository": "W205614/DeepResearch", "artifact_release": "artifact-applications-v0.1.1-r1",
            "version": "v0.1.1", "verification": {"licenses": True, "security": True, "runtime": True},
            "images": [{"service": service, "repository": "ghcr.io/w205614/deepresearch-" + service,
                        "image_id": "sha256:" + "d" * 64, "platform": "linux/amd64",
                        "archive_tag": "dr-package-candidate-" + service + ":v0.1.1", **archive(service)}
                       for service in ["agent", "backend", "web"]], "sources": archive("sources")}


def test_accepts_exact_three_image_release_manifest():
    validate_manifest(manifest(), "c" * 40, "artifact-applications-v0.1.1-r1")


def test_single_product_release_binds_the_same_application_version():
    data = manifest()
    data['artifact_release'] = 'v0.1.1'
    for image in data['images']:
        image['archive_tag'] += '-' + data['source_sha'][:12]
    validate_manifest(data, 'c' * 40, 'v0.1.1')
    data['artifact_release'] = 'v0.1.2'
    with pytest.raises(ValueError, match='Release tag and immutable application version differ'):
        validate_manifest(data, 'c' * 40, 'v0.1.2')


@pytest.mark.parametrize('suffix', ['', '-' + 'e' * 12, '-' + 'c' * 13])
def test_product_release_rejects_archive_tag_without_its_exact_source_suffix(suffix):
    data = manifest()
    data['artifact_release'] = data['version']
    for image in data['images']:
        image['archive_tag'] += '-' + data['source_sha'][:12]
    data['images'][0]['archive_tag'] = 'dr-package-candidate-agent:' + data['version'] + suffix
    with pytest.raises(ValueError, match='Invalid archive-local image tag'):
        validate_manifest(data, data['source_sha'], data['artifact_release'])


def test_historical_artifact_release_keeps_only_its_existing_unsuffixed_archive_tags():
    data = manifest()
    data['images'][0]['archive_tag'] += '-' + data['source_sha'][:12]
    with pytest.raises(ValueError, match='Invalid archive-local image tag'):
        validate_manifest(data, data['source_sha'], data['artifact_release'])


def test_technical_review_is_bound_to_exact_agent_profile():
    data = manifest()
    data['images'][0]['security_review_profile'] = 'agent-full-scope-20261110'
    validate_manifest(data, 'c' * 40, data['artifact_release'])
    data['images'][1]['security_review_profile'] = 'agent-full-scope-20261110'
    with pytest.raises(ValueError, match='Only the exact Agent technical review'):
        validate_manifest(data, 'c' * 40, data['artifact_release'])
    data['images'][1].pop('security_review_profile')
    data['images'][0]['security_review_profile'] = 'ignore-all'
    with pytest.raises(ValueError, match='Only the exact Agent technical review'):
        validate_manifest(data, 'c' * 40, data['artifact_release'])


def test_new_package_selectors_are_excluded_from_historical_grafana_replay(historical_grafana_maintenance_root):
    from scripts.check_grafana_active_source_retirement import ROOT, selector_files

    current = selector_files(ROOT)
    historical = selector_files(historical_grafana_maintenance_root)
    for path in ['.github/workflows/publish-application-packages.yml', 'deployment/compose/compose.packages.yaml']:
        assert path in current
        assert path not in historical


@pytest.mark.parametrize("change", ["source", "namespace", "duplicate-image", "incomplete-evidence", "path", "duplicate-part", "oversize", "mutable-version"])
def test_rejects_unbound_or_unsafe_publication_input(change):
    data = copy.deepcopy(manifest())
    if change == "source":
        data["source_sha"] = "e" * 40
    elif change == "namespace":
        data["images"][0]["repository"] = "ghcr.io/another-owner/deepresearch-agent"
    elif change == "duplicate-image":
        data["images"][1] = copy.deepcopy(data["images"][0])
    elif change == "incomplete-evidence":
        data["verification"]["licenses"] = False
    elif change == "path":
        data["sources"]["parts"][0]["name"] = "../private"
    elif change == "duplicate-part":
        data["sources"]["parts"][0]["name"] = data["images"][0]["parts"][0]["name"]
    elif change == "oversize":
        data["sources"]["parts"][0]["bytes"] = 65 * 1024**2
    else:
        data["version"] = "latest"
    with pytest.raises(ValueError):
        validate_manifest(data, "c" * 40, "artifact-applications-v0.1.1-r1")


def image_scan():
    return {"SchemaVersion": 2, "ArtifactType": "container_image", "Trivy": {"Version": "0.75.0"},
            "ReportID": "fresh-report", "CreatedAt": datetime.now(timezone.utc).isoformat(),
            "Metadata": {"ImageID": "sha256:" + "d" * 64, "DiffIDs": ["sha256:" + "e" * 64],
                         "ImageConfig": {"os": "linux", "architecture": "amd64"}},
            "Results": [{"Target": "image (debian 13)", "Class": "os-pkgs", "Type": "debian",
                         "Packages": [{"Name": "libc6", "Version": "2.41", "Release": "1"}]},
                        {"Target": "Python", "Class": "lang-pkgs", "Type": "python-pkg",
                         "Packages": [{"Name": "sample", "Version": "1.0"}]}]}


def scan_verdict(scan):
    now = datetime.now(timezone.utc)
    return publication_scan_verdict(scan, manifest()["images"][0], ["sha256:" + "e" * 64],
                                    now - timedelta(seconds=5), now + timedelta(seconds=5))


@pytest.mark.parametrize("severity", publication.SEVERITIES)
def test_every_raw_vulnerability_severity_blocks_publication(severity):
    scan = image_scan()
    scan["Results"][0]["Vulnerabilities"] = [{"VulnerabilityID": "CVE-TEST-1", "PkgName": "libc6",
                                            "InstalledVersion": "2.41-1", "Severity": severity}]
    verdict = scan_verdict(scan)
    assert verdict["passed"] is False
    assert verdict["raw_vulnerability_counts"] == {severity: 1}
    assert verdict["unresolved_vulnerabilities"] == scan["Results"][0]["Vulnerabilities"]
    assert verdict["applicability_exceptions_applied"] == []


def test_secret_values_are_never_copied_into_publication_verdict():
    scan = image_scan()
    scan["Results"][0]["Secrets"] = [{"Severity": "LOW", "Match": "fixture-private-secret-value"}]
    verdict = scan_verdict(scan)
    assert verdict["passed"] is False and verdict["unresolved_secret_count"] == 1
    assert "fixture-private-secret-value" not in json.dumps(verdict)


def test_complete_clean_publication_scan_passes_without_changing_report():
    scan = image_scan()
    before = copy.deepcopy(scan)
    assert scan_verdict(scan)["passed"] is True
    assert scan == before


@pytest.mark.parametrize("change", ["image", "layers", "platform", "stale", "version", "os-coverage",
                                   "language-coverage", "inventory", "suppressed", "scanner-errors",
                                   "unknown-severity", "unbound-package", "malformed-vulnerabilities"])
def test_incomplete_or_unbound_image_scan_cannot_authorize_publication(change):
    scan = image_scan()
    if change == "image":
        scan["Metadata"]["ImageID"] = "sha256:" + "f" * 64
    elif change == "layers":
        scan["Metadata"]["DiffIDs"] = []
    elif change == "platform":
        scan["Metadata"]["ImageConfig"]["architecture"] = "arm64"
    elif change == "stale":
        scan["CreatedAt"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    elif change == "version":
        scan["Trivy"]["Version"] = "0.74.0"
    elif change == "os-coverage":
        scan["Results"].pop(0)
    elif change == "language-coverage":
        scan["Results"].pop()
    elif change == "inventory":
        scan["Results"][0]["Packages"] = []
    elif change == "suppressed":
        scan["Results"][0]["SuppressedFindings"] = [{}]
    elif change == "scanner-errors":
        scan["Errors"] = ["fixture scan failure"]
    elif change == "malformed-vulnerabilities":
        scan["Results"][0]["Vulnerabilities"] = None
    else:
        scan["Results"][0]["Vulnerabilities"] = [{"VulnerabilityID": "CVE-TEST-1", "PkgName": "libc6",
                                                "InstalledVersion": "2.41-1", "Severity": "LOW"}]
        if change == "unknown-severity":
            scan["Results"][0]["Vulnerabilities"][0]["Severity"] = "not-a-severity"
        else:
            scan["Results"][0]["Vulnerabilities"][0]["InstalledVersion"] = "different"
    with pytest.raises(ValueError):
        scan_verdict(scan)


@pytest.mark.parametrize("secret,technical_failure", [(False, False), (True, False), (False, True), (True, True), (False, "interrupt"), (False, "runtime")])
def test_loaded_archive_is_scanned_and_blocked_before_any_registry_write(tmp_path, monkeypatch, secret, technical_failure):
    data = manifest()
    config = json.dumps({"os": "linux", "architecture": "amd64", "rootfs": {
        "type": "layers", "diff_ids": ["sha256:" + "e" * 64]}}).encode()
    config_name = hashlib.sha256(config).hexdigest() + ".json"
    for image in data["images"]:
        image["image_id"] = "sha256:" + hashlib.sha256(config).hexdigest()
    source_archive = tmp_path / "accepted.tar"
    with tarfile.open(source_archive, "w") as archive:
        for name, raw in [(config_name, config), ("manifest.json", json.dumps([{
                "Config": config_name, "RepoTags": [data["images"][0]["archive_tag"]], "Layers": []}]).encode())]:
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    raw_manifest = json.dumps(data).encode()
    output = tmp_path / "publication"
    calls, original_report = [], []
    monkeypatch.setattr(publication, "github", lambda path: {"workflow_runs": [
        {"name": name, "event": "push", "status": "completed", "conclusion": "success"}
        for name in ["CI", "Security"]]} if path.startswith("/actions/") else {"sha": "c" * 40} if path.startswith('/commits/') else {
            "draft": False, "published_at": "2026-11-10", "target_commitish": "c" * 40,
            "assets": [{"name": "publication-manifest.json", "size": len(raw_manifest)}]})
    monkeypatch.setattr(publication, "download", lambda asset, destination: destination.write_bytes(raw_manifest))
    monkeypatch.setattr(publication, "assemble", lambda *args: source_archive)
    monkeypatch.setattr(publication.subprocess, "check_output", lambda command, **kwargs:
        json.dumps([{"Id": data["images"][0]["image_id"]}]) if command[:3] == ["docker", "image", "inspect"] else "c" * 40)
    monkeypatch.setattr(publication, "registry_manifest", lambda *args: pytest.fail("Registry queried before failed gate"))
    monkeypatch.setenv("TRIVY_SKIP_FILES", "**/*")
    monkeypatch.setenv("TRIVY_IGNORE_UNFIXED", "true")
    if technical_failure:
        from scripts import check_agent_full_scope as scope

        data["images"][0]["security_review_profile"] = scope.PROFILE_NAME
        raw_manifest = json.dumps(data).encode()
        monkeypatch.setattr(scope, "load_profile", lambda name: {})
        monkeypatch.setattr(scope, "capture_actual_config", lambda inspection: config)
        monkeypatch.setattr(scope, "probe_image", lambda *args: {})

        def unreviewed(*args, **kwargs):
            if technical_failure == "interrupt":
                raise KeyboardInterrupt("Incomplete technical review")
            if technical_failure == "runtime":
                raise RuntimeError("Incomplete technical review")
            raise ValueError("Unreviewed finding in exact technical review")

        monkeypatch.setattr(scope, "adjudicate", unreviewed)

    def external_process(command, **kwargs):
        calls.append(command)
        if command[:3] == ["docker", "image", "save"]:
            shutil.copyfile(source_archive, command[4])
        elif command == ["trivy", "--version"]:
            return subprocess.CompletedProcess(command, 0, b"Version: 0.75.0\n", b"")
        elif command[:2] == ["trivy", "image"]:
            assert not any(key.startswith("TRIVY_") for key in kwargs["env"])
            assert command[command.index("--input") + 1] == str((output / "agent-loaded.tar").resolve())
            assert command[command.index("--scanners") + 1] == "vuln,secret"
            assert command[command.index("--severity") + 1] == "UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL"
            assert Path(command[command.index("--config") + 1]).read_bytes() == b"{}\n"
            assert Path(command[command.index("--ignorefile") + 1]).read_bytes() == b""
            scan = image_scan()
            scan["Metadata"]["ImageID"] = data["images"][0]["image_id"]
            if secret:
                scan["Results"][0]["Secrets"] = [{"Severity": "MEDIUM", "Match": "fixture-sensitive-match"}]
            elif technical_failure not in {"interrupt", "runtime"}:
                scan["Results"][0]["Vulnerabilities"] = [{"VulnerabilityID": "CVE-TEST-LOW", "PkgName": "libc6",
                    "InstalledVersion": "2.41-1", "Severity": "LOW"}]
            raw = json.dumps(scan).encode()
            original_report.append(raw)
            Path(command[command.index("--output") + 1]).write_bytes(raw)
        elif command[:2] != ["docker", "load"]:
            pytest.fail("Unexpected external process or registry write: " + str(command))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(publication.subprocess, "run", external_process)
    monkeypatch.setattr(sys, "argv", ["publish_application_packages.py", "--release-tag", data["artifact_release"],
        "--manifest-sha256", hashlib.sha256(raw_manifest).hexdigest(), "--output", str(output)])
    error = KeyboardInterrupt if technical_failure == "interrupt" else RuntimeError if technical_failure == "runtime" else ValueError
    message = "Incomplete technical review" if technical_failure in {"interrupt", "runtime"} else "Unreviewed finding" if technical_failure else "blocked by unresolved all-severity"
    with pytest.raises(error, match=message):
        publication.main()
    assert any(command[:2] == ["docker", "load"] for command in calls)
    assert any(command[:3] == ["docker", "image", "save"] for command in calls)
    assert not any(command[:2] in [["docker", "login"], ["docker", "tag"], ["docker", "push"]] for command in calls)
    directory = output / "security/agent"
    verdict = json.loads((directory / "verdict.json").read_bytes())
    assert verdict["passed"] is False
    assert verdict["raw_file"] == ("raw.private.json" if secret else "raw.json")
    assert (directory / ("raw.private.json" if secret else "raw.json")).read_bytes() == original_report[0]
    assert "fixture-sensitive-match" not in (directory / "verdict.json").read_text()
    assert (directory / "raw.json").exists() is not secret
