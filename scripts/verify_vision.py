"""Opt-in real vision smoke check with a generated, non-sensitive fixture.

Run: uv run python scripts/verify_vision.py
Uses configured model credentials; writes only the synthetic image and sanitized results.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont
from backend.core.config import Settings
from backend.core.db import Database, uid
from backend.core.observability import configure_task_logger
from backend.infrastructure.providers import Providers


async def main():
    folder = Path(".cache/vision-smoke")
    folder.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (800, 360), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 36) if Path("C:/Windows/Fonts/arial.ttf").exists() else ImageFont.load_default(size=36)
    draw.text((40, 30), "SYNTHETIC TEST - NOT REAL BUSINESS DATA", font=font, fill="black")
    draw.text((40, 130), "Team A: 12 completed", font=font, fill="black")
    draw.text((40, 210), "Team B: 7 NOT completed", font=font, fill="black")
    target = folder / "synthetic.png"
    image.save(target)
    db = Database(folder / "usage.sqlite3")
    await db.init()
    settings = Settings(demo_mode=False)
    configure_task_logger("INFO")
    provider = Providers(settings, db)
    run_id = uid()
    try:
        result = await provider.understand_images("只读取图片中两组的人数和完成状态，保留否定关系，不做外部检索。", [(target.read_bytes(), "image/png")], run_id)
        text = result["answer"] + json.dumps(result["observations"], ensure_ascii=False)
        checks = {"direct_mode": result["mode"] == "chat", "number_12": "12" in text,
                  "number_7": "7" in text, "negation_retained": any(token in text for token in ("未完成", "没有完成", "NOT completed"))}
        report = {"requested_model": settings.vision_model_id, "returned_model": result["actual_model"],
                  "checks": checks, "usage": await db.one("SELECT llm_calls,prompt_tokens,completion_tokens FROM counters WHERE run_id=?", (run_id,)),
                  "answer": result["answer"], "synthetic_only": True}
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "answer"}, ensure_ascii=True))
        if not all(checks.values()):
            raise SystemExit("Vision smoke assertion failed; inspect the synthetic result")
    finally:
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
