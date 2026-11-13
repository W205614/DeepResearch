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
ACTUAL_CITATION = "根据所提供的本地功能验收资料，工作空间成员权限在后端校验。引用：lifecycle.txt，正文·字符1–41，原文“工作空间成员权限在后端校验”。"
ACTUAL_CITATION_DELETION = "根据所提供的本地功能验收资料，删除资料后的预期是：被删除的资料不应在知识库检索结果中出现。引用：lifecycle.txt，正文·字符1–41，原文“删除资料后，不应在知识库检索结果中出现”。"
REVIEW_SOURCE = {
    "id": "review-source", "kind": "local", "title": "review.md", "access": "document",
    "locator": "项目：补充试验 v0.4 · 字符 1–100",
    "text": "合成演示资料。文档版本：需求 v0.4。记录日期：2026-11-12。甲命中5条，乙命中8条。尚未验收上线。",
}


@pytest.mark.parametrize("text", [ORIGINAL, REPAIR, FINAL_ORIGINAL, FINAL_DELETION,
                                  ACTUAL_CITATION, ACTUAL_CITATION_DELETION])
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
    ACTUAL_CITATION.replace("1–41", "1–42"),
    ACTUAL_CITATION.replace("1–41", "1–410"),
    ACTUAL_CITATION.replace("1–41", "1–41.5"),
    ACTUAL_CITATION.replace("1–41", "1–41万"),
    ACTUAL_CITATION.replace("lifecycle.txt", "other.txt"),
    ACTUAL_CITATION + "校验耗时41毫秒。",
    ACTUAL_CITATION + "存在1项缺陷。",
    ACTUAL_CITATION.replace("根据所提供的本地功能验收资料，", ""),
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


@pytest.mark.parametrize("prefix", [
    "资料均自注为合成演示资料，",
    "合成演示资料v0.4补充试验记录：",
    "合成演示资料《补充试验 v0.4》记载，",
    "合成演示资料《补充试验 v0.4》（记录日期2026-11-12）记载，",
    "在合成演示资料v0.4的同次测试中，",
    "仅就合成演示资料v0.4的同次样本而言，",
    "在合成演示资料的记录中，",
    "在合成演示资料所设定的需求与试验情境内，",
    "需求 v0.4 规定，",
    "需求 v0.4 将首版设定为以下情境：",
    "根据所提供的需求 v0.4 记载，",
    "仅依据所提供的合成演示资料中的记录，",
])
def test_explicit_document_record_and_scope_grammar_reaches_semantic_validation(prefix):
    allowed, reason = validate_claim(prefix + "甲命中5条，乙命中8条。", [REVIEW_SOURCE])
    assert allowed, reason
    assert "语义校验" in reason


@pytest.mark.parametrize("text", [
    "甲命中5条，乙命中8条。",
    "资料未记录甲命中5条。",
    "合成演示资料没有记载甲命中5条。",
    "并非合成演示资料记录甲命中5条。",
    "不是在合成演示资料的记录中，甲命中5条。",
    "并非根据所提供资料，甲命中5条。",
    "需求v0.4没有规定甲命中5条。",
    "合成演示资料《未提供的资料》记载，甲命中5条。",
    "根据所提供资料《other.md》，甲命中5条。",
    "根据所提供资料《其他资料.md》，甲命中5条。",
    "合成演示资料《补充试验 v0.4》（记录日期2026-11-14）记载，甲命中5条。",
    "在合成演示资料的记录中，甲命中3条。",
    "合成演示资料记录，乙比甲多命中3条。",
])
def test_attribution_grammar_does_not_approve_missing_negated_invented_or_numeric_facts(text):
    assert not validate_claim(text, [REVIEW_SOURCE])[0]


def test_versioned_descriptor_must_exist_in_the_actual_cited_source():
    source = {**REVIEW_SOURCE, "text": "试验版本 v0.4。甲命中5条，乙命中8条。"}
    assert not validate_claim("需求v0.4规定，甲命中5条。", [source])[0]
    assert validate_claim("补充试验v0.4记录，甲命中5条。", [source])[0]


async def test_new_record_grammar_preserves_sources_and_full_semantic_targets(runtime):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    texts = ["合成演示资料《补充试验v0.4》记录，甲命中5条。",
             "在合成演示资料的记录中，甲命中5条且已经验收上线。"]
    draft = ReportDraft.model_validate({"title": "检索评审", "sections": [{"heading": "记录", "claims": [
        {"text": text, "source_ids": [REVIEW_SOURCE["id"]]} for text in texts
    ]}]})
    calls = []

    async def structured(role, instruction, data, schema, run_id, **kwargs):
        assert role == "validator" and schema is TargetVerification
        calls.append(data)
        assert [claim["text"] for claim in data["claims"]] == texts
        assert data["sources"] == {REVIEW_SOURCE["id"]: REVIEW_SOURCE}
        return TargetVerification.model_validate({"checks": [
            {"index": row["index"], "supported": row["index"] == 0,
             "reason": "原文尚未验收上线" if row["index"] else "资料内命中记录获支持",
             "unsupported_parts": ["已经验收上线"] if row["index"] else [],
             "atomic_checks": [{"target_id": target["id"], "supported": row["index"] == 0}
                               for target in row["targets"]]}
            for row in data["claims"]
        ]})

    runtime.providers.structured = structured
    approved, checks = await graph.check_draft({"run_id": "record-grammar"}, draft, {REVIEW_SOURCE["id"]: REVIEW_SOURCE})
    assert approved == {0}
    assert len(calls) == 1
    assert {row["index"] for row in checks if not row["supported"]} == {1}
    assert [claim.text for claim in draft.sections[0].claims] == texts


@pytest.mark.parametrize("citation_label", ["引用位置", "引用"])
async def test_bound_provenance_keeps_full_claim_and_semantic_rejection(runtime, citation_label):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    original = FINAL_ORIGINAL.replace("引用位置", citation_label)
    deletion = FINAL_DELETION.replace("引用位置", citation_label)
    contradiction = original.replace("后端", "前端")
    texts = [original, deletion, contradiction, original.replace("1–41", "1–42")]
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
