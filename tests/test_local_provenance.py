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
FINAL_ORIGINAL = "根据所提供的本地功能验收资料，工作空间成员权限在后端校验。原文：“工作空间成员权限在后端校验。”引用位置：lifecycle.txt，正文 · 字符 1–41。"
FINAL_DELETION = "根据所提供的本地功能验收资料，删除资料后的检索预期是：被删除的资料不应在知识库检索结果中出现。原文：“删除资料后，不应在知识库检索结果中出现。”引用位置：lifecycle.txt，正文 · 字符 1–41。"


@pytest.mark.parametrize("text", [ORIGINAL, REPAIR, FINAL_ORIGINAL, FINAL_DELETION])
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
    FINAL_ORIGINAL.replace("1–41", "1–42"),
    FINAL_ORIGINAL.replace("1–41", "1–410"),
    FINAL_ORIGINAL.replace("1–41", "1–41.5"),
    FINAL_ORIGINAL.replace("1–41", "1–41万"),
    FINAL_ORIGINAL.replace("lifecycle.txt", "other.txt"),
    FINAL_ORIGINAL + "校验耗时41毫秒。",
    FINAL_ORIGINAL + "存在1项缺陷。",
    FINAL_ORIGINAL.replace("根据所提供的本地功能验收资料，", ""),
    FINAL_ORIGINAL.replace("根据所提供的本地功能验收资料，", "根据推测，其他资料缺失。"),
    FINAL_ORIGINAL.replace("根据所提供的本地功能验收资料，", "根据什么？本地功能验收资料，"),
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
    contradiction = FINAL_ORIGINAL.replace("后端", "前端")
    texts = [FINAL_ORIGINAL, FINAL_DELETION, contradiction, FINAL_ORIGINAL.replace("1–41", "1–42")]
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
