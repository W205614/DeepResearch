"""Receipt-only fixtures: no model, network, Git, Docker, or database operations."""
import hashlib
import json

import pytest

from scripts import write_delivery_receipt as receipts


def env(status="failure"):
    return {"GITHUB_SHA": "a" * 40, "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "example/project", "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2", "EVALUATION_JOB_STATUS": status}


def write(root, path, value):
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
    return destination


def sources(root):
    for path in receipts.SOURCE_FILES:
        write(root, path, ("current " + path + "\r\n").encode())


def historical_reuse(root, environment):
    sources(root)
    def normalized(path):
        return hashlib.sha256((root / path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    original = {"status": "LOCAL_DELIVERY_COMPLETE_CI_PENDING", "baseline_sha": "b" * 40,
                "graph_normalized_sha256": normalized("backend/research/graph.py"),
                "deployment": {"passed": True,
                               "python_services": [{"uv_lock_normalized_sha256": normalized("uv.lock")}],
                               "web": {"frontend_lock_normalized_sha256": normalized("frontend/package-lock.json")}},
                "java_os_security_followup": {"dockerfile_normalized_sha256": normalized("business-backend/Dockerfile")}}
    history = write(root, receipts.HISTORICAL, original)
    write(root, ".cache/eval/quality-reuse.json", {"quality_evidence": "reused",
          "source_sha": "c" * 40, "target_sha": environment["GITHUB_SHA"]})
    return history


def test_fresh_current_sha_and_hashes_do_not_read_historical_deployment(tmp_path, monkeypatch):
    sources(tmp_path)
    original = write(tmp_path, receipts.HISTORICAL, b"unreadable historical JSON is irrelevant to fresh")
    original_bytes = original.read_bytes()
    def forbidden(_):
        raise AssertionError("Fresh must not validate or rebind historical deployment")
    monkeypatch.setattr(receipts, "verify_historical_deployment", forbidden)
    context = write(tmp_path, ".cache/eval/ci-source-context.json", {"checkout_sha": env()["GITHUB_SHA"]})
    ledger = write(tmp_path, ".cache/eval/provider-budget.sqlite3", b"synthetic ledger bytes; not opened as SQL")
    value = receipts.write_receipt(tmp_path, env())
    assert value["ci_receipt"]["sha"] == env()["GITHUB_SHA"]
    assert value["ci_receipt"]["quality_evidence"] == "fresh"
    assert value["deployment_performed"] is False and value["deployment_verified_for_current_sha"] is False
    assert "historical_deployment_reference" not in value and "deployment" not in value
    assert value["reports"]["ci-source-context.json"]["sha256"] == hashlib.sha256(context.read_bytes()).hexdigest()
    assert value["ledger_files"]["provider-budget.sqlite3"]["sha256"] == hashlib.sha256(ledger.read_bytes()).hexdigest()
    graph = tmp_path / "backend/research/graph.py"
    assert value["current_source_fingerprints"]["backend/research/graph.py"]["sha256"] == hashlib.sha256(graph.read_bytes()).hexdigest()
    assert original.read_bytes() == original_bytes


@pytest.mark.parametrize("status", ["failure", "cancelled"])
def test_missing_reports_and_failed_job_status_are_not_fabricated_passes(tmp_path, status):
    sources(tmp_path)
    value = receipts.write_receipt(tmp_path, env(status))
    assert value["status"] == value["ci_receipt"]["job_status_before_receipt"] == status
    assert all(value["reports"][name]["state"] == "not-run" for name in receipts.QUALITY_REPORTS)
    assert value["ledger_files"]["provider-budget.sqlite3"]["state"] == "not-run"
    assert "passed" not in value
    failed = write(tmp_path, ".cache/eval/answer-quality.json", {"summary": {"passed": False, "case_count": 1}})
    value = receipts.write_receipt(tmp_path, env(status))
    assert value["reports"]["answer-quality.json"]["reported"]["summary"]["passed"] is False
    assert value["reports"]["answer-quality.json"]["sha256"] == hashlib.sha256(failed.read_bytes()).hexdigest()
    assert value["status"] == status


@pytest.mark.parametrize("path", receipts.SOURCE_FILES)
def test_reuse_retains_each_original_deployment_drift_rejection(tmp_path, path):
    historical_reuse(tmp_path, env())
    (tmp_path / path).write_bytes(b"changed")
    with pytest.raises(ValueError, match="reuse receipt validation failed"):
        receipts.write_receipt(tmp_path, env())
    recorded = json.loads((tmp_path / ".cache/eval/delivery.json").read_bytes())
    assert recorded["receipt_status"] == "invalid-reuse" and recorded["status"] == "failure"
    assert "historical_deployment_reference" not in recorded


def test_reuse_marker_cannot_bind_another_target_sha(tmp_path):
    historical_reuse(tmp_path, env())
    write(tmp_path, ".cache/eval/quality-reuse.json", {"quality_evidence": "reused", "target_sha": "d" * 40})
    with pytest.raises(ValueError, match="does not bind this CI SHA"):
        receipts.write_receipt(tmp_path, env())


def test_unchanged_reuse_only_links_original_deployment_without_rebinding_it(tmp_path):
    history = historical_reuse(tmp_path, env("success"))
    original = history.read_bytes()
    value = receipts.write_receipt(tmp_path, env("success"))
    reference = value["historical_deployment_reference"]
    assert value["ci_receipt"]["quality_evidence"] == "reused"
    assert reference["original_baseline_sha"] == "b" * 40
    assert reference["sha256"] == hashlib.sha256(original).hexdigest()
    assert history.read_bytes() == original
    assert "deployment" not in value and value["deployment_verified_for_current_sha"] is False
