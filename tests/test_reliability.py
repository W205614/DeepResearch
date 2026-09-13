"""Fault combinations: never replace evidence/permission failures with invented answers."""
import asyncio
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.core.db import uid
from backend.core.reliability import Execution, ServiceError, execution
from backend.domain.models import RunRequest, Verification
from backend.research.graph import ResearchGraph, evidence_context
from backend.research.source_validity import validate_sources


async def held_run(runtime, monkeypatch, **kwargs):
    monkeypatch.setattr(runtime, "schedule", AsyncMock())
    run = await runtime.create("alice", RunRequest(topic="公开研究", client_request_id=uid(), **kwargs))
    return run


async def test_expired_task_and_recovery_cap_do_not_requeue(runtime, monkeypatch):
    run = await held_run(runtime, monkeypatch)
    await runtime.db.execute("UPDATE runs SET status='interrupted',auto_recoveries=?,deadline_at=? WHERE id=?",
                             (runtime.settings.max_job_retries, time.time()+100, run["id"]))
    runtime.schedule.reset_mock()
    await runtime.recover_queued_runs()
    await runtime.recover_queued_runs()
    assert runtime.schedule.await_count == 0
    row = await runtime.db.owned_run(run["id"], "alice")
    assert row["error_info"]["code"] == "budget_exhausted" and not row["can_resume"]
    with pytest.raises(ServiceError):
        await runtime.resume(run["id"], "alice")


async def test_retry_attempts_share_durable_budget(runtime, monkeypatch):
    run = await held_run(runtime, monkeypatch, data_policy="public")
    await runtime.db.execute("UPDATE runs SET status='running',attempt_count=1 WHERE id=?", (run["id"],))
    runtime.settings.max_run_call_attempts = 2
    seen = []
    def reply(request):
        seen.append(request)
        return httpx.Response(503, json={})
    await runtime.providers.client.aclose()
    runtime.providers.client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    token = execution.set(Execution(runtime.db, runtime.settings, run["id"], 1))
    try:
        with pytest.raises(ServiceError) as failure:
            await runtime.providers.post("https://model.example/test", "fake", {"messages": []}, "模型")
        assert failure.value.code == "budget_exhausted" and len(seen) == 2
    finally:
        execution.reset(token)
    await runtime.db.execute("UPDATE runs SET status='failed' WHERE id=?", (run["id"],))
    with pytest.raises(ServiceError):
        await runtime.resume(run["id"], "alice")


async def test_auth_failure_breaks_circuit_without_repeated_calls(runtime):
    seen = []
    def reply(request):
        seen.append(request)
        return httpx.Response(401, json={"private": "must-not-leak"})
    await runtime.providers.client.aclose()
    runtime.providers.client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    for _ in range(2):
        with pytest.raises(ServiceError) as error:
            await runtime.providers.post("https://model.example/test", "fake", {}, "模型")
        assert not error.value.retryable and "must-not-leak" not in str(error.value)
    assert len(seen) == 1


async def test_retry_after_does_not_sleep_beyond_budget(runtime):
    await runtime.providers.client.aclose()
    runtime.providers.client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(429, headers={"Retry-After": "3600"}, json={})))
    async with asyncio.timeout(1):
        with pytest.raises(ServiceError, match="限流"):
            await runtime.providers.post("https://model.example/test", "fake", {}, "模型")


async def test_context_rejected_before_network(runtime):
    runtime.settings.llm_context_tokens = 4096
    network = AsyncMock()
    runtime.providers.client.post = network
    with pytest.raises(ServiceError) as error:
        await runtime.providers.post("https://model.example/test", "fake", {"messages": [{"content": "否" * 3000}]}, "模型")
    assert error.value.code == "context_too_large" and not network.called


def test_tail_qualification_is_not_lost():
    text = "一般介绍。\n" * 200 + "结论：这项试验没有证明有效。\n"
    result = evidence_context([{"text": text}], text_limit=180, query="试验有效性")[0]
    assert "没有证明有效" in result["text"] and result["excerpt_truncated"]


async def test_vector_timeout_uses_lexical_result(runtime, monkeypatch):
    doc = await runtime.documents.add("alice", "facts.txt", "任务恢复必须保留预算与检查点。".encode())
    await runtime.documents.ingest(doc["id"])
    async def slow(*args, **kwargs):
        await asyncio.sleep(10)
    monkeypatch.setattr(runtime.providers, "embed", slow)
    runtime.settings.rag_vector_timeout_seconds = .02
    async with asyncio.timeout(1):
        rows = await runtime.documents.search("alice", ["任务恢复"])
    assert rows and "vector_unavailable" in rows.reasons


async def test_rebuild_failure_keeps_old_version_and_evidence(runtime, monkeypatch):
    doc = await runtime.documents.add("alice", "facts.txt", "任务恢复必须保留预算与检查点。".encode())
    await runtime.documents.ingest(doc["id"])
    old = await runtime.documents.search("alice", ["任务恢复"])
    rebuilt = await runtime.documents.reindex(doc["id"], "alice")
    assert rebuilt["status"] == "rebuilding"
    assert await runtime.documents.search("alice", ["任务恢复"])
    monkeypatch.setattr(runtime.providers, "embed", AsyncMock(side_effect=ServiceError("offline")))
    with pytest.raises(ServiceError):
        await runtime.documents.ingest(doc["id"])
    row = await runtime.db.one("SELECT * FROM documents WHERE id=?", (doc["id"],))
    assert row["status"] == "ready" and row["index_version"] == old[0]["index_version"]


async def test_changed_source_is_not_valid_after_reindex(runtime):
    doc = await runtime.documents.add("alice", "facts.txt", "任务恢复必须保留预算。".encode())
    await runtime.documents.ingest(doc["id"])
    row = (await runtime.documents.search("alice", ["预算"]))[0]
    source = dict(row, kind="local", chunk_id=row["id"])
    await validate_sources(runtime.db, "alice", [source])
    await runtime.documents.reindex(doc["id"], "alice")
    await runtime.documents.ingest(doc["id"])
    with pytest.raises(ServiceError) as error:
        await validate_sources(runtime.db, "alice", [source])
    assert error.value.code == "source_changed"


async def test_old_worker_cannot_write_checkpoint(runtime, monkeypatch):
    run = await held_run(runtime, monkeypatch)
    await runtime.db.execute("UPDATE runs SET status='running',attempt_count=2 WHERE id=?", (run["id"],))
    write = AsyncMock()
    monkeypatch.setattr(runtime.checkpointer.inner, "aput_writes", write)
    token = execution.set(Execution(runtime.db, runtime.settings, run["id"], 1))
    try:
        with pytest.raises(ServiceError) as error:
            await runtime.checkpointer.aput_writes({"configurable": {"thread_id": run["id"]}}, [], "node")
        assert error.value.code == "execution_lost" and not write.called
    finally:
        execution.reset(token)


async def test_internal_question_never_uses_web_cache(runtime, monkeypatch):
    run = await held_run(runtime, monkeypatch)
    await runtime.db.execute("INSERT INTO search_cache VALUES(?,?,?)", (run["id"], "secret", '[{"title":"unsafe"}]'))
    rows = await runtime.providers.search("secret", run["id"])
    assert not rows and rows.outcome == "policy_blocked"
    with pytest.raises(ServiceError):
        await runtime.create("alice", RunRequest(topic="secret", data_policy="restricted", client_request_id=uid()))


async def test_revoked_member_cannot_continue(runtime, monkeypatch):
    run = await held_run(runtime, monkeypatch)
    await runtime.db.execute("UPDATE runs SET status='running',attempt_count=1 WHERE id=?", (run["id"],))
    ctx = Execution(runtime.db, runtime.settings.model_copy(update={"demo_mode": False}), run["id"], 1)
    with pytest.raises(ServiceError) as error:
        await ctx.check()
    assert error.value.code == "permission_revoked"


async def test_partial_verification_persists_and_reuses_only_checked_claims(runtime, monkeypatch):
    run = await held_run(runtime, monkeypatch)
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    data = {"claims": [{"index": 0, "text": "来源支持的描述", "source_ids": ["W1"]},
                       {"index": 1, "text": "没有证据的描述", "source_ids": ["W1"]}],
            "sources": {"W1": {"id": "W1", "kind": "web", "text": "来源支持的描述"}}}
    model = AsyncMock(return_value=Verification(checks=[{"index": 0, "supported": True, "reason": "supported"}]))
    monkeypatch.setattr(runtime.providers, "structured", model)
    await graph.ask("validator", "核查", data, Verification, run["id"])
    model.side_effect = ServiceError("offline")
    await graph.ask("validator", "核查", data, Verification, run["id"])
    assert model.await_count == 1
    report, sources = await runtime.partial_report(run["id"])
    assert "来源支持的描述" in report and "没有证据的描述" not in report and len(sources) == 1


async def test_vector_outage_does_not_remove_core_readiness(runtime, monkeypatch):
    monkeypatch.setattr(runtime.vectors, "ping", AsyncMock(side_effect=ServiceError("offline")))
    await runtime.ready()
    caps = await runtime.capabilities()
    assert caps["read_history"] and not caps["vector_search"]


async def test_done_event_commits_even_if_postcommit_events_fail(runtime, monkeypatch):
    run = await held_run(runtime, monkeypatch)
    monkeypatch.setattr(runtime.graph, "ainvoke", AsyncMock(return_value={"report": "done", "validation": {"kind": "chat"}}))
    original = runtime.db.event
    async def event(run_id, kind, data):
        if kind == "result_status":
            raise OSError("event delivery failed")
        await original(run_id, kind, data)
    monkeypatch.setattr(runtime.db, "event", event)
    await runtime.execute_run(run["id"])
    row = await runtime.db.owned_run(run["id"], "alice")
    assert row["status"] == "completed" and row["validation"]["quality"] == "unverified"
    assert len(await runtime.db.rows("SELECT id FROM events WHERE run_id=? AND type='done'", (run["id"],))) == 1


async def test_sqlite_budget_migration_is_repeatable(runtime):
    await runtime.db.init()
    assert await runtime.db.rows("SELECT * FROM provider_health") == []
    assert json.loads((await runtime.db.one("SELECT '{}' AS value"))["value"]) == {}


def test_backup_copy_rejects_corrupt_manifest_and_preserves_existing_destination(tmp_path):
    from scripts.copy_backup import copy_verified, checksum
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / "data.dump").write_bytes(b"synthetic backup")
    manifest = {"version": 2, "files": {"data.dump": checksum(source / "data.dump")}}
    (source / "manifest.json").write_text(json.dumps(manifest))
    assert copy_verified(source, target) == 2
    with pytest.raises(ValueError):
        copy_verified(source, target)
    (source / "data.dump").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum"):
        copy_verified(source, tmp_path / "bad-target")
    assert not (tmp_path / "bad-target").exists()


def test_document_worker_configuration_is_explicit():
    from backend.worker import DocumentWorkerSettings
    assert {"redis_settings", "on_startup", "on_shutdown", "queue_name", "job_timeout"} <= vars(DocumentWorkerSettings).keys()


async def test_cancel_during_external_request_rejects_late_response(runtime, monkeypatch):
    run = await held_run(runtime, monkeypatch, data_policy="public")
    await runtime.db.execute("UPDATE runs SET status='running',attempt_count=1 WHERE id=?", (run["id"],))
    entered, release = asyncio.Event(), asyncio.Event()
    async def response(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"result": "late"})
    await runtime.providers.client.aclose()
    runtime.providers.client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    async def call():
        token = execution.set(Execution(runtime.db, runtime.settings, run["id"], 1))
        try:
            return await runtime.providers.post("https://model.example/test", "fake", {}, "model")
        finally:
            execution.reset(token)
    task = asyncio.create_task(call())
    await entered.wait()
    await runtime.cancel(run["id"], "alice")
    release.set()
    with pytest.raises(ServiceError) as error:
        await task
    assert error.value.code == "execution_lost"
    assert (await runtime.db.owned_run(run["id"], "alice"))["status"] == "cancelled"


async def test_failed_database_commit_does_not_acknowledge_task(runtime, monkeypatch):
    from contextlib import asynccontextmanager
    from backend.core.db import Database
    original = Database.connection
    @asynccontextmanager
    async def full_disk(self):
        async with original(self) as connection:
            execute = connection.execute
            async def write(sql, args=()):
                if "INSERT INTO runs(" in sql:
                    raise OSError("disk full")
                return await execute(sql, args)
            connection.execute = write
            yield connection
    monkeypatch.setattr(Database, "connection", full_disk)
    with pytest.raises(OSError, match="disk full"):
        await runtime.create("alice", RunRequest(topic="test", client_request_id=uid()))
    assert not await runtime.db.rows("SELECT id FROM runs")
