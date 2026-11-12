"""Regressions for real-model claims that include document provenance in prose."""

import pytest

from backend.domain.models import ReportDraft, TargetVerification
from backend.research.evidence_policy import validate_claim
from backend.research.graph import ResearchGraph


SOURCE = {
    "id": "local-source", "kind": "local", "title": "lifecycle.txt",
    "locator": "正文 · 字符 1–41", "access": "document",
    "text": "功能验收资料：工作空间成员权限在后端校验。删除资料后，不应在知识库检索结果中出现。",
}
ORIGINAL = "根据所提供的本地资料《lifecycle.txt》（正文·字符1–41），工作空间成员权限在后端校验。原文：“工作空间成员权限在后端校验。”"
REPAIR = "《lifecycle.txt》正文明确说明，工作空间成员权限在后端校验。原文：“工作空间成员权限在后端校验。”"


@pytest.mark.parametrize("text", [ORIGINAL, REPAIR])
def test_actual_failed_claims_reach_semantic_validation(text):
    allowed, reason = validate_claim(text, [SOURCE])
    assert allowed, reason
    assert "语义校验" in reason


@pytest.mark.parametrize("text", [
    ORIGINAL.replace("1–41", "1–42"),
    ORIGINAL.replace("lifecycle.txt", "other.txt"),
    ORIGINAL + "校验耗时41毫秒。",
    ORIGINAL + "存在1项缺陷。",
    "工作空间成员权限在后端校验。",
    "《other.txt》正文明确说明，工作空间成员权限在后端校验。",
])
def test_unbound_metadata_and_unattributed_facts_stay_blocked(text):
    assert not validate_claim(text, [SOURCE])[0]


def test_document_title_year_is_metadata_only_inside_exact_source_label():
    source = {**SOURCE, "title": "验收2026.txt"}
    claim = "《验收2026.txt》正文说明，工作空间成员权限在后端校验。"
    assert validate_claim(claim, [source])[0]
    assert not validate_claim(claim + "该行为于2026年生效。", [source])[0]
    assert not validate_claim(claim.replace("2026", "2027"), [source])[0]


@pytest.mark.parametrize("reverse", [False, True])
def test_same_document_chunk_provenance_is_source_order_independent(reverse):
    sources = [{**SOURCE, "locator": "正文 · 字符 1–40"},
               {**SOURCE, "id": "later-chunk", "locator": "正文 · 字符 41–80"}]
    if reverse:
        sources.reverse()
    claim = "《lifecycle.txt》（正文·字符41–80）正文说明，删除资料后不应在知识库检索结果中出现。"
    assert validate_claim(claim, sources)[0]
    assert not validate_claim(claim.replace("41–80", "41–81"), sources)[0]


async def test_bound_provenance_keeps_full_claim_and_semantic_rejection(runtime):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    contradiction = REPAIR.replace("后端", "前端")
    texts = [ORIGINAL, REPAIR, contradiction, ORIGINAL.replace("1–41", "1–42")]
    draft = ReportDraft.model_validate({"title": "验收", "sections": [{"heading": "权限", "claims": [
        {"text": text, "source_ids": [SOURCE["id"]]} for text in texts
    ]}]})
    calls = []

    async def structured(role, instruction, data, schema, run_id, **kwargs):
        assert role == "validator" and schema is TargetVerification
        calls.append(data)
        assert [row["text"] for row in data["claims"]] == texts[:3]
        assert data["sources"][SOURCE["id"]] == SOURCE
        return TargetVerification.model_validate({"checks": [
            {"index": row["index"], "supported": row["index"] != 2,
             "reason": "原文写后端" if row["index"] == 2 else "资料内记录获支持",
             "unsupported_parts": ["前端"] if row["index"] == 2 else [],
             "atomic_checks": [{"target_id": target["id"], "supported": row["index"] != 2}
                               for target in row["targets"]]}
            for row in data["claims"]
        ]})

    runtime.providers.structured = structured
    approved, checks = await graph.check_draft({"run_id": "local-provenance"}, draft, {SOURCE["id"]: SOURCE})
    assert approved == {0, 1}
    assert len(calls) == 1
    assert {row["index"] for row in checks if not row["supported"]} == {2, 3}
    assert [claim.text for claim in draft.sections[0].claims] == texts
