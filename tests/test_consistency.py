import asyncio
import pytest

from backend.core.db import uid, now
from backend.domain.models import RunRequest
from backend.services.runtime import ConflictError
from backend.quality_gate import QualityDraft, evaluate_draft
from backend.infrastructure.providers import ServiceError
from pydantic import SecretStr


async def test_search_fallback_reserves_another_attempt(runtime, monkeypatch):
    async def hold(*args, **kwargs):
        pass
    monkeypatch.setattr(runtime, 'schedule', hold)
    await runtime.db.ensure_personal_workspace('alice', 'Test', (2, 5))
    run = await runtime.create('alice', RunRequest(data_policy="public", topic='search quota', client_request_id=uid()))
    runtime.settings.max_search_calls = 2
    runtime.settings.demo_mode = False
    runtime.settings.web_search_provider = 'auto'
    runtime.settings.bocha_api_key = SecretStr('fixture-only')
    called = []
    async def failed(*args):
        called.append('primary')
        raise ServiceError('fixture failure')
    async def fallback(*args):
        called.append('fallback')
        return [{'url': 'https://example.org'}]
    monkeypatch.setattr(runtime.providers, 'deepseek_search', failed)
    monkeypatch.setattr(runtime.providers, 'bocha_search', fallback)
    assert await runtime.providers.search('first', run['id'])
    assert await runtime.providers.search('first', run['id'])  # cache is free
    assert not await runtime.providers.search('second', run['id'])
    assert called == ['primary', 'fallback']
    counter = await runtime.db.one('SELECT search_calls FROM counters WHERE run_id=?', (run['id'],))
    assert counter['search_calls'] == 2


async def test_retry_at_capacity_and_thread_mismatch(runtime, monkeypatch):
    async def hold(*args, **kwargs):
        pass
    monkeypatch.setattr(runtime, "schedule", hold)
    await runtime.db.ensure_personal_workspace("alice", "Test", (1, 1))
    request = RunRequest(topic="研究项目", client_request_id=uid())
    first = await runtime.create("alice", request)
    assert (await runtime.create("alice", request))["id"] == first["id"]
    other = await runtime.new_thread("alice", "Other")
    with pytest.raises(ConflictError):
        await runtime.create("alice", request.model_copy(update={"thread_id": other["id"]}))


async def test_daily_usage_is_accounting_only_and_per_run_limit_is_atomic(runtime, monkeypatch):
    async def hold(*args, **kwargs):
        pass
    monkeypatch.setattr(runtime, "schedule", hold)
    await runtime.db.ensure_personal_workspace("alice", "Test", (3, 5))
    runs = [await runtime.create("alice", RunRequest(topic="测试", client_request_id=uid())) for _ in range(2)]
    results = await asyncio.gather(*(runtime.db.reserve_search(runs[i % 2]["id"], 2) for i in range(10)))
    assert sum(results) == 4
    await runtime.purge_user("alice", "reports")
    usage = await runtime.db.one("SELECT search_calls FROM workspace_daily_usage WHERE workspace_id=? AND day=?", ("alice", now()[:10]))
    assert usage["search_calls"] == 4


async def test_delete_during_index_cannot_revive(runtime, monkeypatch):
    document = await runtime.documents.add("alice", "race.txt", b"Research evidence for a deletion race.")
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime.providers.embed
    async def pause(texts):
        entered.set()
        await release.wait()
        return await original(texts)
    monkeypatch.setattr(runtime.providers, "embed", pause)
    task = asyncio.create_task(runtime.documents.ingest(document["id"]))
    await asyncio.wait_for(entered.wait(), 10)
    await runtime.documents.delete(document["id"], "alice")
    release.set()
    await task
    assert (await runtime.db.one("SELECT status FROM documents WHERE id=?", (document["id"],)))["status"] == "deleted"
    assert not await runtime.documents.search("alice", ["Research"])


def test_opposite_fact_and_incomplete_verdict_fail():
    case = {"id": "opposite", "evidence": [{"id": "s1", "text": "42人完成导出"}], "required_terms": ["42"]}
    draft = QualityDraft.model_validate({"claims": [{"text": "42人未完成导出", "source_ids": ["s1"]}]})
    for checks in (None, [], [{"index": 0, "supported": False}],
                   [{"index": 0, "supported": True}, {"index": 0, "supported": True}]):
        assert not evaluate_draft(case, draft, checks)["passed"]


async def test_old_attempt_cannot_complete_or_fail_new_attempt(runtime, monkeypatch):
    async def hold(*a, **k):
        pass
    monkeypatch.setattr(runtime, 'schedule', hold)
    run = await runtime.create('alice', RunRequest(topic='attempt fencing', client_request_id=uid()))
    entered, release = asyncio.Event(), asyncio.Event()
    async def paused(*a, **k):
        entered.set()
        await release.wait()
        return {'report': 'obsolete result', 'validation': {}, 'evidence': []}
    monkeypatch.setattr(runtime.graph, 'ainvoke', paused)
    task = asyncio.create_task(runtime.execute_run(run['id']))
    await entered.wait()
    await runtime.db.execute("UPDATE runs SET attempt_count=2 WHERE id=?", (run['id'],))
    release.set()
    await task
    await runtime.fail(run['id'], 'obsolete error', attempt=1)
    state = await runtime.db.owned_run(run['id'], 'alice')
    assert state['status'] == 'running' and state['report'] == '' and state['attempt_count'] == 2
    assert not await runtime.db.rows("SELECT id FROM events WHERE run_id=? AND type='done'", (run['id'],))
