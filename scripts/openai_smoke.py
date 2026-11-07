"""Four explicitly bounded OpenAI probes, with a durable no-retry request ledger."""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.infrastructure.providers import Providers  # noqa: E402

KINDS = ("text", "image", "search", "embedding")
BASE = "https://api.openai.com/v1"


def connect_ledger(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS requests(
        kind TEXT PRIMARY KEY,status TEXT NOT NULL,http_status INTEGER NOT NULL DEFAULT 0,
        error_code TEXT NOT NULL DEFAULT '',usage_json TEXT NOT NULL DEFAULT '{}',
        reserved_at REAL NOT NULL,finished_at REAL NOT NULL DEFAULT 0)""")
    conn.commit()
    return conn


def reserve(conn, kind):
    conn.execute("BEGIN IMMEDIATE")
    if conn.execute("SELECT 1 FROM requests WHERE kind=?", (kind,)).fetchone():
        conn.rollback()
        return False
    if conn.execute("SELECT count(*) FROM requests").fetchone()[0] >= 4:
        conn.rollback()
        raise ValueError("The four-request smoke allowance is exhausted")
    conn.execute("INSERT INTO requests(kind,status,reserved_at) VALUES(?,'reserved',?)", (kind, time.time()))
    conn.commit()
    return True


def payload(kind):
    if kind == "embedding":
        return "/embeddings", {"model": "text-embedding-3-large", "input": ["OpenAI connection check"],
                               "encoding_format": "float", "dimensions": 3072}
    if kind == "search":
        return "/responses", {"model": "gpt-6.1-sol", "reasoning": {"effort": "low"}, "store": False,
                              "input": "Find the official OpenAI API documentation page. Give one native URL citation.",
                              "tools": [{"type": "web_search"}], "tool_choice": {"type": "web_search"},
                              "include": ["web_search_call.action.sources"], "max_tool_calls": 1,
                              "max_output_tokens": 1800}
    content = "Return a JSON object with a single field ok set to true."
    if kind == "image":
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (128, 128), "red").save(buffer, format="PNG")
        data = base64.b64encode(buffer.getvalue()).decode("ascii")
        content = [{"type": "text", "text": "Return the background color in a JSON object with key color."},
                   {"type": "image_url", "image_url": {"url": "data:image/png;base64," + data}}]
    return "/chat/completions", {"model": "gpt-6.1-sol", "reasoning_effort": "low", "store": False,
                                 "messages": [{"role": "user", "content": content}],
                                 "response_format": {"type": "json_object"}, "max_completion_tokens": 1024}


def valid_result(kind, body):
    if kind == "embedding":
        values = body["data"][0]["embedding"]
        return len(values) == 3072 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                          and math.isfinite(v) for v in values) and any(values)
    if kind == "search":
        return bool(Providers._deepseek_citations(body))
    result = json.loads(body["choices"][0]["message"]["content"])
    return result.get("ok") is True if kind == "text" else str(result.get("color", "")).lower() == "red"


def usage_summary(body):
    usage = body.get("usage", {})
    usage = usage if isinstance(usage, dict) else {}
    return {name: usage[name] for name in ("input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(usage.get(name), int) and not isinstance(usage[name], bool) and usage[name] >= 0}


async def run_smoke(key, path, kinds=KINDS, *, client=None):
    if not key:
        return {"passed": False, "error": "openai_key_missing", "requests": []}
    if not kinds or len(set(kinds)) != len(kinds) or any(kind not in KINDS for kind in kinds):
        raise ValueError("Choose each supported smoke category at most once")
    conn = connect_ledger(path)
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=90, trust_env=False, follow_redirects=False)
    try:
        for kind in kinds:
            if not reserve(conn, kind):
                previous = conn.execute("SELECT status FROM requests WHERE kind=?", (kind,)).fetchone()
                if previous["status"] != "passed":
                    break
                continue
            status, code, http_status, usage = "failed", "invalid_response", 0, {}
            try:
                endpoint, body = payload(kind)
                response = await client.post(BASE + endpoint, headers={"Authorization": "Bearer " + key}, json=body)
                http_status = response.status_code
                if response.status_code != 200:
                    code = "http_" + str(response.status_code)
                else:
                    result = response.json()
                    usage = usage_summary(result)
                    if valid_result(kind, result):
                        status, code = "passed", ""
            except (httpx.HTTPError, TimeoutError):
                status, code = "unknown", "network_result_unknown"
            except asyncio.CancelledError:
                status, code = "unknown", "request_interrupted"
                raise
            except (KeyError, TypeError, ValueError, IndexError, AttributeError):
                code = "invalid_response"
            finally:
                conn.execute("UPDATE requests SET status=?,http_status=?,error_code=?,usage_json=?,finished_at=? WHERE kind=?",
                             (status, http_status, code, json.dumps(usage), time.time(), kind))
                conn.commit()
            if status != "passed":
                break
        rows = [dict(row) for row in conn.execute(
            "SELECT kind,status,http_status,error_code,usage_json FROM requests ORDER BY reserved_at,kind")]
        for row in rows:
            row["usage"] = json.loads(row.pop("usage_json"))
        completed = {row["kind"] for row in rows if row["status"] == "passed"}
        return {"passed": set(kinds) <= completed, "requests": rows, "maximum_requests": 4,
                "historical_vectors_rebuilt": False, "full_quality_evaluation": "not_run"}
    finally:
        conn.close()
        if own_client:
            await client.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--ledger", type=Path, default=ROOT / ".cache/openai-smoke/requests.sqlite3")
    parser.add_argument("--output", type=Path, default=ROOT / ".cache/openai-smoke/report.json")
    parser.add_argument("--kinds", nargs="+", choices=KINDS, default=list(KINDS))
    args = parser.parse_args()
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        from dotenv import dotenv_values
        key = dotenv_values(args.env_file).get("OPENAI_API_KEY", "")
    report = asyncio.run(run_smoke(key, args.ledger, args.kinds))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
