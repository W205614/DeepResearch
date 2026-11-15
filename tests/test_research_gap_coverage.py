"""Offline API-wire and graph regressions, not evidence of live model judgment."""
import json

import httpx
import pytest

from backend.domain.models import Evidence
from backend.research.graph import ResearchGraph
from tests.helpers import make_provider


async def test_source_explicit_pending_status_reaches_analyst_api_contract(runtime, tmp_path):
    fact = {"kind": "fact", "text": "根据所提供资料，冷启动验证尚未完成。", "source_ids": ["L-checklist"]}
    calls = []

    def response(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"claims": [fact], "gaps": ["实际冷启动测量结果未知"]})}}]})

    provider = await make_provider(tmp_path, response)
    try:
        graph = ResearchGraph(runtime.settings, runtime.db, provider, runtime.vectors, runtime.documents)
        source = Evidence(id="L-checklist", kind="local", title="待验清单", access="document",
                          text="待验清单明确标注：冷启动验证尚未完成。").model_dump()
        result = await graph.analyst({"run_id": "pending-analyst", "topic": "说明上线前还缺什么验证证据",
                                     "plan": {"questions": ["哪些验证尚未完成？"]},
                                     "evidence": [source], "conflicts": []})
        assert result["claims"] == [fact]
        assert result["gaps"] == ["实际冷启动测量结果未知"]
        assert len(calls) == 1
        system = calls[0]["messages"][0]["content"]
        assert "来源明确标注的尚未完成、待验或未测量状态" in system
        assert "保留来源范围与 source_ids 写入 claims，接受通常核验" in system
        wire = json.loads(calls[0]["messages"][-1]["content"])
        assert wire["evidence"][0]["text"] == source["text"]
    finally:
        await provider.close()


async def test_excerpt_absence_stays_a_gap_in_analyst_wire(runtime, tmp_path):
    calls = []
    fact = {"kind": "fact", "text": "公开搜索默认关闭。", "source_ids": ["W-record"]}
    gap = "提供的选段未显示冷启动结果，不能据此判断全文是否提供。"

    def response(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"claims": [fact], "gaps": [gap]})}}]})

    provider = await make_provider(tmp_path, response)
    try:
        graph = ResearchGraph(runtime.settings, runtime.db, provider, runtime.vectors, runtime.documents)
        source = Evidence(id="W-record", kind="web", title="记录", access="fulltext",
                          text="公开搜索默认关闭。\n" + "另有归档说明。\n" * 1000).model_dump()
        result = await graph.analyst({"run_id": "excerpt-analyst", "topic": "核对资料是否提供冷启动结果",
                                     "plan": {"questions": ["冷启动结果？"]},
                                     "evidence": [source], "conflicts": []})
        assert result["claims"] == [fact] and result["gaps"] == [gap]
        assert len(calls) == 1
        assert "未经证明的全文缺失不能据此写成事实" in calls[0]["messages"][0]["content"]
        wire = json.loads(calls[0]["messages"][-1]["content"])
        assert wire["evidence"][0]["excerpt_truncated"] is True
        assert len(wire["evidence"][0]["text"]) < len(source["text"])
    finally:
        await provider.close()


@pytest.mark.parametrize("case", ["requested-gap", "requested-value", "removed-claim"])
async def test_coverage_uses_verified_facts_and_preserves_provider_decision(runtime, tmp_path, case):
    # Only external model HTTP is replaced; source ownership, ordinary claim
    # checks/repair, saved verification and final coverage run in the real graph.
    text = "资料记录：公开搜索默认关闭。待验清单：冷启动验证尚未完成。"
    document = await runtime.documents.add("alice", "checklist.md", text.encode())
    await runtime.documents.ingest(document["id"])
    current = await runtime.db.one("SELECT * FROM documents WHERE id=?", (document["id"],))
    chunk = await runtime.db.one("SELECT * FROM chunks WHERE document_id=?", (document["id"],))
    source = Evidence(id="L-checklist", kind="local", title="待验清单", access="document", text=chunk["text"],
                      document_id=current["id"], document_hash=current["hash"],
                      index_version=current["index_version"], chunk_id=chunk["id"]).model_dump()
    pending = {"kind": "fact", "text": "根据所提供资料，冷启动验证尚未完成。", "source_ids": [source["id"]]}
    known = {"kind": "fact", "text": "根据所提供资料，公开搜索默认关闭。", "source_ids": [source["id"]]}
    rejected = {"kind": "fact", "text": "根据所提供资料，冷启动验证已经完成。", "source_ids": [source["id"]]}
    claims = [known, rejected] if case == "removed-claim" else [pending]
    topic = "说明上线前还缺什么验证证据" if case == "requested-gap" else "冷启动实测峰值内存具体是多少？"
    extra = "冷启动实测峰值内存具体是多少？"
    questions = [topic, extra] if case == "requested-gap" else [topic]
    gaps = ["实际冷启动测量结果未知"]
    expected = {"original_request_answered": case == "requested-gap",
                "answered_questions": [0] if case == "requested-gap" else []}
    calls = []

    def response(request):
        wire = json.loads(request.content)
        data = json.loads(wire["messages"][-1]["content"])
        calls.append((wire, data))
        if "targets" in data.get("claims", [{}])[0]:
            answer = {"checks": [{"index": i, "supported": i == 0, "reason": "原文状态核验",
                                  "atomic_checks": [{"target_id": target["id"], "supported": i == 0,
                                                     "reason": "来源仅记载待验，未记载完成"} for target in claim["targets"]],
                                  "unsupported_parts": [] if i == 0 else ["没有完成证据"],
                                  "omitted_conditions": []} for i, claim in enumerate(data["claims"])]}
        elif "failed_claims" in data:
            answer = {"repairs": [{"index": 1, "replacement": None}]}
        else:
            answer = expected
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(answer)}}]})

    provider = await make_provider(tmp_path, response)
    try:
        graph = ResearchGraph(runtime.settings.model_copy(update={"demo_mode": False}), runtime.db,
                              provider, runtime.vectors, runtime.documents)
        result = await graph.validator({"run_id": "coverage-" + case, "user_id": "alice", "topic": topic,
            "evidence": [source], "plan": {"questions": questions}, "gaps": gaps, "conflicts": [],
            "draft": {"title": "报告", "sections": [{"heading": "已核验状态", "claims": claims}]}})
        wire, data = calls[-1]
        assert "已核验事实若准确说明来源明确标注的未完成状态" in wire["messages"][0]["content"]
        assert "不能代替已核验事实或自动构成回答" in wire["messages"][0]["content"]
        assert "只披露未知、列待验计划或资料不足不算完整回答" in wire["messages"][0]["content"]
        assert data["topic"] == topic and data["questions"] == questions and data["gaps"] == gaps
        assert data["claims"] == ([known] if case == "removed-claim" else [pending])
        assert result["final_claims"] == data["claims"]
        assert result["validation"]["original_request_answered"] == expected["original_request_answered"]
        assert result["validation"]["unanswered_questions"] == ([extra] if case == "requested-gap" else [topic])
        assert result["validation"]["removed_claims"] == (1 if case == "removed-claim" else 0)
        assert await runtime.db.rows("SELECT result FROM verified_claims WHERE run_id=?", ("coverage-" + case,))
    finally:
        await provider.close()
