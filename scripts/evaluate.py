"""Offline reproducible baseline for routing, citations, latency and usage."""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import Settings
from backend.core.db import uid
from backend.domain.models import RunRequest
from backend.services.runtime import Runtime


async def main_async(args):
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    settings = Settings(_env_file=None, demo_mode=True, data_dir=Path(args.output).parent / "eval-data")
    runtime = await Runtime(settings).start()
    results = []
    try:
        for case in cases:
            started = time.perf_counter()
            run = await runtime.create("evaluator", RunRequest(topic=case["topic"], mode=case.get("mode", "auto"),
                client_request_id=uid()))
            await runtime.tasks[run["id"]]
            result = await runtime.db.owned_run(run["id"], "evaluator")
            events = await runtime.db.rows("SELECT data FROM events WHERE run_id=? AND type='route'", (run["id"],))
            route = json.loads(events[0]["data"])["mode"] if events else (
                "quick" if result["validation"].get("kind") == "greeting" else "quick")
            expected_sources = int(case.get("expected_sources", 0))
            results.append({"id": case["id"], "route": route, "expected_route": case["expected_route"],
                "route_match": route == case["expected_route"], "status": result["status"],
                "sources": len(result["sources"]), "supported_claims": result["validation"].get("supported_claims", 0),
                "checked_claims": result["validation"].get("checked_claims", 0), "usage": result["usage"],
                "recall_at_3": min(len(result["sources"]), expected_sources) / expected_sources if expected_sources else 1.0,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)})
        metrics = await runtime.metrics("evaluator")
    finally:
        await runtime.close()
    summary = {"cases": results, "route_accuracy": sum(row["route_match"] for row in results) / len(results),
        "mean_recall_at_3": sum(row["recall_at_3"] for row in results) / len(results),
        "avg_sources": sum(row["sources"] for row in results) / len(results),
        "total_prompt_tokens": sum(row["usage"].get("prompt_tokens", 0) for row in results),
        "total_completion_tokens": sum(row["usage"].get("completion_tokens", 0) for row in results),
        "node_latency_ms": metrics["node_latency_ms"]}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    output.with_suffix(".md").write_text("# 离线评测基线\n\n" + "\n".join(
        f"- {row['id']}: 路由 {'通过' if row['route_match'] else '失败'}，来源 {row['sources']}，耗时 {row['elapsed_ms']} ms"
        for row in results), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="eval/cases.json")
    parser.add_argument("--output", default=".cache/eval/baseline.json")
    raise SystemExit(asyncio.run(main_async(parser.parse_args())))
