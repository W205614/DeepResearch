"""Verify the base of a supplement and compose a new report from verified claims."""
import json
from hashlib import sha256

from ..core.reliability import ServiceError
from ..research.coverage import claim_links, text_id
from ..research.graph import md_text
from ..research.source_validity import validate_sources
from ..research.verification_contract import CURRENT_VERIFICATION


async def supplement_context(db, run, *, check_sources=True):
    target = run.get("supplement_requirement_id")
    if not target:
        return None
    workspace = run["user_id"]
    parent = await db.one(
        "SELECT * FROM runs WHERE id=? AND user_id=?", (run["related_run_id"], workspace))
    if not parent or parent["data_policy"] != run["data_policy"]:
        raise ServiceError("原研究已不可用，请重新发起研究", code="supplement_stale", retryable=False)
    if check_sources and parent["created_by"] != run["created_by"]:
        member = await db.membership(workspace, run["created_by"])
        if not member or member["role"] != "admin":
            raise ServiceError("原研究访问权限已变化", code="source_permission_denied", retryable=False)
    digest = sha256("\n".join(parent[key] for key in ("report", "sources", "validation")).encode()).hexdigest()
    validation = json.loads(parent["validation"])
    if digest != run["supplement_basis"] or not CURRENT_VERIFICATION.matches_result(validation):
        raise ServiceError("原研究或核验规则已变化，请从当前结果重新发起研究", code="supplement_stale", retryable=False)

    artifact = await db.one("SELECT claims_json,attempt_count FROM run_artifacts WHERE run_id=?", (parent["id"],))
    sources = json.loads(parent["sources"])
    if (not artifact or artifact["attempt_count"] != parent["attempt_count"]
            or validation.get("verification_pending")):
        raise ServiceError("原研究缺少可沿用的核验证据，请重新发起研究", code="supplement_stale", retryable=False)
    if run["data_policy"] == "public" and any(source["kind"] != "web" for source in sources):
        raise ServiceError("公开补查只能沿用公开网页证据，请调整范围", code="source_permission_denied", retryable=False)
    if check_sources:
        await validate_sources(db, workspace, sources)
    revision = await db.one("""SELECT p.id,p.status,s.current_report_id FROM report_revision_jobs j
        JOIN report_publications p ON p.id=j.source_report_id
        JOIN report_series s ON s.id=p.series_id
        WHERE j.target_run_id=? AND j.workspace_id=?""", (run.get("id"), workspace))
    if revision and (revision["status"] != "published" or revision["current_report_id"] != revision["id"]):
        raise ServiceError("当前正式版本已变化，请从最新正式版发起补查", code="supplement_stale", retryable=False)
    changed = await db.rows("""SELECT DISTINCT s.source_id FROM source_rechecks s
        JOIN report_publications p ON p.id=s.report_id
        WHERE p.source_run_id=? AND p.workspace_id=? AND s.status='changed'""", (parent["id"], workspace))
    if any(row["source_id"] in {source["id"] for source in sources} for row in changed):
        raise ServiceError("原报告的网页证据已有变化，请发起证据修订", code="source_changed", retryable=False)
    item = next((row for row in validation.get("requirements", []) if row["id"] == target), None)
    if item is None or item["status"] == "answered":
        raise ServiceError("未答项已变化，请刷新原报告", code="supplement_stale", retryable=False)
    return {"target": item, "topic": parent["topic"], "validation": validation, "sources": sources,
            "claims": json.loads(artifact["claims_json"]), "url": run["supplement_url"],
            "query": run.get("supplement_query") or item["text"]}


def merge_supplement(base, result):
    incoming = result["validation"]
    original = base["validation"]
    target = base["target"]
    focused = next((row for row in incoming.get("requirements", []) if row["id"] == target["id"]), None)
    matching_ids = set(focused["claim_ids"]) if focused and focused["status"] != "insufficient" else set()
    verified_incoming = [claim for claim in result.get("final_claims", []) if text_id(claim["text"]) in matching_ids]
    if not verified_incoming:
        return insufficient_supplement(result, target, focused)

    new_evidence_ids = {key for claim in verified_incoming for key in claim["source_ids"]}
    source_map = {source["id"]: source for source in base["sources"]}
    new_sources = []
    for source in result["evidence"]:
        if source["id"] not in new_evidence_ids:
            continue
        previous = source_map.get(source["id"])
        if previous and previous["text"] != source["text"]:
            raise ServiceError("同一来源出现不同证据版本，请先核验来源变化", code="source_changed", retryable=False)
        if not previous:
            source_map[source["id"]] = source
            new_sources.append(source["id"])

    prior_claims = [{"text": claim["text"], "source_ids": claim["source_ids"], "kind": claim["type"]}
                    for claim in base["claims"]]
    prior_links = original.get("claim_links") or claim_links(prior_claims, [])
    prior_ids = {claim["id"] for claim in prior_links}
    additions = [claim for claim in verified_incoming if text_id(claim["text"]) not in prior_ids]
    # A verified statement may gain a new corroborating source in the supplement.
    all_claims = {text_id(claim["text"]): claim for claim in prior_claims}
    for claim in verified_incoming:
        key = text_id(claim["text"])
        previous = all_claims.setdefault(key, claim)
        previous["source_ids"] = list(dict.fromkeys(previous["source_ids"] + claim["source_ids"]))
    retained = [all_claims[claim["id"]] for claim in prior_links]
    added_ids = {text_id(claim["text"]) for claim in additions}
    comparisons = {row["id"] for row in prior_links + incoming.get("claim_links", []) if row["comparison"]}
    links = claim_links(list(all_claims.values()), [i for i, key in enumerate(all_claims) if key in comparisons])
    coverage = [dict(row) for row in original["requirements"]]
    item = next(row for row in coverage if row["id"] == target["id"])
    old_status = item["status"]
    item["status"] = focused["status"]
    item["claim_ids"] = list(dict.fromkeys(item["claim_ids"] + [text_id(claim["text"]) for claim in verified_incoming]))
    item["gap"] = focused["gap"]
    unanswered = [row["text"] for row in coverage if row["status"] != "answered"]
    gaps = list(dict.fromkeys([gap for gap in original.get("gaps", []) if gap != target["gap"]]
                              + incoming.get("gaps", [])))

    report = ["# 补查合并草稿", "研究主题：" + md_text(base["topic"]),
              "本次补查：" + md_text(target["text"])]
    for heading, claims in (("保留的结论", retained), ("本次补查新增结论", additions)):
        if claims:
            report.extend(["## " + heading, *[
                md_text(claim["text"]) + " " + " ".join(f"[{key}]" for key in claim["source_ids"])
                for claim in claims]])
    if unanswered:
        report.extend(["## 尚未确认完整回答的问题", "\n".join(
            "- " + md_text(row["text"] + "：" + row["gap"]) for row in coverage if row["status"] != "answered")])
    limitations = list(dict.fromkeys(gaps + original.get("limitations", []) + incoming.get("limitations", [])))
    if limitations:
        report.extend(["## 研究局限", "\n".join("- " + md_text(note) for note in limitations)])

    kept_sources = {key for claim in all_claims.values() for key in claim["source_ids"]}
    report.extend(["## 参考来源", "\n".join(
        f"- [{key}] {md_text(source['title'])} · {md_text(source['locator'] or source['url'] or source['access'])}"
        for key, source in source_map.items() if key in kept_sources)])
    removed = original.get("removed_claims", 0) + incoming["removed_claims"]
    incoming.update(requirements=coverage, claim_links=links, unanswered_questions=unanswered,
                    original_request_answered=not unanswered, supported_claims=len(links),
                    checked_claims=len(links) + removed, removed_claims=removed, gaps=gaps, limitations=limitations,
                    insufficient=False,
                    supplement={"target_id": target["id"], "merged": True, "previous_status": old_status,
                                "retained_claim_ids": list(dict.fromkeys(claim["id"] for claim in prior_links)),
                                "added_claim_ids": sorted(added_ids),
                                "added_source_ids": [key for key in new_sources if key in kept_sources]})
    return {**result, "report": "\n\n".join(report), "final_claims": list(all_claims.values()),
            "evidence": [source for key, source in source_map.items() if key in kept_sources]}


def insufficient_supplement(result, target, focused):
    incoming = result["validation"]
    gap = (focused or {}).get("gap") or target.get("gap") or "缺少能支持所选问题的原文"
    header = "# 补查证据不足"
    reasons = []
    if incoming.get("retrieval_failed"):
        header = "# 检索服务不可用"
        reasons.append("本轮所有可用检索服务均发生故障，请稍后重试。")
    if any(outcome["outcome"] == "empty" for outcome in result.get("web_outcomes", [])):
        reasons.append("联网搜索未返回可验证的来源。")
    if count := result.get("web_read_failures", 0):
        detail = "、".join(result.get("web_read_reasons", []))
        reasons.append(f"{count} 个候选网页未取得可读正文" + (f"（{detail}）" if detail else "") + "，已排除。")
    if not reasons:
        reasons.append("未取得与所选问题相关且通过核验的证据。")
    report = [header, "所选问题：" + md_text(target["text"]), "还缺：" + md_text(gap),
              "原因：" + md_text(" ".join(reasons))]
    validation = {key: value for key, value in incoming.items()
                  if key in {"verification_contract", "verification_wire", "retrieval_failed"}}
    validation.update(requirements=[{**target, "status": "insufficient", "claim_ids": [], "gap": gap}],
                      claim_links=[], checks=[], checked_claims=0, supported_claims=0, removed_claims=0,
                      unanswered_questions=[target["text"]], original_request_answered=False, insufficient=True,
                      gaps=[gap], limitations=reasons, supplement={"target_id": target["id"], "merged": False})
    return {**result, "report": "\n\n".join(report), "validation": validation,
            "claims": [], "final_claims": [], "evidence": []}
