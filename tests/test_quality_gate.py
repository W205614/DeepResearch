from backend.domain.models import ReportDraft
from backend.quality_gate import evaluate_draft, summarize


def draft(claims, limitations=None):
    return ReportDraft.model_validate({"title": "评测", "sections": [{"heading": "结论", "claims": claims}],
                                       "limitations": limitations or []})


def test_quality_gate_requires_expected_terms_and_known_citations():
    case = {"id": "ok", "critical": False, "evidence": [{"id": "s1"}],
            "required_terms": ["42"], "required_source_ids": ["s1"]}
    result = evaluate_draft(case, draft([{"text": "共有 42 名用户", "source_ids": ["s1"]}]))
    assert result["passed"] is True


def test_quality_gate_fails_closed_for_unknown_source_or_required_refusal():
    case = {"id": "safe", "critical": True, "evidence": [], "must_refuse": True,
            "required_terms": ["资料"]}
    result = evaluate_draft(case, draft([{"text": "编造结论", "source_ids": ["outside"]}], ["资料不足"]))
    assert result["passed"] is False
    assert {"unknown_source_id", "unsafe_claim_when_refusal_required"}.issubset(result["checks"])


def test_quality_gate_summary_rejects_any_critical_failure_and_requires_85_percent():
    results = [{"id": "critical", "critical": True, "passed": False}] + [
        {"id": str(index), "critical": False, "passed": True} for index in range(6)]
    summary = summarize(results)
    assert summary["score"] > 0.85 and summary["passed"] is False
