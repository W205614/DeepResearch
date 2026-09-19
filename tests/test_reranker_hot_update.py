import asyncio
import json

import httpx
import pytest

import backend.services.documents as documents_module
from backend.core.clamav import ScanRejected
from backend.infrastructure.providers import ServiceError


async def add_ready(runtime, name: str, text: str):
    document = await runtime.documents.add("alice", name, text.encode())
    await runtime.documents.ingest(document["id"])
    return await runtime.db.one("SELECT * FROM documents WHERE id=?", (document["id"],))


async def test_reranker_uses_original_query_and_cache_contract(runtime):
    await add_ready(runtime, "one.md", "ALPHA first evidence")
    await add_ready(runtime, "two.md", "ALPHA second evidence")
    runtime.settings.reranker_enabled = True
    runtime.settings.reranker_url = "https://reranker.test/rerank"
    runtime.settings.reranker_model = "fixture-v1"
    calls = []

    async def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        count = min(body["top_n"], len(body["documents"]))
        return httpx.Response(200, json={"results": [
            {"index": index, "relevance_score": 0.9 - position * 0.1}
            for position, index in enumerate(reversed(range(len(body["documents"]))))
        ][:count]})

    await runtime.reranker.client.aclose()
    runtime.reranker.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rewritten = ["ALPHA rewritten", "second retrieval expansion"]
    first = await runtime.documents.search("alice", rewritten, limit=2, rerank_query="ALPHA original")
    second = await runtime.documents.search("alice", rewritten, limit=2, rerank_query="ALPHA original")

    assert first == second
    assert len(calls) == 1 and calls[0]["query"] == "ALPHA original"
    assert all(row["ranking_stage"] == "rerank" and row["rerank_score"] is not None for row in first)

    runtime.settings.reranker_model = "fixture-v2"
    await runtime.documents.search("alice", rewritten, limit=2, rerank_query="ALPHA original")
    assert len(calls) == 2

    runtime.settings.reranker_candidate_limit += 1
    await runtime.documents.search("alice", rewritten, limit=2, rerank_query="ALPHA original")
    assert len(calls) == 3

    await runtime.documents.search("alice", rewritten, limit=2, rerank_query="ALPHA changed original")
    assert len(calls) == 4


@pytest.mark.parametrize("response", [
    httpx.Response(503),
    httpx.Response(401),
    httpx.Response(200, json={"results": [{"index": 999, "relevance_score": 1.0}]}),
    httpx.Response(200, json={"results": [
        {"index": 0, "relevance_score": 1.0}, {"index": 0, "relevance_score": 0.5}]}),
    httpx.Response(200, json={"results": [{"index": 0, "relevance_score": "high"}]}),
])
async def test_reranker_failure_falls_back_and_is_not_cached(runtime, response):
    await add_ready(runtime, "fallback.md", "ALPHA fallback evidence")
    runtime.settings.reranker_enabled = True
    runtime.settings.reranker_url = "https://reranker.test/rerank"
    runtime.settings.reranker_model = "fixture-v1"
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        return response

    await runtime.reranker.client.aclose()
    runtime.reranker.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    first = await runtime.documents.search("alice", ["ALPHA"], limit=1)
    second = await runtime.documents.search("alice", ["ALPHA"], limit=1)

    assert first == second and first.reasons == ["reranker_unavailable"]
    assert first[0]["ranking_stage"] == "fusion_fallback" and calls == 2
    assert not await runtime.db.one("SELECT result FROM document_search_cache WHERE user_id='alice'")


async def test_reranker_timeout_and_policy_block_are_fallbacks(runtime):
    await add_ready(runtime, "timeout.md", "ALPHA timeout evidence")
    runtime.settings.reranker_enabled = True
    runtime.settings.reranker_url = "https://reranker.test/rerank"
    runtime.settings.reranker_model = "fixture-v1"
    runtime.settings.reranker_timeout_seconds = 0.1
    calls = 0

    async def slow(_request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 1.0}]})

    await runtime.reranker.client.aclose()
    runtime.reranker.client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    timed_out = await runtime.documents.search("alice", ["ALPHA"], limit=1)
    assert calls == 1 and timed_out.reasons == ["reranker_unavailable"]

    runtime.settings.demo_mode = False
    runtime.settings.allow_internal_model_processing = False
    blocked = await runtime.documents.search("alice", ["ALPHA policy"], limit=1)
    assert calls == 1 and blocked.reasons == ["policy_blocked", "reranker_unavailable"]
    assert blocked[0]["ranking_stage"] == "fusion_fallback"


async def test_hot_update_switches_version_and_cleans_old_object(runtime):
    document = await add_ready(runtime, "guide.md", "OLD_ONLY original evidence")
    old_key, old_version = document["object_key"], document["index_version"]

    replacement = await runtime.documents.replace(document["id"], "alice", "guide-v2.md", b"NEW_ONLY replacement evidence")
    assert replacement["status"] == "rebuilding" and replacement["pending_version"] == old_version + 1
    assert await runtime.documents.store.exists("uploads", old_key)
    await runtime.documents.ingest(document["id"])

    current = await runtime.db.one("SELECT * FROM documents WHERE id=?", (document["id"],))
    chunks = await runtime.db.rows("SELECT text FROM chunks WHERE document_id=?", (document["id"],))
    assert current["status"] == "ready" and current["index_version"] == old_version + 1
    assert current["name"] == "guide-v2.md" and current["pending_version"] == 0
    assert "NEW_ONLY" in " ".join(row["text"] for row in chunks)
    assert not await runtime.documents.store.exists("uploads", old_key)


async def test_hot_update_failure_keeps_old_version(runtime, monkeypatch):
    document = await add_ready(runtime, "stable.md", "STABLE_OLD evidence")
    old_key, old_version = document["object_key"], document["index_version"]
    replacement = await runtime.documents.replace(document["id"], "alice", "broken.md", b"BROKEN_NEW evidence")
    pending_key = replacement["pending_object_key"]

    async def failed_embed(_texts):
        raise ServiceError("fixture embedding failure")

    monkeypatch.setattr(runtime.providers, "embed", failed_embed)
    with pytest.raises(ServiceError, match="fixture embedding failure"):
        await runtime.documents.ingest(document["id"])

    current = await runtime.db.one("SELECT * FROM documents WHERE id=?", (document["id"],))
    chunks = await runtime.db.rows("SELECT text FROM chunks WHERE document_id=?", (document["id"],))
    assert current["status"] == "ready" and current["index_version"] == old_version
    assert current["object_key"] == old_key and current["pending_version"] == 0
    assert "STABLE_OLD" in " ".join(row["text"] for row in chunks)
    assert await runtime.documents.store.exists("uploads", old_key)
    assert not await runtime.documents.store.exists("uploads", pending_key)


async def test_hot_update_rejects_unchanged_duplicate_and_delete_wins(runtime):
    first = await add_ready(runtime, "first.md", "FIRST unique evidence")
    await add_ready(runtime, "second.md", "SECOND duplicate target")

    with pytest.raises(ServiceError) as unchanged:
        await runtime.documents.replace(first["id"], "alice", "first.md", b"FIRST unique evidence")
    assert unchanged.value.code == "document_unchanged"
    with pytest.raises(ServiceError) as duplicate:
        await runtime.documents.replace(first["id"], "alice", "duplicate.md", b"SECOND duplicate target")
    assert duplicate.value.code == "document_duplicate"

    replacement = await runtime.documents.replace(first["id"], "alice", "third.md", b"THIRD pending evidence")
    pending_key = replacement["pending_object_key"]
    await runtime.documents.delete(first["id"], "alice")
    await runtime.documents.ingest(first["id"])
    current = await runtime.db.one("SELECT status FROM documents WHERE id=?", (first["id"],))
    assert current["status"] == "deleted"
    assert not await runtime.documents.store.exists("uploads", pending_key)


async def test_pending_version_is_protected_from_cleanup(runtime):
    document = await add_ready(runtime, "protected.md", "PROTECTED old evidence")
    replacement = await runtime.documents.replace(document["id"], "alice", "protected-v2.md", b"PROTECTED new evidence")
    await runtime.db.execute("""INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version,object_key)
        VALUES(?,?,?,?)""", (document["id"], "alice", replacement["pending_version"], replacement["pending_object_key"]))
    await runtime.documents.cleanup_pending()
    assert await runtime.documents.store.exists("uploads", replacement["pending_object_key"])
    assert await runtime.db.one("SELECT version FROM document_cleanup WHERE document_id=? AND version=?",
                                (document["id"], replacement["pending_version"]))


async def test_replacement_scan_failure_keeps_ready_version_and_records_error(runtime, monkeypatch):
    document = await add_ready(runtime, "safe.md", "SAFE current evidence")

    async def reject(_settings, _content):
        raise ScanRejected("fixture replacement rejected")

    monkeypatch.setattr(documents_module, "scan", reject)
    with pytest.raises(ScanRejected):
        await runtime.documents.replace(document["id"], "alice", "unsafe.md", b"UNSAFE replacement evidence")
    current = await runtime.db.one("SELECT * FROM documents WHERE id=?", (document["id"],))
    assert current["status"] == "ready" and current["index_version"] == document["index_version"]
    assert current["error"] == "fixture replacement rejected"
    assert await runtime.documents.store.exists("uploads", document["object_key"])


async def test_concurrent_replacements_cannot_delete_winning_stage(runtime, monkeypatch):
    document = await add_ready(runtime, "race.md", "RACE current evidence")
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused_scan(_settings, _content):
        entered.set()
        await release.wait()

    monkeypatch.setattr(documents_module, "scan", paused_scan)
    first = asyncio.create_task(runtime.documents.replace(
        document["id"], "alice", "race-v2.md", b"RACE winning replacement"))
    await entered.wait()
    second = asyncio.create_task(runtime.documents.replace(
        document["id"], "alice", "race-v3.md", b"RACE losing replacement"))
    await asyncio.sleep(0)
    release.set()
    winner = await first
    with pytest.raises(ServiceError) as busy:
        await second
    assert busy.value.code == "document_busy"
    assert await runtime.documents.store.exists("uploads", winner["pending_object_key"])
