from types import SimpleNamespace

import pytest

from backend.domain.models import ReportDraft, Verification
from backend.research.evidence_policy import validate_claim
from backend.research.graph import ResearchGraph


@pytest.mark.parametrize("claim,evidence,accepted", [
    ("资料结论仅有一条：只验证了流程。", "只验证了流程，尚未验证市场效果。", False),
    ("审计发现只有两项。", "发现凭证缺失，同时还需检查访问控制。", False),
    ("实验结果总共三项。", "观测到温度变化，压力尚未验证。", False),
    ("仅验证了流程，尚未验证市场效果。", "仅验证了流程，尚未验证市场效果。", True),
    ("发现只有两项。", "发现只有两项。", True),
    ("某一条结论仍需核查。", "某一条结论仍需核查。", True),
    ("术语“结论”与原文“验证”的对应关系尚未确认。", "仅验证了操作流程，尚未验证市场效果。", False),
    ("术语“结论”与原文“验证”的对应关系尚未确认。", "术语“结论”与原文“验证”的对应关系尚未确认。", True),
])
def test_unstated_exhaustive_counts(claim, evidence, accepted):
    assert validate_claim(claim, [{"text": evidence}])[0] is accepted


def test_explicit_count_unit_blocks_missing_unit_claim():
    sources = [
        {"text": "记录甲显示已完成人数为33名。"},
        {"text": "记录乙显示已完成人数为36名。"},
    ]
    claim = "根据所提供资料，两份记录文本中均未明确计数单位，无法判定两数单位是否等价。"

    accepted, reason = validate_claim(claim, sources)

    assert not accepted
    assert "计数单位" in reason


def test_missing_other_metadata_is_not_blocked_by_count_unit_gate():
    sources = [{"text": "记录甲显示已完成人数为33名。"}]

    accepted, _ = validate_claim("记录中未给出统计截止时间。", sources)

    assert accepted


@pytest.mark.asyncio
async def test_permissive_model_cannot_approve_exhaustive_scope():
    graph = ResearchGraph.__new__(ResearchGraph)
    graph.settings = SimpleNamespace(llm_context_tokens=128000)
    seen = []

    async def ask(role, instruction, data, schema, run_id):
        seen.extend(c["index"] for c in data["claims"])
        return Verification(checks=[{"index": c["index"], "supported": True, "reason": "mock"}
                                    for c in data["claims"]])

    graph.ask = ask
    draft = ReportDraft(title="范围回归", sections=[{"heading": "发现", "claims": [
        {"text": "结论仅有一条：只验证了流程。", "source_ids": ["s"]},
        {"text": "只验证了流程，尚未验证市场效果。", "source_ids": ["s"]},
    ]}])
    approved, checks = await graph.check_draft({"run_id": "test"}, draft,
        {"s": {"id": "s", "text": "只验证了流程，尚未验证市场效果。", "access": "fulltext"}})
    assert approved == {1}
    assert seen == [1]
    assert any(c["index"] == 0 and not c["supported"] for c in checks)


@pytest.mark.asyncio
async def test_permissive_model_cannot_approve_missing_unit_claim():
    graph = ResearchGraph.__new__(ResearchGraph)
    graph.settings = SimpleNamespace(llm_context_tokens=128000)
    seen = []

    async def ask(role, instruction, data, schema, run_id):
        seen.extend(c["index"] for c in data["claims"])
        return Verification(checks=[{"index": c["index"], "supported": True, "reason": "mock"}
                                    for c in data["claims"]])

    graph.ask = ask
    draft = ReportDraft(title="计数单位回归", sections=[{"heading": "发现", "claims": [
        {"text": "两份记录均未明确计数单位。", "source_ids": ["a", "b"]},
        {"text": "记录甲为33名，记录乙为36名。", "source_ids": ["a", "b"]},
    ]}])
    sources = {
        "a": {"id": "a", "text": "记录甲为33名。", "access": "fulltext", "trust_label": "fixture"},
        "b": {"id": "b", "text": "记录乙为36名。", "access": "fulltext", "trust_label": "fixture"},
    }

    approved, checks = await graph.check_draft({"run_id": "test"}, draft, sources)

    assert approved == {1}
    assert seen == [1]
    assert any(c["index"] == 0 and not c["supported"] for c in checks)
