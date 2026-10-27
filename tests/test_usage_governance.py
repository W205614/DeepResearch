import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.core.config import Settings
from backend.core.db import Database
from backend.core.reliability import ServiceError
from backend.core.usage_governance import UsageGovernance, USAGE_SCHEMA, paid_scope, provider_account
from backend.infrastructure.providers import Providers
from backend.infrastructure.reranker import RerankerClient


async def ledger(tmp_path, **overrides):
    db = Database(tmp_path / "usage.sqlite3")
    await db.init()
    async with db.connection() as conn:
        await conn.executescript(USAGE_SCHEMA)
        await conn.commit()
    settings = Settings(_env_file=None, demo_mode=False, llm_api_key="fixture-key",
                        embedding_api_key="fixture-key", **overrides)
    return db, settings, UsageGovernance(db, settings)


async def test_concurrent_reservations_are_atomic_across_all_dimensions_and_reopen(tmp_path):
    db, settings, governance = await ledger(tmp_path, subject_period_calls=3)
    async def attempt():
        with paid_scope("space", "alice", "evaluation"):
            try:
                return await governance.reserve("account", "model", 100)
            except ServiceError as error:
                assert error.code == "usage_budget_exhausted"
                return None
    attempts = await asyncio.gather(*(attempt() for _ in range(10)))
    assert len([key for key in attempts if key]) == 3
    assert {row["calls"] for row in await db.rows("SELECT * FROM usage_periods")} == {3}
    # New instances use the same durable period; no process restart resets it.
    with paid_scope("other-space", "alice", "evaluation"):
        with pytest.raises(ServiceError, match="预算"):
            await UsageGovernance(Database(db.path), settings).reserve("other-account", "embedding", 10)
    assert await db.one("SELECT * FROM usage_periods WHERE scope_key='account:other-account'") is None


async def test_actual_unknown_and_pending_exposure_remain_distinct_and_settlement_is_idempotent(tmp_path):
    db, _, governance = await ledger(tmp_path)
    with paid_scope("space", "alice", "evaluation"):
        known = await governance.reserve("account", "model", 100)
        unknown = await governance.reserve("account", "model", 80)
        await governance.reserve("account", "model", 20)
    await asyncio.gather(*(governance.settle(known, "http_200", {"usage": {
        "prompt_tokens": 7, "completion_tokens": 3}}) for _ in range(2)))
    await governance.settle(unknown, "http_200", {})
    row = await db.one("SELECT * FROM usage_periods WHERE scope_key='platform'")
    assert (row["reserved_tokens"], row["actual_input"], row["actual_output"]) == (200, 7, 3)
    assert (row["unknown_tokens"], row["pending_tokens"], row["calls"]) == (80, 20, 3)


async def test_unknown_usage_cannot_refund_budget_and_period_rollover_preserves_history(tmp_path, monkeypatch):
    db, _, governance = await ledger(tmp_path, subject_period_tokens=1000, usage_period_seconds=60)
    monkeypatch.setattr("backend.core.usage_governance.time.time", lambda: 120)
    with paid_scope("space", "alice", "evaluation"):
        key = await governance.reserve("account", "model", 1000)
        await governance.settle(key, "timeout")
        with pytest.raises(ServiceError):
            await governance.reserve("account", "model", 1)
        monkeypatch.setattr("backend.core.usage_governance.time.time", lambda: 180)
        await governance.reserve("account", "model", 1000)
    rows = await db.rows("SELECT * FROM usage_periods WHERE scope_key='subject:alice' ORDER BY period_start")
    assert [(row["period_start"], row["calls"]) for row in rows] == [(120, 1), (180, 1)]


@pytest.mark.parametrize("limit", ["platform_period_calls", "workspace_period_calls", "account_period_calls"])
async def test_each_cross_task_dimension_has_an_independent_hard_limit(tmp_path, limit):
    db, _, governance = await ledger(tmp_path, **{limit: 1})
    with paid_scope("space", "alice", "evaluation"):
        await governance.reserve("account", "embedding", 100)
    # A new user/task does not bypass the shared platform/space/provider account limit.
    with paid_scope("space", "bob", "evaluation"):
        with pytest.raises(ServiceError) as captured:
            await governance.reserve("account", "model", 100)
    assert captured.value.code == "usage_budget_exhausted"
    assert len(await db.rows("SELECT * FROM usage_attempts")) == 1


async def test_probe_dedicated_budget_and_not_connected_release_only_tokens(tmp_path):
    db, settings, governance = await ledger(tmp_path, platform_operator_subjects="alice", probe_period_calls=1)
    with paid_scope("space", "alice", "probe"):
        key = await governance.reserve("account", "model", 100)
        await governance.settle(key, "not_connected", not_connected=True)
        with pytest.raises(ServiceError):
            await governance.reserve("account", "model", 100)
    row = await db.one("SELECT * FROM usage_periods WHERE scope_key='probe'")
    assert row["calls"] == 1 and row["pending_tokens"] == row["unknown_tokens"] == row["actual_input"] == 0


@pytest.mark.parametrize("path,kind", [("chat/completions", "model"), ("embeddings", "embedding"),
                                       ("responses", "search"), ("web-search", "search")])
async def test_every_provider_post_is_reserved_before_transport(tmp_path, path, kind):
    db, settings, _ = await ledger(tmp_path)
    sent = []
    async def handle(request):
        attempts = await db.rows("SELECT * FROM usage_attempts")
        assert len(attempts) == 1 and attempts[0]["settled"] == 0
        sent.append(request)
        return httpx.Response(200, json={"usage": {"prompt_tokens": 5, "completion_tokens": 0}})
    provider = Providers(settings, db, httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    with paid_scope("space", "alice", "evaluation"):
        await provider.post("https://supplier.test/" + path, "fixture-key", {"input": "test"}, "fixture", retries=0)
    row = await db.one("SELECT * FROM usage_attempts")
    assert len(sent) == 1 and row["kind"] == kind and row["usage_known"] == 1
    assert "fixture-key" not in str(await db.rows("SELECT * FROM usage_periods"))
    await provider.close()


async def test_http_retries_consume_separate_budget_and_stop_before_second_transport(tmp_path):
    db, settings, _ = await ledger(tmp_path, subject_period_calls=1)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(503, json={})
    provider = Providers(settings, db, httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    with paid_scope("space", "alice", "evaluation"):
        with pytest.raises(ServiceError) as captured:
            await provider.post("https://supplier.test/chat/completions", "fixture-key", {}, "fixture")
    assert captured.value.code == "usage_budget_exhausted"
    assert len(calls) == 1
    assert (await db.one("SELECT * FROM usage_periods WHERE scope_key='platform'"))["unknown_tokens"] > 0
    await provider.close()


async def test_missing_identity_and_broken_ledger_block_before_transport(tmp_path, monkeypatch):
    db, settings, _ = await ledger(tmp_path)
    calls = []
    provider = Providers(settings, db, httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(200, json={}))))
    with pytest.raises(ServiceError) as captured:
        await provider.post("https://supplier.test/chat/completions", "fixture-key", {}, "fixture")
    assert captured.value.code == "usage_scope_required"
    monkeypatch.setattr(db, "connection", lambda: (_ for _ in ()).throw(OSError("unavailable")))
    with paid_scope("space", "alice", "evaluation"):
        with pytest.raises(ServiceError) as captured:
            await provider.governance.reserve("account", "model", 10)
    assert captured.value.code == "usage_unavailable" and calls == []
    await provider.close()


async def test_demo_http_still_requires_accounting_but_pure_demo_model_is_local(tmp_path):
    db, settings, governance = await ledger(tmp_path)
    settings.demo_mode = True
    with pytest.raises(ServiceError) as captured:
        await governance.reserve("account", "reranker", 100)
    assert captured.value.code == "usage_scope_required"
    provider = Providers(settings, db, httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _request: pytest.fail("pure demo must not use HTTP"))))
    assert await provider.embed(["local demo"])
    await provider.close()


async def test_reranker_uses_the_same_durable_budget(tmp_path):
    db, settings, _ = await ledger(tmp_path, allow_internal_model_processing=True, subject_period_calls=1)
    calls = []
    client = RerankerClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
        calls.append(request) or httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]}))), db=db)
    with paid_scope("space", "alice", "evaluation"):
        assert await client.rerank("query", [{"id": "one", "text": "text"}], 1) == [(0, 0.9)]
        with pytest.raises(ServiceError) as captured:
            await client.rerank("query", [{"id": "one", "text": "text"}], 1)
    assert captured.value.code == "usage_budget_exhausted" and len(calls) == 1
    assert (await db.one("SELECT * FROM usage_attempts"))["kind"] == "reranker"
    await client.client.aclose()


async def test_static_configuration_is_free_and_self_created_admin_cannot_probe(api_client):
    app, client = api_client
    provider = app.state.runtime.providers
    provider.structured = AsyncMock()
    provider.embed = AsyncMock()
    assert (await client.post("/api/config/check")).json()["mode"] == "static"
    assert (await client.post("/api/config/probe")).status_code == 403
    provider.structured.assert_not_awaited()
    provider.embed.assert_not_awaited()


async def test_operator_probe_cooldown_is_persistent_cached_and_audited(api_client):
    app, client = api_client
    app.state.runtime.settings.platform_operator_subjects = "alice"
    provider = app.state.runtime.providers
    provider.structured = AsyncMock()
    first = await client.post("/api/config/probe?target=llm")
    second = await client.post("/api/config/probe?target=llm")
    assert first.status_code == second.status_code == 200
    assert first.json()["cached"] is False and second.json()["cached"] is True
    provider.structured.assert_awaited_once()
    rows = await app.state.runtime.db.rows("SELECT action FROM audit_logs WHERE action LIKE 'provider.probe.%'")
    assert {row["action"] for row in rows} == {"provider.probe.started", "provider.probe.finished", "provider.probe.cached"}
    assert provider_account("https://supplier.test/embeddings", "key") == provider_account("https://supplier.test/responses", "key")
