import json
from hashlib import sha256

import pytest

from backend.core.reliability import ServiceError
from backend.domain.models import QuestionCoverage
from backend.research.coverage import claim_links, coverage_items, text_id
from backend.research.verification_contract import VERIFICATION_CONTRACT, VERIFICATION_WIRE
from backend.services.research_supplements import merge_supplement, supplement_context


def test_original_requirements_include_the_item_omitted_by_the_plan():
    claims = [{"text": "甲提供本地部署。", "source_ids": ["W-a"]}]
    topic = "比较甲的部署条件和乙的容量。"
    model = QuestionCoverage.model_validate({"requirements": [
        {"requirement": "甲的部署", "status": "partial", "claim_indices": [0], "gap": "缺少运行条件"},
        {"requirement": "乙的容量", "status": "answered", "claim_indices": [3]},
    ]})
    rows = coverage_items(topic, ["甲的部署"], claims, model)
    assert [(row["text"], row["status"]) for row in rows] == [
        ("甲的部署", "partial"), ("乙的容量", "insufficient")]
    assert rows[0]["claim_ids"] == [text_id(claims[0]["text"])]
    assert rows[1]["claim_ids"] == []


def test_targeted_merge_updates_only_the_selected_requirement():
    old_claim = {"text": "甲支持全文索引。", "source_ids": ["W-1"], "kind": "fact"}
    new_claim = {"text": "乙支持过滤查询。", "source_ids": ["W-2"], "kind": "fact"}
    def source(key, content):
        return {"id": key, "kind": "web", "text": content, "title": key,
                "url": "https://example.com/" + key, "locator": "", "access": "fulltext"}
    old_source, new_source = source("W-1", old_claim["text"]), source("W-2", new_claim["text"])
    original = "比较甲的全文索引、乙的过滤方式和冷启动。"
    rows = coverage_items(original, [], [old_claim], QuestionCoverage.model_validate({"requirements": [
        {"requirement": "全文索引", "status": "answered", "claim_indices": [0]},
        {"requirement": "过滤", "status": "insufficient", "gap": "缺少过滤证据"},
        {"requirement": "冷启动", "status": "insufficient", "gap": "没有实测"},
    ]}))
    previous = {"requirements": rows, "claim_links": claim_links([old_claim], []), "gaps": ["没有实测"]}
    basis = {"target": rows[1], "topic": original, "claims": [{"type": "fact", **old_claim}],
             "validation": previous, "sources": [old_source]}
    current = coverage_items("过滤", [], [new_claim], QuestionCoverage.model_validate(
        {"original_request_answered": True,
         "requirements": [{"requirement": "过滤", "status": "answered", "claim_indices": [0]}]}))
    result = merge_supplement(basis, {"final_claims": [new_claim], "evidence": [new_source],
        "validation": {"requirements": current, "original_request_answered": True, "removed_claims": 0}})
    assert result["final_claims"] == [old_claim, new_claim]
    assert result["evidence"] == [old_source, new_source]
    assert result["validation"]["unanswered_questions"] == ["冷启动"]
    assert rows[1]["status"] == "insufficient"
    assert result["validation"]["supplement"]["added_source_ids"] == ["W-2"]
    assert "甲支持全文索引。" in result["report"] and "乙支持过滤查询。" in result["report"]
    assert "没有实测" in result["report"]


def test_supplement_with_only_unrelated_evidence_returns_the_selected_gap():
    target = {"id": text_id("内存峰值实测是多少？"), "text": "内存峰值实测是多少？",
              "status": "partial", "claim_ids": ["original-claim"], "gap": "缺少实测记录"}
    unrelated = {"text": "笔记应用可以按标签归档。", "source_ids": ["W-notes"], "kind": "fact"}
    other = {"id": text_id("能否归档？"), "text": "能否归档？", "status": "answered",
             "gap": "", "claim_ids": [text_id(unrelated["text"])]}
    result = merge_supplement({"target": target, "validation": {"requirements": [target, other]}}, {
        "report": unrelated["text"], "validation": {"requirements": [other], "checks": [{"index": 0}]},
        "claims": [unrelated], "final_claims": [unrelated], "evidence": [{"id": "W-notes"}],
        "web_read_failures": 1,
        "web_read_reasons": ["来源正文不可访问（HTTP 404）"],
    })
    assert result["claims"] == result["final_claims"] == result["evidence"] == []
    assert result["validation"]["checks"] == result["validation"]["claim_links"] == []
    assert result["validation"]["requirements"] == [{**target, "status": "insufficient", "claim_ids": []}]
    assert result["validation"]["supplement"] == {"target_id": target["id"], "merged": False}
    assert target["gap"] in result["report"]
    assert "1 个候选网页未取得可读正文（来源正文不可访问（HTTP 404）），已排除。" in result["report"]
    assert unrelated["text"] not in result["report"]
    assert target["status"] == "partial"


async def test_inherited_document_content_must_still_match_the_base(runtime):
    document = await runtime.documents.add("alice", "example.md", b"a known condition")
    await runtime.documents.ingest(document["id"])
    current = await runtime.db.one("SELECT * FROM documents WHERE id=?", (document["id"],))
    chunk = await runtime.db.one("SELECT * FROM chunks WHERE document_id=?", (document["id"],))
    sources = json.dumps([{"id": "L-a", "kind": "local", "document_id": document["id"],
                           "chunk_id": chunk["id"], "document_hash": current["hash"],
                           "index_version": current["index_version"], "text": chunk["text"]}])
    validation = json.dumps({"verification_contract": VERIFICATION_CONTRACT, "verification_wire": VERIFICATION_WIRE,
                             "requirements": [{"id": "req-1", "status": "insufficient", "text": "运行条件是什么？"}]})
    await runtime.db.execute("""INSERT INTO runs(id,user_id,thread_id,topic,mode,status,created_at,updated_at,report,
        sources,validation,data_policy,attempt_count,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("base", "alice", "thread", "topic", "deep", "completed", "", "", "report", sources, validation, "internal", 1, ""))
    await runtime.db.execute("INSERT INTO run_artifacts VALUES(?,?,?,?,?,?,?)", ("base", "alice", 1, 0, "{}", "[]", ""))
    digest = sha256("\n".join(("report", sources, validation)).encode()).hexdigest()
    child = {"user_id": "alice", "created_by": "", "data_policy": "internal", "related_run_id": "base",
             "supplement_requirement_id": "req-1", "supplement_basis": digest, "supplement_url": None}
    context = await supplement_context(runtime.db, child)
    assert context["target"]["id"] == "req-1"
    assert context["query"] == "运行条件是什么？"
    await runtime.db.execute("UPDATE documents SET hash='new version' WHERE id=?", (document["id"],))
    with pytest.raises(ServiceError, match="引用资料已删除、变化或缺少版本记录"):
        await supplement_context(runtime.db, child)
