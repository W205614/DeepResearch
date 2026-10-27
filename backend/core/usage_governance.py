"""Persistent, fail-closed budgets for every paid outbound HTTP attempt.

Counters use UTC epoch periods and include pending/unknown exposure. They are
operations configuration, never workspace-admin editable. No prompts, URLs or
credentials are stored. A database transaction reserves all dimensions together.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import time
from uuid import uuid4
from urllib.parse import urlsplit

from .consistency import affected
from .reliability import ServiceError, execution


USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_periods(
 scope_key TEXT NOT NULL,period_start BIGINT NOT NULL,calls BIGINT NOT NULL DEFAULT 0,
 reserved_tokens BIGINT NOT NULL DEFAULT 0,pending_tokens BIGINT NOT NULL DEFAULT 0,
 unknown_tokens BIGINT NOT NULL DEFAULT 0,actual_input BIGINT NOT NULL DEFAULT 0,
 actual_output BIGINT NOT NULL DEFAULT 0,PRIMARY KEY(scope_key,period_start));
CREATE TABLE IF NOT EXISTS usage_attempts(
 id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,subject TEXT NOT NULL,purpose TEXT NOT NULL,
 provider_account TEXT NOT NULL,kind TEXT NOT NULL,period_start BIGINT NOT NULL,
 scope_keys TEXT NOT NULL,reserved_tokens BIGINT NOT NULL,actual_input BIGINT NOT NULL DEFAULT 0,
 actual_output BIGINT NOT NULL DEFAULT 0,usage_known INTEGER NOT NULL DEFAULT 0,
 settled INTEGER NOT NULL DEFAULT 0,outcome TEXT NOT NULL DEFAULT 'pending',created_at DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS usage_attempts_workspace ON usage_attempts(workspace_id,created_at);
CREATE TABLE IF NOT EXISTS provider_probes(
 id TEXT PRIMARY KEY,next_allowed DOUBLE PRECISION NOT NULL,result TEXT NOT NULL DEFAULT '{}',
 actor_subject TEXT NOT NULL,workspace_id TEXT NOT NULL,updated_at DOUBLE PRECISION NOT NULL);
"""


@dataclass(frozen=True)
class UsageScope:
    workspace_id: str
    subject: str
    purpose: str = "request"


usage_scope = ContextVar("paid_usage_scope", default=None)


def paid_identity():
    """Read request/job identity without reserving or making external calls."""
    scope = usage_scope.get()
    return (scope.workspace_id, scope.subject) if scope else None


@contextmanager
def paid_scope(workspace_id, subject, purpose="request"):
    token = usage_scope.set(UsageScope(str(workspace_id), str(subject), str(purpose)))
    try:
        yield
    finally:
        usage_scope.reset(token)


async def resolve_scope(db, settings):
    scope = usage_scope.get()
    ctx = execution.get()
    if ctx:
        row = await ctx.check()
        scope = UsageScope(row["user_id"], row["created_by"], "research")
    if not scope or not scope.workspace_id or not scope.subject:
        raise ServiceError("付费调用缺少费用归属，已阻止外发", code="usage_scope_required", retryable=False)
    if not settings.demo_mode and scope.purpose != "evaluation":
        member = await db.membership(scope.workspace_id, scope.subject)
        if not member or (scope.purpose == "indexing" and member["role"] not in {"admin", "researcher"}):
            # Platform operators may perform probes in a read-only membership.
            if scope.purpose != "probe" or scope.subject not in settings.operator_subjects:
                raise ServiceError("费用主体的工作空间写权限已失效", code="permission_revoked", retryable=False)
    return scope


def provider_account(url, secret):
    """A one-way identifier; rotating a key does not evade platform/user caps."""
    parts = urlsplit(str(url))
    origin = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
    return hashlib.sha256((origin + "\0" + str(secret)).encode()).hexdigest()


class UsageGovernance:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    async def reserve(self, account, kind, tokens):
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens <= 0:
            raise ServiceError("费用预留必须为正整数", code="usage_invalid_reservation", retryable=False)
        if self.db is None:
            raise ServiceError("费用账本不可用，已阻止外发", code="usage_unavailable", retryable=False)
        try:
            scope = await resolve_scope(self.db, self.settings)
            period = int(time.time()) // self.settings.usage_period_seconds * self.settings.usage_period_seconds
            limits = [
                ("platform", self.settings.platform_period_calls, self.settings.platform_period_tokens),
                ("subject:" + scope.subject, self.settings.subject_period_calls, self.settings.subject_period_tokens),
                ("workspace:" + scope.workspace_id, self.settings.workspace_period_calls, self.settings.workspace_period_tokens),
                ("account:" + account, self.settings.account_period_calls, self.settings.account_period_tokens),
            ]
            if scope.purpose == "probe":
                limits.append(("probe", self.settings.probe_period_calls, self.settings.probe_period_tokens))
            limits.sort()
            key = uuid4().hex
            async with self.db.connection() as conn:
                for dimension, max_calls, max_tokens in limits:
                    await conn.execute("INSERT INTO usage_periods(scope_key,period_start) VALUES(?,?) ON CONFLICT DO NOTHING",
                                       (dimension, period))
                    changed = await conn.execute("""UPDATE usage_periods SET calls=calls+1,
                        reserved_tokens=reserved_tokens+?,pending_tokens=pending_tokens+?
                        WHERE scope_key=? AND period_start=? AND calls<?
                        AND actual_input+actual_output+pending_tokens+unknown_tokens+?<=?""",
                        (tokens, tokens, dimension, period, max_calls, tokens, max_tokens))
                    if not affected(changed):
                        raise ServiceError("周期调用或 Token 费用预算已耗尽", code="usage_budget_exhausted", retryable=False)
                await conn.execute("""INSERT INTO usage_attempts(id,workspace_id,subject,purpose,provider_account,
                    kind,period_start,scope_keys,reserved_tokens,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (key, scope.workspace_id, scope.subject, scope.purpose, account, kind, period,
                     json.dumps([item[0] for item in limits]), tokens, time.time()))
                await conn.commit()
            return key
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("费用账本不可用，已阻止外发", code="usage_unavailable", retryable=False) from None

    async def settle(self, key, outcome, body=None, *, not_connected=False):
        if key is None:
            return
        usage = (body or {}).get("usage") if isinstance(body, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
        async with self.db.connection() as conn:
            # Claim settlement before reading; SQLite write locking and PostgreSQL
            # row locking make this idempotent across replicas and duplicate replies.
            changed = await conn.execute("UPDATE usage_attempts SET settled=1 WHERE id=? AND settled=0", (key,))
            if not affected(changed):
                return
            cursor = await conn.execute("SELECT * FROM usage_attempts WHERE id=?", (key,))
            if hasattr(cursor, "fetchone"):
                row = dict(await cursor.fetchone())
            else:
                # The PostgreSQL adapter exposes its native connection for reads.
                row = dict(await conn.connection.fetchrow("SELECT * FROM usage_attempts WHERE id=$1", key))
            if row["kind"] in {"embedding", "reranker"}:
                prompt = prompt if prompt is not None else usage.get("total_tokens")
                completion = 0 if completion is None else completion
            known = (isinstance(prompt, int) and not isinstance(prompt, bool) and prompt >= 0
                     and isinstance(completion, int) and not isinstance(completion, bool) and completion >= 0)
            if not_connected:
                known, prompt, completion = True, 0, 0
            actual_input, actual_output = (prompt, completion) if known else (0, 0)
            unknown = 0 if known else row["reserved_tokens"]
            for dimension in json.loads(row["scope_keys"]):
                await conn.execute("""UPDATE usage_periods SET pending_tokens=pending_tokens-?,
                    unknown_tokens=unknown_tokens+?,actual_input=actual_input+?,actual_output=actual_output+?
                    WHERE scope_key=? AND period_start=?""",
                    (row["reserved_tokens"], unknown, actual_input, actual_output, dimension, row["period_start"]))
            await conn.execute("""UPDATE usage_attempts SET usage_known=?,actual_input=?,actual_output=?,outcome=? WHERE id=?""",
                               (int(known), actual_input, actual_output, str(outcome)[:40], key))
            await conn.commit()

    async def claim_probe(self, probe_key, scope):
        """Reserve cooldown durably before any paid call, including failed probes."""
        stamp = time.time()
        async with self.db.connection() as conn:
            await conn.execute("""INSERT INTO provider_probes(id,next_allowed,actor_subject,workspace_id,updated_at)
                VALUES(?,0,?,?,?) ON CONFLICT DO NOTHING""", (probe_key, scope.subject, scope.workspace_id, stamp))
            changed = await conn.execute("""UPDATE provider_probes SET next_allowed=?,actor_subject=?,workspace_id=?,
                updated_at=?,result='{}' WHERE id=? AND next_allowed<=?""",
                (stamp + self.settings.provider_probe_cooldown_seconds, scope.subject, scope.workspace_id, stamp, probe_key, stamp))
            await conn.commit()
        return bool(affected(changed))
