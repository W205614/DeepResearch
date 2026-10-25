import copy
import json

import pytest

from scripts.reuse_quality_evidence import (
    ARTIFACT, DIGEST, FILES, REPO, RUN, SOURCE, check_diff, check_fingerprints,
    check_reports, check_source, choose_mode, unpack,
)


def metadata():
    run = {"repository": {"full_name": REPO}, "head_sha": SOURCE, "id": RUN,
           "workflow_id": 354007725, "path": ".github/workflows/answer-quality.yml",
           "status": "completed", "conclusion": "success"}
    artifact = {"id": ARTIFACT, "expired": False, "workflow_run": {"head_sha": SOURCE, "id": RUN},
                "name": "answer-quality-evidence-" + SOURCE, "digest": "sha256:" + DIGEST}
    return run, artifact


def test_only_exact_successful_source_metadata_is_accepted():
    run, artifact = metadata()
    check_source(run, artifact)
    for key, value in [("conclusion", "failure"), ("head_sha", "a" * 40), ("workflow_id", 1)]:
        changed = copy.deepcopy(run)
        changed[key] = value
        with pytest.raises(ValueError, match="successful workflow"):
            check_source(changed, artifact)
    artifact["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="digest differs"):
        check_source(run, artifact)


def test_bad_archive_digest_fails_before_any_extraction():
    with pytest.raises(ValueError, match="checksum differs"):
        unpack(b"not-the-approved-artifact")


def test_changed_inputs_and_scope_fail_closed():
    expected = {"backend/research/graph.py": "same", "uv.lock": "same", "eval/cases.json": "same"}
    check_fingerprints(expected, dict(expected))
    for path in expected:
        with pytest.raises(ValueError, match="input differs"):
            check_fingerprints(expected, expected | {path: "changed"})
    check_diff(["business-backend/Dockerfile"], ["business-backend/Dockerfile"])
    with pytest.raises(ValueError, match="outside"):
        check_diff(["business-backend/Dockerfile", "backend/research/graph.py"], ["business-backend/Dockerfile"])


def test_forged_pass_summary_is_rejected_without_trusting_it():
    raw = {name: b"{}" for name in FILES}
    raw["answer-quality.json"] = json.dumps({"results": [{"id": "critical", "critical": True, "passed": False}],
                                             "summary": {"passed": True}}).encode()
    with pytest.raises(ValueError, match="unchanged thresholds"):
        check_reports(raw, None)


def test_one_time_branch_does_not_skip_future_changed_commits():
    assert choose_mode({"approved": True}, SOURCE) == "reuse"
    assert choose_mode({"approved": True}, "next-application-commit") == "fresh"
    assert choose_mode(None, SOURCE) == "fresh"
