"""Evaluate the complete local retrieval-to-grounded-answer path without RAGAS."""
import argparse
import asyncio
import json
import math
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import Settings
from backend.quality_gate import QualityDraft, assess_support, evaluate_draft, summarize
from backend.services.runtime import Runtime


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else 0


async def main_async(args):
    corpus_path = Path(args.corpus)
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    output = Path(args.output)
    data_dir = output.parent / f"{output.stem}-data"
    if data_dir.exists():
        raise ValueError(f"评测数据目录已存在：{data_dir}；请更换 --output 或手动确认后清理")
    settings = Settings(data_dir=data_dir, database_url="", queue_backend="local", object_store_backend="filesystem",
                        document_scan_mode="disabled", vector_collection_prefix=f"dr_qa_{uuid.uuid4().hex[:12]}")
    settings.allow_internal_model_processing = True
    if not settings.llm_api_key.get_secret_value() or not settings.embedding_api_key.get_secret_value():
        raise ValueError("本地问答联合评测需要真实 LLM_API_KEY 与 EMBEDDING_API_KEY")
    if args.with_reranker:
        if not settings.reranker_url or not settings.reranker_model:
            raise ValueError("--with-reranker requires RERANKER_URL and RERANKER_MODEL")
        settings.reranker_enabled = True
    else:
        settings.reranker_enabled = False
    runtime = await Runtime(settings, isolated_mode=True).start()
    results, latencies = [], []
    llm_calls = 0
    reranker_calls = 0
    try:
        for row in corpus:
            path = (corpus_path.parent / row["path"]).resolve()
            document = await runtime.documents.add("evaluator", row["name"], path.read_bytes())
            await runtime.documents.ingest(document["id"])
        for case in cases:
            started = time.monotonic()
            hits = await runtime.documents.search("evaluator", [case["question"]], limit=5,
                                                    rerank_query=case["question"])
            evidence = [{"id": "L-" + row["id"][:12], "title": row["title"], "text": row["text"]}
                        for row in hits]
            draft = await runtime.providers.structured(
                "local_qa_eval",
                "只根据 evidence 回答 question。只输出 claims 和 limitations；每条事实必须引用 evidence 中的 id。"
                "资料不能回答时不要生成事实结论，并在 limitations 明确说明资料不足。",
                {"question": case["question"], "evidence": evidence}, QualityDraft,
                "local-qa-" + case["id"], temperature=0)
            quality_case = {**case, "evidence": evidence}
            checks = await assess_support(runtime.providers, quality_case, draft, "local-qa-support-" + case["id"])
            verdict = evaluate_draft(quality_case, draft, checks)
            retrieved = {row["title"] for row in hits}
            expected = set(case.get("relevant_documents", []))
            retrieval_pass = expected.issubset(retrieved) if expected else True
            verdict["retrieval_pass"] = retrieval_pass
            verdict["passed"] = verdict["passed"] and retrieval_pass
            verdict["retrieved_documents"] = sorted(retrieved)
            verdict["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
            verdict["cited_claim_count"] = sum(bool(claim.source_ids) for claim in draft.claims)
            verdict["citation_coverage"] = (round(verdict["cited_claim_count"] / len(draft.claims), 4)
                                              if draft.claims else 1.0)
            call_rows = await runtime.db.rows("SELECT llm_calls FROM counters WHERE run_id IN (?,?)",
                                               ("local-qa-" + case["id"], "local-qa-support-" + case["id"]))
            llm_calls += sum(int(row["llm_calls"]) for row in call_rows)
            reranker_calls += int(bool(hits) and hits[0].get("ranking_stage") in {"rerank", "fusion_fallback"})
            latencies.append(verdict["latency_ms"])
            results.append(verdict)
    finally:
        try:
            await runtime.vectors.drop_collections()
        finally:
            await runtime.close()
    summary = summarize(results)
    total_claims = sum(row["claim_count"] for row in results)
    summary["citation_coverage"] = (round(sum(row["cited_claim_count"] for row in results) / total_claims, 4)
                                      if total_claims else 1.0)
    report = {
        "suite": "local-qa-v1",
        "mode": "hybrid_plus_reranker" if args.with_reranker else "hybrid_bm25_vector",
        "summary": summary,
        "latency": {"p50_ms": round(statistics.median(latencies), 2),
                    "p95_ms": round(percentile(latencies, 0.95), 2)},
        "call_counts": {"llm": llm_calls, "embedding": len(corpus) + len(cases),
                        "reranker": reranker_calls},
        "results": results,
    }
    gate_passed = summary["passed"]
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        added = round(report["latency"]["p95_ms"] - baseline["latency"]["p95_ms"], 2)
        report["comparison"] = {"baseline": str(args.baseline),
                                "baseline_score": baseline["summary"]["score"],
                                "p95_added_ms": added, "p95_added_within_2s": added <= 2000}
        gate_passed = gate_passed and added <= 2000
    report["gate_passed"] = gate_passed
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": summary, "latency": report["latency"]}, ensure_ascii=False))
    if args.require_gate and not gate_passed:
        raise RuntimeError("local QA acceptance gate failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default="eval/local_retrieval_corpus.json")
    parser.add_argument("--cases", default="eval/local_qa_cases.json")
    parser.add_argument("--output", default=".cache/eval/local-qa.json")
    parser.add_argument("--with-reranker", action="store_true")
    parser.add_argument("--baseline", help="Baseline local-QA JSON used to enforce the end-to-end P95 delta")
    parser.add_argument("--require-gate", action="store_true")
    raise SystemExit(asyncio.run(main_async(parser.parse_args())))
