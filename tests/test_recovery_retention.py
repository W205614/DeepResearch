"""Offline regressions for durable recovery and deletion; no supplier calls."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock
from typing import TypedDict

import pytest
from langgraph.graph import StateGraph, END

from backend.core.db import now, uid
from backend.domain.models import RunRequest


async def held_run(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "schedule", AsyncMock())
    return await runtime.create("alice", RunRequest(data_policy="public", topic="恢复验证", client_request_id=uid()))


async def test_java_schedule_resume_restarts_terminal_retrieval_failure(runtime, monkeypatch):
    class State(TypedDict, total=False):
        validation: dict
        report: str

    calls = []

    async def retrieve(state):
        calls.append(1)
        return {"report": "", "validation": {"retrieval_failed": len(calls) == 1}}

    builder = StateGraph(State)
    builder.add_node("retrieve", retrieve)
    builder.set_entry_point("retrieve")
    builder.add_edge("retrieve", END)
    runtime.graph = builder.compile(checkpointer=runtime.checkpointer)
    run = await held_run(runtime, monkeypatch)
    await runtime.execute_run(run["id"])
    assert (await runtime.db.owned_run(run["id"], "alice"))["status"] == "failed"
    await runtime.db.execute("UPDATE runs SET status='queued' WHERE id=?", (run["id"],))
    await runtime.execute_run(run["id"], resume=True)
    assert len(calls) == 2
    assert (await runtime.db.owned_run(run["id"], "alice"))["status"] == "completed"


async def test_object_put_failure_is_reuploadable_instead_of_stuck_scanning(runtime, monkeypatch):
    original = runtime.documents.store.put
    monkeypatch.setattr(runtime.documents.store, "put", AsyncMock(side_effect=OSError("offline")))
    with pytest.raises(Exception):
        await runtime.documents.add("alice", "policy.txt", b"safe policy")
    document = await runtime.db.one("SELECT * FROM documents WHERE user_id='alice'")
    assert document["status"] == "scan_failed"
    monkeypatch.setattr(runtime.documents.store, "put", original)
    retried = await runtime.documents.add("alice", "policy.txt", b"safe policy")
    assert retried["status"] == "indexing"


async def test_single_thread_delete_removes_artifacts_and_dead_letter(runtime, monkeypatch):
    run = await held_run(runtime, monkeypatch)
    await runtime.db.execute("UPDATE runs SET status='failed' WHERE id=?", (run["id"],))
    await runtime.db.execute("INSERT INTO run_artifacts VALUES(?,?,?,?,?,?,?)",
                             (run["id"], "alice", 0, -1, '{}', '[{"text":"private"}]', now()))
    await runtime.db.execute("INSERT INTO dead_letter_runs VALUES(?,?,?,?,?,?)",
                             (run["id"], "alice", "test", "private", now(), ""))
    await runtime.delete_thread(run["thread_id"], "alice")
    for table in ("run_artifacts", "dead_letter_runs"):
        assert not await runtime.db.one(f"SELECT run_id FROM {table} WHERE run_id=?", (run["id"],))


async def test_heartbeat_database_failure_is_interrupted_not_user_cancelled(runtime, monkeypatch):
    import backend.services.runtime as module
    monkeypatch.setattr(module, "RUN_HEARTBEAT_SECONDS", 0.01)
    run = await held_run(runtime, monkeypatch)
    entered = asyncio.Event()

    async def waiting(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime.graph, "ainvoke", waiting)
    # Execution.check reads the run first. Simulate a single heartbeat read failure,
    # while the cancellation handler can immediately persist the interruption.
    one = runtime.db.one
    failed = False

    async def transient(sql, args=()):
        nonlocal failed
        if entered.is_set() and not failed and sql == "SELECT * FROM runs WHERE id=?":
            failed = True
            raise ConnectionError("database unavailable")
        return await one(sql, args)

    monkeypatch.setattr(runtime.db, "one", transient)
    await asyncio.wait_for(runtime.execute_run(run["id"]), 2)
    row = await runtime.db.owned_run(run["id"], "alice")
    assert row["status"] == "interrupted"
    assert row["error_info"]["code"] == "infrastructure_unavailable"
    assert row["can_resume"]


def test_python_and_java_share_command_wire_cases():
    from types import SimpleNamespace
    from backend.core.recovery_contract import parse_schedule, can_resume, VERSION
    fixture = json.loads((Path(__file__).parents[1] / "backend/core/research_command_contract.json").read_text())
    assert fixture["contract_version"] == VERSION
    for case in fixture["schedule_cases"]:
        try:
            parse_schedule(case["payload"])
            valid = True
        except ValueError:
            valid = False
        assert valid == case["valid"], case["name"]
    limits = fixture['resume_fixture_limits']
    settings = SimpleNamespace(max_run_call_attempts=limits['max_calls'], max_run_reserved_tokens=limits['max_tokens'])
    for case in fixture['resume_cases']:
        row = {'status': case['status'], 'error_info': {'retryable': case['retryable']},
               'deadline_at': case['deadline'], 'call_attempts': case['calls'], 'reserved_tokens': case['tokens']}
        assert can_resume(row, settings, epoch=limits['epoch']) == case['expected'], case['name']


async def test_delayed_versioned_command_cannot_schedule_newer_attempt(runtime, monkeypatch):
    run = await held_run(runtime, monkeypatch)
    await runtime.db.execute("UPDATE runs SET attempt_count=2 WHERE id=?", (run["id"],))
    runtime.schedule.reset_mock()
    result = await runtime.accept_schedule(run["id"], {"contract_version": "research-command-v1", "resume": True, "expected_attempt": 1})
    assert not result["accepted"]
    runtime.schedule.assert_not_awaited()


async def test_old_queue_attempt_cannot_claim_newly_queued_execution(runtime, monkeypatch):
    run = await held_run(runtime, monkeypatch)
    await runtime.db.execute("UPDATE runs SET attempt_count=2,call_attempts=7,reserved_tokens=1234 WHERE id=?", (run['id'],))
    graph = AsyncMock()
    monkeypatch.setattr(runtime.graph, 'ainvoke', graph)
    await runtime.execute_run(run['id'], resume=True, expected_attempt=1)
    graph.assert_not_awaited()
    row = await runtime.db.owned_run(run['id'],'alice')
    assert row['status']=='queued' and row['attempt_count']==2
    assert row['call_attempts']==7 and row['reserved_tokens']==1234


async def test_scanning_crash_rescans_and_never_promotes_unscanned_bytes(runtime, monkeypatch):
    import hashlib
    content = b"isolated policy"
    stamp, doc_id = now(), uid()
    key = doc_id + ".v1"
    await runtime.db.execute("""INSERT INTO documents(id,user_id,name,hash,status,created_at,updated_at,index_version,object_key)
        VALUES(?,?,?,?,'scanning',?,?,1,?)""", (doc_id, "alice", "policy.txt", hashlib.sha256(content).hexdigest(), stamp, stamp, key))
    await runtime.documents.store.put("quarantine", key, content)
    from backend.core.clamav import ScanRejected
    monkeypatch.setattr("backend.services.documents.scan", AsyncMock(side_effect=ScanRejected("test rejection")))
    await runtime.documents.recover_scans()
    assert (await runtime.db.one("SELECT status FROM documents WHERE id=?", (doc_id,)))["status"] == "quarantined"
    assert not await runtime.documents.store.exists("uploads", key)
    assert (await runtime.db.one("SELECT state FROM document_scan_jobs WHERE document_id=?", (doc_id,)))["state"] == "failed"


async def test_external_cleanup_failure_keeps_tombstone_then_retries(runtime, monkeypatch):
    run = await held_run(runtime, monkeypatch)
    await runtime.db.execute("UPDATE runs SET status='failed' WHERE id=?", (run["id"],))
    original = runtime.checkpointer.adelete_thread
    monkeypatch.setattr(runtime.checkpointer, "adelete_thread", AsyncMock(side_effect=OSError("temporarily offline")))
    deleted = await runtime.delete_thread(run["thread_id"], "alice")
    assert deleted["cleanup_pending"]
    assert not await runtime.db.one("SELECT id FROM runs WHERE id=?", (run["id"],))
    assert await runtime.db.one("SELECT run_id FROM run_cleanup WHERE run_id=?", (run["id"],))
    monkeypatch.setattr(runtime.checkpointer, "adelete_thread", original)
    await runtime.cleanup_runs()
    assert not await runtime.db.one("SELECT run_id FROM run_cleanup WHERE run_id=?", (run["id"],))


async def test_retention_cleans_only_bounded_caches_and_orphans_by_default(runtime):
    runtime.settings.retention_cleanup_batch_size = 2
    for index in range(4):
        await runtime.db.execute("INSERT INTO web_cache VALUES(?,?,?,?,?)", (f"https://example.test/{index}", "old", "2000-01-01", "full", ""))
    await runtime.db.execute("INSERT INTO run_artifacts VALUES('orphan','alice',1,0,'{}','[]','2000-01-01')")
    await runtime.db.execute("INSERT INTO document_versions VALUES('doc','alice',1,'h','n','key','ready','[]','[]','{}','{}','2000-01-01')")
    result = await runtime.retention.reconcile()
    assert result["source_evidence_deleted"] == 0
    assert not await runtime.db.one("SELECT run_id FROM run_artifacts WHERE run_id='orphan'")
    assert await runtime.db.one("SELECT document_id FROM document_versions WHERE document_id='doc'")
    assert result["cache_deleted"]["web_cache"] <= result["policy"]["batch"]
    assert (await runtime.db.one("SELECT COUNT(*) AS total FROM web_cache"))["total"] == 2


async def test_scan_storage_move_failure_retries_safely(runtime, monkeypatch):
    original = runtime.documents.store.move
    monkeypatch.setattr(runtime.documents.store, "move", AsyncMock(side_effect=OSError("copy unavailable")))
    with pytest.raises(OSError):
        await runtime.documents.add("alice", "move.txt", b"safe move policy")
    document = await runtime.db.one("SELECT * FROM documents WHERE user_id='alice'")
    assert document["status"] == "scan_failed"
    assert await runtime.documents.store.exists("quarantine", document["object_key"])
    monkeypatch.setattr(runtime.documents.store, "move", original)
    await runtime.db.execute("UPDATE document_scan_jobs SET updated_at='2000-01-01T00:00:00+00:00' WHERE document_id=?",(document['id'],))
    await runtime.documents.recover_scans()
    document = await runtime.db.one("SELECT * FROM documents WHERE id=?", (document["id"],))
    assert document["status"] == "indexing"
    assert await runtime.documents.store.exists("uploads", document["object_key"])


async def test_scanner_unavailable_recovery_is_bounded(runtime, monkeypatch):
    from backend.core.clamav import ScanUnavailable
    scanner = AsyncMock(side_effect=ScanUnavailable("scanner unavailable"))
    monkeypatch.setattr("backend.services.documents.scan", scanner)
    with pytest.raises(ScanUnavailable):
        await runtime.documents.add("alice", "budget.txt", b"safe content")
    for _ in range(runtime.settings.max_job_retries + 3):
        await runtime.db.execute("UPDATE document_scan_jobs SET updated_at='2000-01-01T00:00:00+00:00'")
        await runtime.documents.recover_scans()
    document = await runtime.db.one("SELECT * FROM documents WHERE user_id='alice'")
    job = await runtime.db.one("SELECT * FROM document_scan_jobs WHERE document_id=?", (document["id"],))
    assert document["status"] == "scan_failed"
    assert job["state"] == "failed"
    assert scanner.await_count == runtime.settings.max_job_retries + 1


async def test_deletion_sql_failure_rolls_back_records_and_tombstone(runtime, monkeypatch):
    run = await held_run(runtime, monkeypatch)
    await runtime.db.execute("UPDATE runs SET status='failed' WHERE id=?", (run["id"],))
    original = runtime._delete_run_records

    async def fail_after_deletes(conn, run_id, user):
        await original(conn, run_id, user)
        raise OSError("database commit unavailable")

    monkeypatch.setattr(runtime, "_delete_run_records", fail_after_deletes)
    with pytest.raises(OSError):
        await runtime.delete_thread(run["thread_id"], "alice")
    assert await runtime.db.one("SELECT id FROM runs WHERE id=?", (run["id"],))
    assert not await runtime.db.one("SELECT run_id FROM run_cleanup WHERE run_id=?", (run["id"],))


async def test_opt_in_archive_is_immutable_and_explicit_deletion_removes_it(runtime, monkeypatch):
    runtime.settings.retention_archive_after_days = 1
    run = await held_run(runtime, monkeypatch)
    await runtime.db.execute("UPDATE runs SET status='completed',updated_at='2000-01-01',report='old' WHERE id=?", (run["id"],))
    await runtime.db.execute("INSERT INTO run_artifacts VALUES(?,?,?,?,?,?,?)", (run["id"], "alice", 0, -1, '{}', '[]', now()))
    result = await runtime.retention.reconcile()
    assert result["archived"] == 1
    archived = await runtime.db.one("SELECT * FROM retention_archives WHERE source_id=?", (run["id"],))
    await runtime.db.execute("UPDATE runs SET report='new' WHERE id=?", (run["id"],))
    await runtime.retention.reconcile()
    assert await runtime.db.one("SELECT * FROM retention_archives WHERE source_id=?", (run["id"],)) == archived
    await runtime.delete_thread(run["thread_id"], "alice")
    assert not await runtime.db.one("SELECT * FROM retention_archives WHERE source_id=?", (run["id"],))


async def test_internal_http_schedule_rejects_unknown_versions_and_stale_attempts(settings, monkeypatch):
    import httpx
    from pydantic import SecretStr
    from backend.api.app import create_app

    settings.internal_service_token = SecretStr("offline-contract-test")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        run = await held_run(runtime, monkeypatch)
        runtime.schedule.reset_mock()
        headers = {"Authorization": "Bearer offline-contract-test"}
        url = f"/internal/agent/runs/{run['id']}/schedule"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
            invalid = await client.post(url, headers=headers, json={"contract_version": "future", "resume": True})
            assert invalid.status_code == 422
            stale = await client.post(url, headers=headers, json={"contract_version": "research-command-v1", "resume": True, "expected_attempt": 3})
            assert stale.status_code == 202 and not stale.json()['accepted']
            accepted = await client.post(url, headers=headers, json={"contract_version": "research-command-v1", "resume": True, "expected_attempt": 0})
            assert accepted.status_code == 202 and accepted.json()['accepted']
            runtime.schedule.assert_awaited_once_with(run['id'], resume=True, expected_attempt=0)


async def test_revoked_source_never_saves_checkpoint_partial_evidence(runtime, monkeypatch):
    run=await held_run(runtime,monkeypatch)
    await runtime.db.execute("UPDATE runs SET status='running',attempt_count=1 WHERE id=?",(run['id'],))
    partial=AsyncMock(return_value=('private excerpt',[{'id':'L-private','text':'private'}]))
    monkeypatch.setattr(runtime,'partial_report',partial)
    await runtime.fail(run['id'],'source revoked',attempt=1,detail={'code':'source_permission_denied','retryable':False,'action':'request access'})
    partial.assert_not_awaited()
    row=await runtime.db.owned_run(run['id'],'alice')
    assert not row['report'] and not row['sources'] and not row['can_resume']


async def test_cancelled_replacement_retains_cleanup_ownership_and_allows_reupload(runtime,monkeypatch):
    document=await runtime.documents.add('alice','original.txt',b'original stable evidence')
    await runtime.db.execute("UPDATE documents SET status='ready' WHERE id=?",(document['id'],))
    original_put=runtime.documents.store.put
    entered,release=asyncio.Event(),asyncio.Event()

    async def paused_put(area,key,content):
        await original_put(area,key,content)
        entered.set()
        await release.wait()

    monkeypatch.setattr(runtime.documents.store,'put',paused_put)
    task=asyncio.create_task(runtime.documents.replace(document['id'],'alice','replacement.txt',b'new evidence'))
    await asyncio.wait_for(entered.wait(),2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()  # The underlying storage mutation still owns the lock.
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    current=await runtime.db.one('SELECT * FROM documents WHERE id=?',(document['id'],))
    assert current['status']=='ready' and current['hash']==document['hash']
    assert await runtime.documents.store.exists('uploads',document['object_key'])

    cleanup=await runtime.db.one('SELECT * FROM document_cleanup WHERE document_id=?',(document['id'],))
    assert cleanup and cleanup['version']==document['index_version']+1
    await runtime.documents.cleanup_pending()
    assert not await runtime.documents.store.exists('quarantine',cleanup['object_key'])
    monkeypatch.setattr(runtime.documents.store,'put',original_put)
    replacement=await runtime.documents.replace(document['id'],'alice','replacement.txt',b'new evidence')
    assert replacement['status']=='rebuilding' and replacement['pending_version']==document['index_version']+1
    assert await runtime.documents.store.exists('uploads',document['object_key'])


async def test_concurrent_scan_recoverers_recheck_persisted_cooldown_without_spending_attempts(runtime,monkeypatch):
    from datetime import datetime,timedelta,timezone
    from backend.core.clamav import ScanUnavailable
    scanner=AsyncMock(side_effect=ScanUnavailable('scanner starting'))
    monkeypatch.setattr('backend.services.documents.scan',scanner)
    with pytest.raises(ScanUnavailable):
        await runtime.documents.add('alice','cooldown.txt',b'complete staged original')
    document=await runtime.db.one("SELECT * FROM documents WHERE user_id='alice'")
    await asyncio.gather(*(runtime.documents.recover_scans() for _ in range(4)))
    assert scanner.await_count==1
    job=await runtime.db.one('SELECT * FROM document_scan_jobs WHERE document_id=?',(document['id'],))
    assert job['attempts']==1 and job['state']=='pending'
    old=(datetime.now(timezone.utc)-timedelta(seconds=21)).isoformat()
    await runtime.db.execute('UPDATE document_scan_jobs SET updated_at=? WHERE document_id=?',(old,document['id']))
    await asyncio.gather(*(runtime.documents.recover_scans() for _ in range(4)))
    job=await runtime.db.one('SELECT * FROM document_scan_jobs WHERE document_id=?',(document['id'],))
    assert scanner.await_count==2 and job['attempts']==2 and job['state']=='pending'
    await asyncio.gather(*(runtime.documents.recover_scans() for _ in range(4)))
    assert scanner.await_count==2
    scanner.side_effect=None
    scanner.return_value=None
    old=(datetime.now(timezone.utc)-timedelta(seconds=41)).isoformat()
    await runtime.db.execute('UPDATE document_scan_jobs SET updated_at=? WHERE document_id=?',(old,document['id']))
    await asyncio.gather(*(runtime.documents.recover_scans() for _ in range(4)))
    current=await runtime.db.one('SELECT * FROM documents WHERE id=?',(document['id'],))
    job=await runtime.db.one('SELECT * FROM document_scan_jobs WHERE document_id=?',(document['id'],))
    assert current['status']=='indexing' and job['state']=='completed'
    assert scanner.await_count==3 and job['attempts']==3  # Existing cap is unchanged.


async def test_delete_stage_rejects_wrong_workspace_before_any_sql_mutation(runtime,monkeypatch):
    run=await held_run(runtime,monkeypatch)
    async with runtime.db.connection() as conn:
        with pytest.raises(PermissionError):
            await runtime._delete_run_records(conn,run['id'],'bob')
    assert await runtime.db.one('SELECT id FROM runs WHERE id=?',(run['id'],))
    assert not await runtime.db.one('SELECT run_id FROM run_cleanup WHERE run_id=?',(run['id'],))


async def test_concurrent_run_cleaners_recheck_intent_and_delete_checkpoint_once(runtime,monkeypatch):
    run=await held_run(runtime,monkeypatch)
    async with runtime.db.connection() as conn:
        await runtime._delete_run_records(conn,run['id'],'alice')
        await conn.execute('DELETE FROM runs WHERE id=?',(run['id'],))
        await conn.commit()
    delete=AsyncMock()
    monkeypatch.setattr(runtime.checkpointer,'adelete_thread',delete)
    selected=0
    ready=asyncio.Event()
    original=runtime.db.rows
    async def select_together(sql,args=()):
        nonlocal selected
        rows=await original(sql,args)
        if 'FROM run_cleanup ORDER BY' in sql:
            selected+=1
            if selected==2:
                ready.set()
            await asyncio.wait_for(ready.wait(),2)
        return rows
    monkeypatch.setattr(runtime.db,'rows',select_together)
    await asyncio.gather(runtime.cleanup_runs(),runtime.cleanup_runs())
    assert delete.await_count==1
    assert not await runtime.db.one('SELECT run_id FROM run_cleanup WHERE run_id=?',(run['id'],))


async def test_cleanup_reads_only_committed_intents_and_protects_live_run(runtime,monkeypatch):
    run=await held_run(runtime,monkeypatch)
    delete=AsyncMock()
    monkeypatch.setattr(runtime.checkpointer,'adelete_thread',delete)
    async with runtime.db.connection() as conn:
        await runtime._delete_run_records(conn,run['id'],'alice')
        result=await runtime.cleanup_runs()
        assert result.examined==0  # Another connection cannot see this uncommitted intent.
        delete.assert_not_awaited()
        await conn.rollback()
    assert not await runtime.db.one('SELECT run_id FROM run_cleanup WHERE run_id=?',(run['id'],))
    await runtime.db.execute('INSERT INTO run_cleanup(run_id,workspace_id,created_at) VALUES(?,?,?)',
                             (run['id'],'alice',now()))
    result=await runtime.cleanup_runs()
    assert result.live_run_deferred==1 and result.cleaned==result.failed==0
    delete.assert_not_awaited()
