"""Evaluate labeled local-RAG retrieval without mislabeling source counts as recall."""
import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import Settings
from backend.services.runtime import Runtime


def metrics(hits: list[dict], expected: set[str], cutoffs: list[int]) -> dict:
    titles = [str(hit["title"]) for hit in hits]
    result = {}
    for cutoff in cutoffs:
        observed = set(titles[:cutoff])
        result[f"recall_at_{cutoff}"] = round(len(observed & expected) / len(expected), 4) if expected else None
        gains = [1 if title in expected else 0 for title in titles[:cutoff]]
        dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
        ideal = sum(1 / math.log2(index + 2) for index in range(min(len(expected), cutoff)))
        result[f"ndcg_at_{cutoff}"] = round(dcg / ideal, 4) if ideal else None
    first = next((index + 1 for index, title in enumerate(titles) if title in expected), None)
    result["mrr_at_max_k"] = round(1 / first, 4) if first else 0.0
    return result


async def main_async(args):
    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if not corpus or not cases:
        raise ValueError("语料和评测用例不能为空")
    output = Path(args.output)
    data_dir = output.parent / "retrieval-eval-data"
    if data_dir.exists():
        raise ValueError(f"评测数据目录已存在：{data_dir}；请更换 --output 或手动确认后清理")
    settings = Settings(_env_file=None, demo_mode=args.demo, data_dir=data_dir)
    runtime = await Runtime(settings).start()
    try:
        for row in corpus:
            name, path = row["name"], Path(row["path"])
            document = await runtime.documents.add("evaluator", name, path.read_bytes())
            await runtime.documents.ingest(document["id"])
        rows = []
        for case in cases:
            expected = set(case["relevant_documents"])
            query = case["query"]
            cold = await runtime.documents.search("evaluator", [query], limit=args.max_k, run_id=f"cold-{case['id']}")
            await runtime.documents.search("evaluator", [query], limit=args.max_k, run_id=f"warm-{case['id']}")
            events = await runtime.db.rows("SELECT run_id,data FROM events WHERE run_id IN (?,?) ORDER BY id",
                                           (f"cold-{case['id']}", f"warm-{case['id']}"))
            timing = {row["run_id"].split("-", 1)[0]: json.loads(row["data"]) for row in events}
            rows.append({"id": case["id"], "query": query, "expected_documents": sorted(expected),
                         "retrieved_documents": [hit["title"] for hit in cold],
                         **metrics(cold, expected, args.cutoffs),
                         "cold_total_ms": timing["cold"]["total_ms"], "warm_total_ms": timing["warm"]["total_ms"],
                         "warm_cache_hit": timing["warm"]["cache_hit"]})
    finally:
        await runtime.close()
    summary = {"cases": rows, "case_count": len(rows), "cutoffs": args.cutoffs,
               "mean_recall": {str(k): round(sum(row[f"recall_at_{k}"] for row in rows) / len(rows), 4)
                               for k in args.cutoffs},
               "mean_ndcg": {str(k): round(sum(row[f"ndcg_at_{k}"] for row in rows) / len(rows), 4)
                             for k in args.cutoffs},
               "mean_mrr": round(sum(row["mrr_at_max_k"] for row in rows) / len(rows), 4),
               "mean_cold_ms": round(sum(row["cold_total_ms"] for row in rows) / len(rows), 2),
               "mean_warm_ms": round(sum(row["warm_total_ms"] for row in rows) / len(rows), 2)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, help="JSON: [{name,path}]，name 必须与标注中的文档名一致")
    parser.add_argument("--cases", required=True, help="JSON: [{id,query,relevant_documents}]")
    parser.add_argument("--output", default=".cache/eval/local-retrieval.json")
    parser.add_argument("--max-k", type=int, default=10)
    parser.add_argument("--cutoffs", type=int, nargs="+", default=[3, 5, 10])
    parser.add_argument("--demo", action="store_true", help="仅验证评测流程；不代表真实模型质量")
    raise SystemExit(asyncio.run(main_async(parser.parse_args())))
