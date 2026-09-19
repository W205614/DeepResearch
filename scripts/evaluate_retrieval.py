"""Evaluate a labeled local-RAG corpus and a vector-only ablation.

The script measures document retrieval, not final-answer correctness. It uses a
single frozen corpus for both methods: vector-only is the ablation baseline;
hybrid is the product BM25 + vector fusion with its query-result cache.
"""
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
from backend.services.runtime import Runtime


def document_titles(hits: list[dict]) -> list[str]:
    seen, titles = set(), []
    for hit in hits:
        title = str(hit["title"])
        if title not in seen:
            seen.add(title)
            titles.append(title)
    return titles


def metrics(hits: list[dict], expected: set[str], cutoffs: list[int]) -> dict:
    titles = document_titles(hits)
    result = {}
    for cutoff in cutoffs:
        observed = set(titles[:cutoff])
        result[f"recall_at_{cutoff}"] = round(len(observed & expected) / len(expected), 4) if expected else None
        # P@K divides by K, including when fewer than K documents are returned.
        result[f"precision_at_{cutoff}"] = round(len(observed & expected) / cutoff, 4)
        gains = [1 if title in expected else 0 for title in titles[:cutoff]]
        dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
        ideal = sum(1 / math.log2(index + 2) for index in range(min(len(expected), cutoff)))
        result[f"ndcg_at_{cutoff}"] = round(dcg / ideal, 4) if ideal else None
    first = next((index + 1 for index, title in enumerate(titles) if title in expected), None)
    result["mrr_at_max_k"] = round(1 / first, 4) if first else 0.0
    return result


async def vector_only(runtime: Runtime, user: str, query: str, limit: int) -> tuple[list[dict], float]:
    """Same embedding and vector index, without BM25, fusion, or query cache."""
    started = time.monotonic()
    vector = (await runtime.providers.embed([query]))[0]
    candidates = await runtime.vectors.search("documents", user, vector, limit=limit * 4)
    seen, selected = set(), []
    for item in candidates:
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected, round((time.monotonic() - started) * 1000, 2)


def aggregate(rows: list[dict], prefix: str, cutoffs: list[int]) -> dict:
    values = {key: [] for k in cutoffs for key in (f"recall_at_{k}", f"precision_at_{k}", f"ndcg_at_{k}")}
    for row in rows:
        for key in values:
            values[key].append(row[f"{prefix}_{key}"])
    cold = [row[f"{prefix}_cold_ms"] for row in rows]
    return {
        **{key: round(statistics.mean(items), 4) for key, items in values.items()},
        "mrr_at_max_k": round(statistics.mean(row[f"{prefix}_mrr_at_max_k"] for row in rows), 4),
        "mean_cold_ms": round(statistics.mean(cold), 2),
        "p50_cold_ms": round(statistics.median(cold), 2),
        "p95_cold_ms": round(sorted(cold)[max(0, math.ceil(len(cold) * 0.95) - 1)], 2),
    }


def delta(baseline: float, optimized: float) -> dict:
    return {"absolute": round(optimized - baseline, 4),
            "relative_percent": round((optimized - baseline) / baseline * 100, 2) if baseline else None}


async def main_async(args):
    if args.with_reranker and 5 not in args.cutoffs:
        raise ValueError("Reranker gate requires cutoff 5")
    corpus_path, cases_path = Path(args.corpus), Path(args.cases)
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not corpus or not cases:
        raise ValueError("语料和评测用例不能为空")
    output = Path(args.output)
    data_dir = output.parent / "retrieval-eval-data"
    if data_dir.exists():
        raise ValueError(f"评测数据目录已存在：{data_dir}；请更换 --output 或手动确认后清理")
    settings = (Settings(_env_file=None, demo_mode=True, data_dir=data_dir, database_url='',
                         queue_backend='local', object_store_backend='filesystem', document_scan_mode='disabled')
                if args.demo else Settings(demo_mode=False, data_dir=data_dir, database_url='', queue_backend='local',
                                               object_store_backend='filesystem', document_scan_mode='disabled',
                                               vector_collection_prefix=f"dr_eval_{uuid.uuid4().hex[:12]}"))
    if not args.demo and settings.missing():
        raise ValueError("真实评测缺少配置：" + "、".join(settings.missing()))
    # Live embeddings and Milvus are intentional here, but evaluation metadata
    # stays in a disposable SQLite database rather than the product database.
    runtime = await Runtime(settings, isolated_mode=True).start()
    try:
        configured_reranker = bool(runtime.settings.reranker_url and runtime.settings.reranker_model)
        if args.with_reranker and not configured_reranker:
            raise ValueError("--with-reranker requires RERANKER_URL and a reachable compatible service")
        for row in corpus:
            path = (corpus_path.parent / row["path"]).resolve()
            document = await runtime.documents.add("evaluator", row["name"], path.read_bytes())
            await runtime.documents.ingest(document["id"])
        chunk_count = int((await runtime.db.one("SELECT COUNT(*) AS count FROM chunks WHERE user_id='evaluator'"))["count"])
        rows = []
        for case in cases:
            expected, query = set(case["relevant_documents"]), case["query"]
            vector_hits, vector_ms = await vector_only(runtime, "evaluator", query, args.max_k)
            runtime.settings.reranker_enabled = False
            hybrid = await runtime.documents.search("evaluator", [query], limit=args.max_k,
                                                    run_id=f"hybrid-cold-{case['id']}")
            await runtime.documents.search("evaluator", [query], limit=args.max_k,
                                           run_id=f"hybrid-warm-{case['id']}")
            reranked = []
            if args.with_reranker:
                runtime.settings.reranker_enabled = True
                reranked = await runtime.documents.search("evaluator", [query], limit=args.max_k,
                    run_id=f"reranked-cold-{case['id']}", rerank_query=query)
                await runtime.documents.search("evaluator", [query], limit=args.max_k,
                    run_id=f"reranked-warm-{case['id']}", rerank_query=query)
            event_ids = [f"hybrid-cold-{case['id']}", f"hybrid-warm-{case['id']}"]
            if args.with_reranker:
                event_ids += [f"reranked-cold-{case['id']}", f"reranked-warm-{case['id']}"]
            placeholders = ",".join("?" for _ in event_ids)
            events = await runtime.db.rows(f"SELECT run_id,data FROM events WHERE run_id IN ({placeholders}) ORDER BY id", tuple(event_ids))
            timing = {row["run_id"]: json.loads(row["data"]) for row in events}
            row = {"id": case["id"], "query": query, "expected_documents": sorted(expected),
                   "vector_retrieved_documents": document_titles(vector_hits),
                   "hybrid_retrieved_documents": document_titles(hybrid),
                   "vector_cold_ms": vector_ms,
                   "hybrid_cold_ms": timing[f"hybrid-cold-{case['id']}"]["total_ms"],
                   "hybrid_warm_ms": timing[f"hybrid-warm-{case['id']}"]["total_ms"],
                   "hybrid_warm_cache_hit": timing[f"hybrid-warm-{case['id']}"]["cache_hit"]}
            row.update({f"vector_{key}": value for key, value in metrics(vector_hits, expected, args.cutoffs).items()})
            row.update({f"hybrid_{key}": value for key, value in metrics(hybrid, expected, args.cutoffs).items()})
            if args.with_reranker:
                row.update({"reranked_retrieved_documents": document_titles(reranked),
                            "reranked_cold_ms": timing[f"reranked-cold-{case['id']}"]["total_ms"],
                            "reranked_warm_ms": timing[f"reranked-warm-{case['id']}"]["total_ms"],
                            "reranked_warm_cache_hit": timing[f"reranked-warm-{case['id']}"]["cache_hit"]})
                row.update({f"reranked_{key}": value for key, value in metrics(reranked, expected, args.cutoffs).items()})
            rows.append(row)
    finally:
        try:
            if not args.demo:
                await runtime.vectors.drop_collections()
        finally:
            await runtime.close()
    baseline, optimized = aggregate(rows, "vector", args.cutoffs), aggregate(rows, "hybrid", args.cutoffs)
    reranked_summary = aggregate(rows, "reranked", args.cutoffs) if args.with_reranker else None
    comparable = [f"recall_at_{k}" for k in args.cutoffs] + [f"precision_at_{k}" for k in args.cutoffs] + [
        f"ndcg_at_{k}" for k in args.cutoffs] + ["mrr_at_max_k"]
    summary = {"dataset": {"corpus_documents": len(corpus), "corpus_chunks": chunk_count,
                            "query_cases": len(rows), "embedding_model": settings.embedding_model,
                            "mode": "demo" if args.demo else "live"},
               "methods": {"vector_only_baseline": baseline, "hybrid_bm25_vector": optimized,
                           "hybrid_plus_reranker": reranked_summary},
               "quality_delta": {key: delta(baseline[key], optimized[key]) for key in comparable},
               "latency": {"vector_only_mean_cold_ms": baseline["mean_cold_ms"],
                           "hybrid_mean_cold_ms": optimized["mean_cold_ms"],
                           "hybrid_mean_warm_ms": round(statistics.mean(row["hybrid_warm_ms"] for row in rows), 2),
                           "hybrid_warm_cache_hit_rate": round(statistics.mean(
                               float(row["hybrid_warm_cache_hit"]) for row in rows), 4)},
               "cost_proxy": {"index_embedding_inputs_one_time": chunk_count,
                              "vector_only_query_embedding_inputs_per_query": 1,
                              "hybrid_cold_query_embedding_inputs_per_query": 1,
                              "hybrid_warm_query_embedding_inputs_per_query": 0,
                              "llm_calls_for_retrieval_evaluation": 0,
                              "reranker_calls_per_cold_query": 1 if args.with_reranker else 0,
                              "note": "请求计数，不是货币成本；实际金额取决于部署时的嵌入与重排服务定价。"},
               "cases": rows}
    if args.with_reranker:
        recall_key = "recall_at_5"
        quality_improved = (reranked_summary["ndcg_at_5"] > optimized["ndcg_at_5"]
                            or reranked_summary["mrr_at_max_k"] > optimized["mrr_at_max_k"])
        summary["reranker_gate"] = {
            "recall_not_lower": reranked_summary[recall_key] >= optimized[recall_key],
            "ranking_improved": quality_improved,
            "p95_added_ms": round(reranked_summary["p95_cold_ms"] - optimized["p95_cold_ms"], 2),
        }
        summary["reranker_gate"]["passed"] = (summary["reranker_gate"]["recall_not_lower"]
            and quality_improved and summary["reranker_gate"]["p95_added_ms"] <= 2000)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.require_reranker_gate and not summary.get("reranker_gate", {}).get("passed"):
        raise RuntimeError("reranker acceptance gate failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, help="JSON: [{name,path}]，name 必须与标注中的文档名一致")
    parser.add_argument("--cases", required=True, help="JSON: [{id,query,relevant_documents}]")
    parser.add_argument("--output", default=".cache/eval/local-retrieval.json")
    parser.add_argument("--max-k", type=int, default=10)
    parser.add_argument("--cutoffs", type=int, nargs="+", default=[3, 5, 10])
    parser.add_argument("--demo", action="store_true", help="使用离线嵌入，仅验证评测可复现性")
    parser.add_argument("--with-reranker", action="store_true", help="调用已配置的真实 HTTP Reranker")
    parser.add_argument("--require-reranker-gate", action="store_true", help="重排未满足启用门槛时返回失败")
    raise SystemExit(asyncio.run(main_async(parser.parse_args())))
