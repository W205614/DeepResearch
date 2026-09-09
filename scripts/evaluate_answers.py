"""Run a real-model, redacted answer-quality gate against a versioned golden set."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import Settings
from backend.core.db import Database
from backend.infrastructure.providers import Providers, ServiceError
from backend.quality_gate import QualityDraft, evaluate_draft, summarize


async def main_async(args) -> int:
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    settings = Settings(demo_mode=False)
    if not settings.llm_api_key.get_secret_value():
        raise RuntimeError("LLM_API_KEY is required: the answer-quality gate fails closed without a real model")
    db = Database(output.parent / "answer-quality.sqlite3")
    await db.init()
    providers = Providers(settings, db)
    results = []
    try:
        for case in cases:
            instruction = (
                "只输出 claims 和 limitations。每个事实性 claim 必须引用 evidence 中的 id；"
                "资料不足时不要生成 claim，并在 limitations 说明资料不足。资料冲突时必须在 limitations 说明冲突。"
            )
            draft = await providers.structured(
                "answer_quality", instruction,
                {"question": case["question"], "evidence": case["evidence"]}, QualityDraft,
                "quality-" + case["id"], temperature=0,
            )
            results.append(evaluate_draft(case, draft))
    except ServiceError as exc:
        raise RuntimeError("answer-quality gate could not obtain a valid real-model result") from exc
    finally:
        await providers.close()
    report = {"suite": "answer-quality-v1", "results": results, "summary": summarize(results)}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    if not report["summary"]["passed"]:
        raise RuntimeError("answer-quality gate failed")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="eval/answer_quality_cases.json")
    parser.add_argument("--output", default=".cache/eval/answer-quality.json")
    raise SystemExit(asyncio.run(main_async(parser.parse_args())))
