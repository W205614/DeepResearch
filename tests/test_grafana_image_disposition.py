from copy import deepcopy
from datetime import datetime
import hashlib
import json

import pytest

from scripts import grafana_image_disposition as gate
from scripts import fetch_approved_image as fetch
from scripts.image_disposition import DispositionError


@pytest.fixture(scope="module")
def inputs():
    binding = gate.load_binding(now=datetime.fromisoformat("2026-10-26T15:00:00+00:00"))
    source = gate.ROOT / gate.EVIDENCE
    data = [
        json.loads((source / n).read_bytes())
        for n in (
            "musl-fixed-trivy-full-raw.json",
            "musl-fixed-native-grype-full-raw.json",
            "musl-fixed-native-syft.json",
            "image-inspect.json",
        )
    ]
    return binding, data


def assess(binding, data, **kwargs):
    return gate.evaluate_scans(
        *data,
        binding,
        trivy_exit_code=kwargs.get("trivy_exit_code", 0),
        grype_exit_code=kwargs.get("grype_exit_code", 0),
    )


def test_real_complete_reports_keep_raw_and_remaining_medium(inputs):
    binding, data = inputs
    before = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    result = assess(binding, data)
    assert result["passed"] and result["unresolved_count"] == 0
    assert result["raw_counts"]["Trivy"] == {"CRITICAL": 2, "HIGH": 11, "MEDIUM": 20, "LOW": 2, "UNKNOWN": 5}
    assert result["raw_counts"]["Grype"] == {
        "Critical": 0,
        "High": 2,
        "Medium": 3,
        "Low": 0,
        "Unknown": 5,
        "Negligible": 0,
    }
    assert result["verified_fixed_counts"] == {"Trivy": 2, "Grype": 2}
    assert result["verified_not_affected_counts"] == {"Trivy": 16, "Grype": 5}
    assert len([r for r in result["remaining_unexcluded_medium_low"] if r["scanner"] == "Grype"]) == 3
    assert result["identified_local_source_tuple_count"] == 28
    assert before == hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize(
    "field,value",
    [
        ("VulnerabilityID", "CVE-2099-NEW"),
        ("PkgName", "new/package"),
        ("InstalledVersion", "v999"),
        ("Severity", "HIGH"),
    ],
)
def test_new_or_changed_blocking_tuple_never_inherits_approval(inputs, field, value):
    binding, original = inputs
    data = deepcopy(original)
    result = next(r for r in data[0]["Results"] if r.get("Vulnerabilities"))
    finding = deepcopy(result["Vulnerabilities"][0])
    finding[field] = value
    if field == "Severity":
        finding["VulnerabilityID"] = "CVE-2099-NEW"
    result["Vulnerabilities"].append(finding)
    assert not assess(binding, data)["passed"]


def test_new_grype_alias_or_cross_target_cannot_reuse_related_cve(inputs):
    binding, original = inputs
    for mutate in (
        lambda m: m["vulnerability"].update(id="GHSA-new-new-new"),
        lambda m: m["artifact"]["locations"][0].update(path="/usr/bin/other"),
    ):
        data = deepcopy(original)
        mutate(data[1]["matches"][0])
        assert not assess(binding, data)["passed"]


@pytest.mark.parametrize(
    "case",
    ["trivy_exit", "grype_exit", "missing_results", "coverage", "inspect", "native", "ignored", "source"],
)
def test_operational_unknown_missing_or_mismatched_evidence_blocks(inputs, case):
    binding, original = inputs
    data = deepcopy(original)
    kwargs = {}
    if case == "trivy_exit":
        kwargs["trivy_exit_code"] = 1
    elif case == "grype_exit":
        kwargs["grype_exit_code"] = 1
    elif case == "missing_results":
        data[0]["Results"] = []
    elif case == "coverage":
        data[0]["Results"][0]["Packages"] = []
    elif case == "inspect":
        data[3]["Id"] = "sha256:" + "a" * 64
    elif case == "native":
        for artifact in data[2]["artifacts"]:
            if artifact.get("type") == "binary" and artifact.get("name") == "grafana":
                artifact["version"] = "0.0.0"
    elif case == "ignored":
        data[1]["ignoredMatches"] = [{"vulnerability": {"id": "ignored"}}]
    elif case == "source":
        data[1]["source"]["target"]["imageID"] = "sha256:" + "b" * 64
    with pytest.raises((DispositionError, ValueError)):
        assess(binding, data, **kwargs)


def test_exact_local_source_identity_does_not_allow_new_target_or_similar_module(inputs):
    binding, data = inputs
    row = binding["inventory_identity"]["local_source_modules"][0]
    assert gate.recognized_local_identity(row["target"], row["module"], binding)
    assert not gate.recognized_local_identity("usr/bin/other", row["module"], binding)
    assert not gate.recognized_local_identity(row["target"], row["module"] + "-similar", binding)
    changed = deepcopy(data)
    for result in changed[0]["Results"]:
        for package in result.get("Packages", []):
            if package.get("Version") is None:
                package["Name"] += "-unreviewed"
                with pytest.raises(DispositionError, match="missing-version"):
                    assess(binding, changed)
                return
    pytest.fail("expected the real local source tuple")


def test_fake_replace_and_source_hash_drift_are_rejected(inputs, tmp_path, monkeypatch):
    binding, _ = inputs
    row = deepcopy(binding["inventory_identity"]["local_source_modules"][0])
    row.update(source_go_mod="module.go.mod", replace_file="parent.go.mod")
    (tmp_path / "module.go.mod").write_text(row["module_directive"] + "\n", encoding="utf-8")
    (tmp_path / "parent.go.mod").write_text(row["replace_directive"] + "\n", encoding="utf-8")
    fixture = {
        "binary_sha256": {row["target"]: row["elf_sha256"]},
        "inventory_identity": {"local_source_modules": [row]},
    }
    gate.validate_local_sources(fixture, tmp_path)
    (tmp_path / "parent.go.mod").write_text(row["module"] + " => ./fake\n", encoding="utf-8")
    with pytest.raises(DispositionError, match="replace"):
        gate.validate_local_sources(fixture, tmp_path)
    subject = deepcopy(binding)
    subject["evidence_files"] = {"proof.json": "0" * 64}
    subject["recipe_files"] = {}
    (tmp_path / "proof.json").write_bytes(b"changed source proof")
    policy = tmp_path / gate.POLICY
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({"binding": subject}), encoding="utf-8")
    monkeypatch.setattr(gate, "APPROVED_BINDING_SHA256", hashlib.sha256(gate.canonical(subject)).hexdigest())
    with pytest.raises(DispositionError, match="digest"):
        gate.load_binding(tmp_path, now=datetime.fromisoformat("2026-10-26T15:00:00+00:00"))


def test_expired_or_changed_binding_cannot_inherit_old_subject(inputs, tmp_path):
    binding, _ = inputs
    with pytest.raises(DispositionError, match="expired"):
        gate.load_binding(now=datetime.fromisoformat("2026-11-26T00:00:00+00:00"))
    changed = deepcopy(binding)
    changed["index_digest"] = "sha256:" + "f" * 64
    path = tmp_path / gate.POLICY
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"binding": changed}), encoding="utf-8")
    with pytest.raises(DispositionError, match="binding changed"):
        gate.load_binding(tmp_path, now=datetime.fromisoformat("2026-10-26T15:00:00+00:00"))


def test_grafana_transport_and_tag_cannot_mix_minio_or_unverified_upload(inputs, tmp_path, monkeypatch):
    binding, _ = inputs
    monkeypatch.setattr(fetch, "build_opener", lambda *args: pytest.fail("must fail before network"))
    policy = json.loads((gate.ROOT / gate.POLICY).read_bytes())
    transport = deepcopy(policy["transport"])
    # Parts have individual server digests; no whole-archive server digest exists.
    transport["parts"][0]["remote_digest"] = None
    with pytest.raises(DispositionError, match="server digest"):
        fetch.download_archive(tmp_path / "archive.tar", binding, transport, artifact="grafana")
    transport = deepcopy(policy["transport"])
    transport["asset_id"] = 678114320
    with pytest.raises(DispositionError, match="subject/repository/scope"):
        fetch.download_archive(tmp_path / "archive.tar", binding, transport, artifact="grafana")
    with pytest.raises(DispositionError, match="namespace"):
        fetch.load_image(tmp_path / "archive.tar", binding, fetch.APPROVED_TAG, artifact="grafana")
    assert fetch.artifact_spec("minio")["archive_cap"] == 512 * 1024**2
    assert fetch.artifact_spec("grafana")["archive_cap"] == 2 * 1024**3


def test_disposition_type_cannot_be_changed_from_human_authorization(inputs):
    binding, data = inputs
    auth = json.loads((gate.ROOT / gate.EVIDENCE / "authorization.json").read_text(encoding="utf-8-sig"))
    for original, replacement in [("FIXED", "NOT_AFFECTED"), ("NOT_AFFECTED", "FIXED")]:
        changed = deepcopy(binding)
        row = next(r for r in changed["allowed_findings"] if r["disposition"] == original)
        row["disposition"] = replacement
        with pytest.raises(DispositionError, match="exact18"):
            gate.validate_authorized_rows(auth, data[0], changed)
