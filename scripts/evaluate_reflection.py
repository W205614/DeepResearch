"""Paired real-model reflection study over frozen, explicitly staged source excerpts."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.config import Settings  # noqa: E402
from backend.core.db import uid  # noqa: E402
from backend.core.security import canonical_url  # noqa: E402
from backend.domain.models import RunRequest  # noqa: E402
from backend.research.graph import ResearchGraph  # noqa: E402
from backend.services.runtime import Runtime  # noqa: E402
from scripts.evaluate_research import evaluate  # noqa: E402


async def one(case, rounds, output):
    directory = output / f"{case['id']}-rounds-{rounds}"
    directory.mkdir(exist_ok=False)
    settings = Settings(demo_mode=False, database_url="", queue_backend="local",
                        document_scan_mode="disabled", object_store_backend="filesystem",
                        data_dir=directory / "runtime", max_reflection_rounds=rounds,
                        max_search_calls=12, task_log_level="WARNING")
    runtime = await Runtime(settings, isolated_mode=True).start()
    try:
        passages = {canonical_url(row["source_url"] + f"?frozen_passage={i}"): row
                    for i, row in enumerate(case["evidence"])}
        stage = {"round": 0}
        graph = ResearchGraph(settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
        scout = graph.web_scout

        async def staged_scout(state):
            stage["round"] = state["round"]
            return await scout(state)

        async def search(query, run_id):
            if not await runtime.db.reserve_search(run_id, settings.max_search_calls):
                return []
            # Identical retrieval policy for both variants. First pass exposes one
            # excerpt; an actual supplemental search can expose remaining excerpts.
            eligible = list(passages.items()) if stage["round"] > 0 else list(passages.items())[:1]
            return [{"url": url, "name": row["title"]} for url, row in eligible]

        async def read(url):
            graph.agents.require("web_fetch")
            return passages[url]["text"]

        graph.web_scout = staged_scout
        graph.read_web = read
        runtime.providers.search = search
        runtime.graph = graph.build(runtime.checkpointer)
        await runtime.db.ensure_personal_workspace("evaluator", "Frozen source reflection study", (0, 2))
        created = await runtime.create("evaluator", RunRequest(data_policy="public", topic=case["question"],
                                                              mode="deep", client_request_id=uid()))
        await runtime.tasks[created["id"]]
        result = await runtime.db.owned_run(created["id"], "evaluator")
        if result["status"] not in {"completed", "insufficient"}:
            raise RuntimeError("Research failed before grading; stop the study and inspect retained events")
        nodes = [json.loads(row["data"])["node"] for row in await runtime.db.rows(
            "SELECT data FROM events WHERE run_id=? AND type='node_end' ORDER BY id", (created["id"],))]
        reflections = [json.loads(row["data"]) for row in await runtime.db.rows(
            "SELECT data FROM events WHERE run_id=? AND type='reflection' ORDER BY id", (created["id"],))]
        saved = directory / "saved"
        saved.mkdir()
        (saved / (case["id"] + ".json")).write_text(json.dumps({
            "report": result["report"], "sources": result["sources"], "status": result["status"],
            "nodes": nodes, "usage": result.get("usage", {})}, ensure_ascii=False), encoding="utf-8")
        grade = await evaluate(case, directory / "external-review", external_results=saved)
        row = {"id": case["id"], "split": case["split"], "reflection_round_limit": rounds,
               "status": result["status"], "grade": grade, "reflections": reflections,
               "report_sha256": hashlib.sha256(result["report"].encode()).hexdigest(),
               "production_original_request_answered": result["validation"].get("original_request_answered"),
               "source_count": len(result["sources"])}
        (directory / "result.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        return row
    finally:
        await runtime.close()


async def main(args):
    raw = args.cases.read_bytes()
    contract = json.loads(args.contract.read_bytes())
    if hashlib.sha256(raw).hexdigest() != contract["new_cases_sha256"]:
        raise ValueError("Frozen case hash differs")
    cases = [row for row in json.loads(raw) if row["split"] == args.split]
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for case in cases:
        for rounds in (0, 2):
            print(json.dumps({"event": "start", "id": case["id"], "rounds": rounds}), flush=True)
            try:
                row = await one(case, rounds, args.output)
            except Exception as exc:
                row = {"id": case["id"], "split": args.split, "reflection_round_limit": rounds,
                       "error_category": type(exc).__name__, "error_code": getattr(exc, "code", None)}
            results.append(row)
            (args.output / "summary.json").write_text(json.dumps({
                "source_sha256": contract["new_cases_sha256"], "results": results,
                "mode": "real model, frozen staged retrieval; no live search or independent human review",
                "sampling": "one run per frozen variant, all outcomes retained"}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"id": row["id"], "rounds": rounds, "passed": row.get("grade", {}).get("passed"),
                              "support": row.get("grade", {}).get("support"), "coverage": row.get("grade", {}).get("coverage"),
                              "error": row.get("error_code")}), flush=True)
            if "error_category" in row or (args.split == "holdout" and rounds == 2 and not row["grade"]["passed"]):
                raise RuntimeError("Study stopped; preserve failure and do not tune on holdout")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("docs/evidence/improvement-20261025/source-independent-cases.json"))
    parser.add_argument("--contract", type=Path, default=Path("docs/evidence/improvement-20261025/frozen-contract.json"))
    parser.add_argument("--split", choices=["development", "holdout"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
