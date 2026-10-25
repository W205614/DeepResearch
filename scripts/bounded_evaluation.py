"""Explicit evaluation entrypoint: persistent transport budgets, never enabled implicitly."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.reliability import ServiceError, estimate_tokens  # noqa: E402


class Budget:
    def __init__(self, path, policy, label="evaluation"):
        self.path, self.policy, self.label = Path(path), policy, label
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(policy, sort_keys=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS policy(value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT)")
            db.execute("""CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,kind TEXT,label TEXT,
                model TEXT,payload_sha256 TEXT,input_bound INTEGER,output_bound INTEGER,
                prompt_tokens INTEGER,completion_tokens INTEGER,status INTEGER)""")
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT value FROM policy").fetchone()
            if prior and prior[0] != encoded:
                raise ValueError("Existing evaluation policy differs; ledger cannot be reset or enlarged")
            if not prior:
                db.execute("INSERT INTO policy VALUES(?)", (encoded,))

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def reserve(self, kind, payload):
        input_bound = estimate_tokens(payload)
        output_bound = int(payload.get("max_tokens", payload.get("max_output_tokens", 0)))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM state WHERE key='halt'").fetchone():
                raise ServiceError("Evaluation stopped", code="evaluation_stopped", retryable=False)
            rows = db.execute("SELECT kind,coalesce(prompt_tokens,input_bound),"
                              "coalesce(completion_tokens,output_bound),label FROM requests").fetchall()
            counts = {key: sum(row[0] == key for row in rows) for key in ("llm", "search", "embedding", "reranker")}
            model_count = counts["llm"] + counts["search"]
            limits = self.policy
            exceeded = (len(rows) >= limits["total_http"] or counts[kind] >= limits[kind + "_http"]
                        or (kind in {"llm", "search"} and model_count >= limits["model_http"])
                        or (kind in {"llm", "search"} and self.label in limits.get("stages", {})
                            and sum(row[0] in {"llm", "search"} and row[3] == self.label for row in rows)
                            >= limits["stages"][self.label])
                        or sum(row[1] for row in rows) + input_bound > limits["input_tokens"]
                        or sum(row[2] for row in rows) + output_bound > limits["output_tokens"])
            if exceeded:
                raise ServiceError("Evaluation budget exhausted before HTTP", code="evaluation_budget", retryable=False)
            return db.execute("INSERT INTO requests(kind,label,model,payload_sha256,input_bound,output_bound) "
                              "VALUES(?,?,?,?,?,?)", (kind, self.label, str(payload.get("model", "")),
                              hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                              input_bound, output_bound)).lastrowid

    def settle(self, index, status, usage):
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
        # Missing usage remains conservatively charged at its reservation; never fabricate zero usage.
        prompt = prompt if type(prompt) is int and prompt >= 0 else None
        completion = completion if type(completion) is int and completion >= 0 else None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE requests SET status=?,prompt_tokens=?,completion_tokens=? WHERE id=?",
                       (status, prompt, completion, index))
            if status in {401, 402, 403, 429, 500, 502, 503, 504}:
                db.execute("INSERT OR REPLACE INTO state VALUES('halt',?)", ("http_" + str(status),))

    def report(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY id")]
            halt = db.execute("SELECT value FROM state WHERE key='halt'").fetchone()
            changes = db.execute("SELECT value FROM state WHERE key LIKE 'policy_change_%' ORDER BY key").fetchall()
            contexts = db.execute("SELECT value FROM state WHERE key LIKE 'execution_context_%' ORDER BY key").fetchall()
        return {"policy": self.policy, "http_requests": len(rows), "halt": halt[0] if halt else None,
                "policy_changes": [json.loads(row[0]) for row in changes],
                "execution_contexts": [json.loads(row[0]) for row in contexts],
                "actual_prompt_tokens": sum(row["prompt_tokens"] or 0 for row in rows),
                "actual_completion_tokens": sum(row["completion_tokens"] or 0 for row in rows),
                "usage_missing_requests": sum(row["prompt_tokens"] is None for row in rows),
                "charged_input_tokens": sum(row["input_bound"] if row["prompt_tokens"] is None else row["prompt_tokens"] for row in rows),
                "charged_output_tokens": sum(row["output_bound"] if row["completion_tokens"] is None else row["completion_tokens"] for row in rows),
                "requests": rows}

    @classmethod
    def amend_stages(cls, path, policy, authorization):
        """Explicit, audited stage transfer; aggregate limits and history stay fixed."""
        authorization = Path(authorization).read_bytes()
        if not authorization.strip():
            raise ValueError("An explicit authorization record is required")
        with sqlite3.connect(path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = json.loads(db.execute("SELECT value FROM policy").fetchone()[0])
            if ({k: v for k, v in previous.items() if k != "stages"}
                    != {k: v for k, v in policy.items() if k != "stages"}):
                raise ValueError("Aggregate budget cannot change")
            old, new = previous.get("stages", {}), policy.get("stages", {})
            if set(old) != set(new) or sum(old.values()) != sum(new.values()) or any(type(v) is not int or v < 0 for v in new.values()):
                raise ValueError("Stage allocation must preserve its total and names")
            counts = dict(db.execute("SELECT label,count(*) FROM requests WHERE kind IN ('llm','search') GROUP BY label"))
            if any(counts.get(label, 0) > cap for label, cap in new.items()):
                raise ValueError("New stage limit is below recorded usage")
            record = {"previous_policy": previous, "next_policy": policy, "http_at_transfer": sum(counts.values()),
                      "authorization_sha256": hashlib.sha256(authorization).hexdigest(), "at": time.time()}
            key = "policy_change_" + str(time.time_ns())
            db.execute("INSERT INTO state VALUES(?,?)", (key, json.dumps(record, sort_keys=True)))
            db.execute("UPDATE policy SET value=?", (json.dumps(policy, sort_keys=True),))

    def install(self):
        original = httpx.AsyncClient.__init__
        budget = self

        async def before(request):
            # Only authenticated supplier POSTs, never webpage downloads or local API reads.
            if request.method != "POST" or "authorization" not in request.headers:
                return
            payload = json.loads(request.content)
            path = request.url.path.rstrip("/")
            kind = ("embedding" if path.endswith("/embeddings") else "reranker" if path.endswith("/rerank")
                    else "search" if path.endswith(("/responses", "/web-search")) else "llm")
            request.extensions["evaluation_request"] = budget.reserve(kind, payload)

        async def after(response):
            index = response.request.extensions.get("evaluation_request")
            if index is None:
                return
            await response.aread()
            try:
                body = response.json()
                usage = body.get("usage") or {} if isinstance(body, dict) else {}
            except (ValueError, TypeError):
                usage = {}
            budget.settle(index, response.status_code, usage)

        def initialize(client, *args, **kwargs):
            original(client, *args, **kwargs)
            client.event_hooks["request"].append(before)
            client.event_hooks["response"].append(after)

        httpx.AsyncClient.__init__ = initialize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--label", default="evaluation")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--amend-stages", type=Path, metavar="AUTHORIZATION")
    parser.add_argument("target", nargs="?")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    policy = json.loads(args.policy.read_bytes())
    if args.amend_stages:
        if args.target:
            raise ValueError("Amendment must be reviewed separately from execution")
        Budget.amend_stages(args.ledger, policy, args.amend_stages)
    budget = Budget(args.ledger, policy, args.label)
    try:
        if args.target:
            from backend.core.config import Settings
            settings = Settings()
            source = hashlib.sha256()
            normalized_source = hashlib.sha256()
            for path in sorted((ROOT / "backend").rglob("*.py")):
                source.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
                normalized_source.update(path.relative_to(ROOT).as_posix().encode() + b"\0"
                                         + path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
            context = {"label": args.label, "http_before": budget.report()["http_requests"],
                       "backend_source_sha256": source.hexdigest(),
                       "backend_normalized_source_sha256": normalized_source.hexdigest(),
                       "checkout_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                       "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
                       "target_sha256": hashlib.sha256(Path(args.target).read_bytes()).hexdigest(),
                       "arguments_sha256": hashlib.sha256(json.dumps(args.arguments).encode()).hexdigest(),
                       "model": settings.llm_model_id,
                       "model_base_url_sha256": hashlib.sha256(settings.llm_base_url.encode()).hexdigest(),
                       "extra_body_sha256": hashlib.sha256(settings.llm_extra_body.encode()).hexdigest()}
            with budget.connect() as db:
                db.execute("INSERT INTO state VALUES(?,?)", ("execution_context_" + str(time.time_ns()), json.dumps(context)))
            budget.install()
            os.environ["EVALUATION_LEDGER"] = str(args.ledger.resolve())
            sys.argv = [args.target, *args.arguments]
            runpy.run_path(args.target, run_name="__main__")
    finally:
        report = args.report or args.ledger.with_suffix(".json")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(budget.report(), ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
