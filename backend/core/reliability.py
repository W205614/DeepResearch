"""Durable execution budgets and fail-closed ownership checks.

Reserved tokens are conservative exposure, not invoiced usage. Unknown upstream
outcomes keep their reservation. No provider response or credential is persisted.
"""
import hashlib
import json
import time
from contextvars import ContextVar
from dataclasses import dataclass


class ServiceError(Exception):
    def __init__(self, message, *, code="service_unavailable", retryable=True,
                 action="稍后重试或联系管理员", stage="", result_unknown=False):
        super().__init__(message)
        self.code, self.retryable, self.action = code, retryable, action
        self.stage, self.result_unknown = stage, result_unknown

    def detail(self):
        return dict(code=self.code, retryable=self.retryable, action=self.action,
                    stage=self.stage or phase.get(), result_unknown=self.result_unknown)


phase = ContextVar("research_phase", default="")
execution = ContextVar("research_execution", default=None)

RUN_COLUMNS = {
    "deadline_at": "DOUBLE PRECISION NOT NULL DEFAULT 0",
    "call_attempts": "INTEGER NOT NULL DEFAULT 0",
    "reserved_tokens": "INTEGER NOT NULL DEFAULT 0",
    "auto_recoveries": "INTEGER NOT NULL DEFAULT 0",
    "error_info": "TEXT NOT NULL DEFAULT '{}'",
    "data_policy": "TEXT NOT NULL DEFAULT 'internal'",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_health(
 id TEXT PRIMARY KEY,failures INTEGER NOT NULL DEFAULT 0,open_until DOUBLE PRECISION NOT NULL DEFAULT 0,
 blocked INTEGER NOT NULL DEFAULT 0,probe_until DOUBLE PRECISION NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS call_attempts(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL,attempt INTEGER NOT NULL,stage TEXT NOT NULL,
 reserved_tokens INTEGER NOT NULL,outcome TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS verified_claims(
 run_id TEXT NOT NULL,cache_key TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(run_id,cache_key));
"""


@dataclass
class Execution:
    db: object
    settings: object
    run_id: str
    attempt: int

    async def check(self):
        row = await self.db.one("SELECT * FROM runs WHERE id=?", (self.run_id,))
        if not row or row["status"] != "running" or row["attempt_count"] != self.attempt:
            raise ServiceError("执行所有权已失效", code="execution_lost", retryable=False)
        if row["deadline_at"] and time.time() >= row["deadline_at"]:
            raise ServiceError("任务累计时限已耗尽，请缩小问题重新提交", code="budget_exhausted", retryable=False)
        # Offline evaluations have no memberships; deployed jobs must retain write permission.
        if not self.settings.demo_mode:
            member = await self.db.membership(row["user_id"], row["created_by"])
            if not member or member["role"] not in {"admin", "researcher"}:
                raise ServiceError("任务发起人的权限已失效", code="permission_revoked", retryable=False)
        return row

    async def reserve(self, tokens):
        from .db import uid
        async with self.db.guard("execution:" + self.run_id):
            await self.check()
            changed = await self.db.execute("""UPDATE runs SET call_attempts=call_attempts+1,
                reserved_tokens=reserved_tokens+? WHERE id=? AND status='running' AND attempt_count=?
                AND call_attempts<? AND reserved_tokens+?<=?""",
                (tokens, self.run_id, self.attempt, self.settings.max_run_call_attempts,
                 tokens, self.settings.max_run_reserved_tokens))
            if not changed:
                raise ServiceError("任务调用或 Token 预算已耗尽", code="budget_exhausted", retryable=False)
            key = uid()
            await self.db.execute("INSERT INTO call_attempts VALUES(?,?,?,?,?,?,?)",
                (key, self.run_id, self.attempt, phase.get(), tokens, "pending", time.time()))
            return key


async def check_execution():
    ctx = execution.get()
    return await ctx.check() if ctx else None


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def estimate_tokens(value):
    """Unknown tokenizer: reserve one token per UTF-8 byte plus framing overhead.

    Image transport bytes are not language tokens; use a separately configured
    conservative per-image allowance at the model gateway instead.
    """
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8")) + 256


def quality(validation):
    kind = validation.get("kind", "research")
    if kind in {"chat", "vision", "greeting", "help", "preference"}:
        return "unverified"
    if validation.get("insufficient"):
        return "insufficient"
    if validation.get("verification_pending"):
        return "partial" if validation.get("supported_claims") else "unverified"
    if not validation.get("checked_claims"):
        return "unknown"
    return "partial" if validation.get("removed_claims") or validation.get("gaps") or validation.get("unanswered_questions") else "complete"


def enrich_run(row, settings=None):
    row["error_info"] = json.loads(row.get("error_info") or "{}") if isinstance(row.get("error_info", ""), str) else row["error_info"]
    row["validation"].setdefault("quality", "unknown")
    exhausted = row["error_info"].get("retryable") is False or (
        row.get("deadline_at", 0) > 0 and row["deadline_at"] <= time.time())
    row["can_resume"] = row["status"] in {"failed", "interrupted", "cancelled"} and not exhausted
    row["recovery"] = {"auto_recoveries": row.get("auto_recoveries", 0),
        "deadline_at": row.get("deadline_at", 0), "call_attempts": row.get("call_attempts", 0),
        "reserved_tokens": row.get("reserved_tokens", 0)}
    if settings:
        row["recovery"].update(remaining_calls=max(0, settings.max_run_call_attempts - row.get("call_attempts", 0)),
            remaining_reserved_tokens=max(0, settings.max_run_reserved_tokens - row.get("reserved_tokens", 0)))
        if row["recovery"]["remaining_calls"] == 0 or row["recovery"]["remaining_reserved_tokens"] == 0:
            row["can_resume"] = False
    return row
