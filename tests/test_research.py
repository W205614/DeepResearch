import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

from backend.core.db import uid
from backend.research.graph import ResearchGraph
from backend.domain.models import Route, RunRequest
from backend.core.observability import get_task_logger
from backend.infrastructure.providers import ServiceError
from backend.services.runtime import ConflictError, Runtime


async def finish(rt, run):
    task = rt.tasks.get(run["id"])
    if task:
        await task
    return await rt.db.owned_run(run["id"], run["user_id"])


async def test_complete_research_has_real_events_and_citations(runtime):
    result = await finish(runtime, await runtime.create("alice", RunRequest(topic="研究助手工作流程", client_request_id=uid())))
    assert result["status"] == "completed", result["error"]
    assert len(result["sources"]) == 2
    assert all(f"[{s['id']}]" in result["report"] for s in result["sources"])
    assert "测试模式" in result["report"]
    assert result["validation"]["supported_claims"] == 2
    assert result["validation"]["evidence_metrics"] == {
        "source_count": 2, "unique_web_domains": 1, "source_kinds": ["web"], "access_types": ["summary"],
        "evidence_levels": ["secondary"], "trusted_sources": 0}
    events = await runtime.db.rows("SELECT type,data FROM events WHERE run_id=?", (result["id"],))
    starts = [json.loads(e["data"])["node"] for e in events if e["type"] == "node_start"]
    assert {"web_scout", "local_scout", "judge", "reflect", "validator"} <= set(starts)
    assert starts.count("web_scout") == 2
    assert result["usage"]["search_calls"] == 2
    assert not await runtime.db.one("SELECT id FROM memories WHERE run_id=?", (result["id"],))
    await runtime.save_run_memory(result["id"], "alice")
    assert await runtime.db.one("SELECT id FROM memories WHERE run_id=?", (result["id"],))


async def test_task_logs_show_progress_without_topic_or_evidence(runtime, caplog):
    logger = get_task_logger()
    logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger="deepresearch.task")
    try:
        result = await finish(runtime, await runtime.create(
            "alice", RunRequest(topic="不应写入容器日志的研究主题", client_request_id=uid())))
    finally:
        logger.removeHandler(caplog.handler)
    assert result["status"] == "completed"
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "node=web_scout phase=start" in messages
    assert "component=web_search" in messages
    assert "phase=completed" in messages
    assert "不应写入容器日志的研究主题" not in messages


async def test_duplicate_request_is_idempotent(runtime):
    request = RunRequest(topic="研究助手工作流程", client_request_id=uid())
    first, second = await asyncio.gather(runtime.create("alice", request), runtime.create("alice", request))
    assert first["id"] == second["id"]
    assert len(await runtime.db.rows("SELECT id FROM runs")) == 1
    await finish(runtime, first)
    with pytest.raises(ConflictError):
        await runtime.create("alice", request.model_copy(update={"topic": "另一个主题"}))


async def test_failed_node_resumes_without_repeating_successful_searches(runtime):
    original = runtime.providers.structured
    fail = True
    async def flaky(role, *args, **kwargs):
        nonlocal fail
        if role == "analyst" and fail:
            fail = False
            raise ServiceError("测试：分析阶段临时失败")
        return await original(role, *args, **kwargs)
    runtime.providers.structured = flaky
    run = await runtime.create("alice", RunRequest(topic="研究流程", mode="quick", client_request_id=uid()))
    failed = await finish(runtime, run)
    assert failed["status"] == "failed"
    await asyncio.sleep(0)
    await runtime.resume(run["id"], "alice")
    result = await finish(runtime, run)
    assert result["status"] == "completed", result["error"]
    assert result["usage"]["search_calls"] == 1


async def test_cancel_and_thread_concurrency(runtime):
    original = runtime.providers.structured
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked(role, *args, **kwargs):
        if role == "planner":
            entered.set()
            await release.wait()
        return await original(role, *args, **kwargs)
    runtime.providers.structured = blocked
    run = await runtime.create("alice", RunRequest(topic="研究流程", client_request_id=uid()))
    await asyncio.wait_for(entered.wait(), 5)
    with pytest.raises(ConflictError):
        await runtime.create("alice", RunRequest(topic="另一个研究", thread_id=run["thread_id"], client_request_id=uid()))
    result = await runtime.cancel(run["id"], "alice")
    assert result["status"] == "cancelled"
    assert result["usage"].get("search_calls", 0) == 0
    await asyncio.sleep(0)
    release.set()
    await runtime.resume(run["id"], "alice")
    assert (await finish(runtime, run))["status"] == "completed"


async def test_missing_all_sources_produces_insufficient_report(runtime):
    async def empty(query, run_id):
        return []
    runtime.providers.search = empty
    result = await finish(runtime, await runtime.create("alice", RunRequest(topic="缺少所有资料的行业研究", client_request_id=uid())))
    assert result["status"] == "insufficient", result["error"]
    assert result["validation"]["supported_claims"] == 0
    assert not result["sources"]
    assert "资料不足" in result["report"]


async def test_production_excludes_unreadable_web_candidates(runtime):
    graph = ResearchGraph(runtime.settings.model_copy(update={"demo_mode": False}), runtime.db,
                          runtime.providers, runtime.vectors, runtime.documents)

    async def unreadable(_):
        raise ServiceError("来源正文不可访问")

    graph.read_web = unreadable
    result = await graph.web_scout({"run_id": "unreadable", "queries": ["不可访问候选来源"], "searched": []})
    assert not result["web_results"]
    warnings = await runtime.db.rows("SELECT data FROM events WHERE run_id=? AND type='warning'", ("unreadable",))
    assert any("已排除" in json.loads(row["data"])["message"] for row in warnings)


async def test_web_scout_rotates_candidates_across_queries(runtime):
    settings = runtime.settings.model_copy(update={"demo_mode": False, "max_web_candidates": 6, "web_results_per_query": 4})
    graph = ResearchGraph(settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)

    async def search(query, _run_id):
        return [{"url": f"https://{query}-{index}.example.com/page", "name": f"{query}-{index}", "summary": ""}
                for index in range(4)]

    async def read_web(_url):
        return "这是一段足以通过网页正文可读性检查的测试资料。" * 4

    runtime.providers.search = search
    graph.read_web = read_web
    result = await graph.web_scout({"run_id": "rotating-web", "queries": ["alpha", "beta"], "searched": []})
    titles = [row["title"] for row in result["web_results"]]
    assert len(titles) == 6
    assert {"alpha-0", "beta-0", "alpha-1", "beta-1"} <= set(titles)


async def test_budget_is_atomic_and_survives_resume(runtime):
    results = await asyncio.gather(*[runtime.db.reserve_search("budget", 3) for _ in range(12)])
    assert sum(results) == 3
    assert not await runtime.db.reserve_search("budget", 3)


async def test_greeting_does_not_search(runtime):
    result = await finish(runtime, await runtime.create("alice", RunRequest(topic="你好", client_request_id=uid())))
    assert result["status"] == "completed"
    assert result["validation"]["kind"] == "greeting"
    assert result["usage"].get("search_calls", 0) == 0


async def test_help_does_not_search(runtime):
    result = await finish(runtime, await runtime.create("alice", RunRequest(topic="你有什么功能？", client_request_id=uid())))
    assert result["status"] == "completed"
    assert result["validation"]["kind"] == "help"
    assert "本地资料库" in result["report"]
    assert result["usage"].get("search_calls", 0) == 0
    assert not result["sources"]


async def test_current_user_preference_is_local_and_does_not_search(runtime):
    await runtime.db.execute("""INSERT INTO memories(id,user_id,kind,content,run_id,created_at)
        VALUES('preference-query','alice','preference','优先关注中国市场与中文输出','pref','today')""")
    result = await finish(runtime, await runtime.create(
        "alice", RunRequest(topic="当前用户的研究偏好是什么？", client_request_id=uid())))
    assert result["status"] == "completed"
    assert result["validation"]["kind"] == "preference"
    assert "优先关注中国市场与中文输出" in result["report"]
    assert result["usage"].get("search_calls", 0) == 0
    assert result["usage"].get("llm_calls", 0) == 0
    assert not result["sources"]


async def test_llm_chat_route_is_upgraded_to_evidence_retrieval(runtime):
    original = runtime.providers.structured
    local_search = AsyncMock(return_value=[])

    async def chat_route(role, *args, **kwargs):
        if role == "router":
            return Route(mode="chat", reason="普通对话")
        return await original(role, *args, **kwargs)

    runtime.providers.structured = chat_route
    runtime.documents.search = local_search
    await runtime.db.execute("""INSERT INTO documents(id,user_id,name,hash,status,created_at)
        VALUES('local-ready','alice','local.md','local-hash','ready','today')""")
    result = await finish(runtime, await runtime.create("alice", RunRequest(
        topic="用一句话介绍你自己", client_request_id=uid())))
    assert result["status"] == "completed"
    assert result["validation"].get("kind") != "chat"
    assert result["usage"].get("search_calls", 0) >= 1
    assert result["sources"]
    local_search.assert_awaited_once()


async def test_user_profile_name_is_shared_between_threads(runtime):
    named = await finish(runtime, await runtime.create(
        "alice", RunRequest(topic="以后叫你小研", client_request_id=uid())))
    assert named["validation"]["kind"] == "profile"
    recalled = await finish(runtime, await runtime.create(
        "alice", RunRequest(topic="你叫什么名字？", client_request_id=uid())))
    assert recalled["thread_id"] != named["thread_id"]
    assert "小研" in recalled["report"]
    assert recalled["usage"].get("search_calls", 0) == 0
    profile = await runtime.db.one("SELECT content FROM memories WHERE user_id='alice' AND kind='profile'")
    assert profile["content"] == "助手名称：小研"


async def test_semantic_memory_requires_relevance_and_is_observable(runtime):
    thread = await runtime.new_thread("alice", "语义记忆会话")
    await runtime.db.execute("""INSERT INTO runs(id,user_id,thread_id,topic,mode,status,created_at,updated_at,client_request_id)
        VALUES('old-near','alice',?,'历史主题','auto','completed','today','today','old-near-request')""", (thread["id"],))
    await runtime.db.execute("""INSERT INTO runs(id,user_id,thread_id,topic,mode,status,created_at,updated_at,client_request_id)
        VALUES('old-far','alice','other-thread','另一历史主题','auto','completed','today','today','old-far-request')""")
    await runtime.db.execute("""INSERT INTO memories(id,user_id,kind,content,run_id,created_at)
        VALUES('near','alice','semantic','高度相关的历史研究','old-near','today')""")
    await runtime.db.execute("""INSERT INTO memories(id,user_id,kind,content,run_id,created_at)
        VALUES('far','alice','semantic','不相关的历史研究','old-far','today')""")

    async def embed(_):
        return [[0.1] * 64]

    async def search(*_args, **_kwargs):
        return [{"id": "near", "score": 0.9}, {"id": "far", "score": 0.1}]

    runtime.providers.embed = embed
    runtime.vectors.search = search
    context = await runtime.context({"id": "context-run", "user_id": "alice", "thread_id": thread["id"], "topic": "相关研究"})
    assert "高度相关的历史研究" in context
    assert "不相关的历史研究" not in context
    event = await runtime.db.one("SELECT data FROM events WHERE run_id='context-run' AND type='memory_context'")
    assert json.loads(event["data"])["semantic_memories"] == 1


async def test_context_keeps_thread_outline_beyond_recent_report_detail(runtime):
    runtime.settings.conversation_turn_limit = 12
    runtime.settings.conversation_recent_runs = 3
    thread = await runtime.new_thread("alice", "连续会话")
    for index in range(8):
        stamp = f"2026-01-01T00:00:{index:02d}+00:00"
        await runtime.db.execute("""INSERT INTO runs(id,user_id,thread_id,topic,mode,status,created_at,updated_at,report,client_request_id)
            VALUES(?,?,?,?,?,'completed',?,?,?,?)""",
            (f"history-{index}", "alice", thread["id"], f"历史问题 {index}", "auto", stamp, stamp,
             f"历史报告详情 {index}", uid()))
    context = await runtime.context({"id": "context-continuity", "user_id": "alice", "thread_id": thread["id"],
                                     "topic": "继续研究"})
    for index in range(8):
        assert f"历史问题 {index}" in context
    assert "历史报告详情 5" in context
    assert "历史报告详情 4" not in context
    event = await runtime.db.one("SELECT data FROM events WHERE run_id='context-continuity' AND type='memory_context'")
    assert json.loads(event["data"])["session_runs"] == 8
    assert json.loads(event["data"])["session_detail_runs"] == 3


async def test_restart_preserves_reports_and_preferences(settings):
    rt = await Runtime(settings).start()
    await rt.db.execute("INSERT INTO memories(id,user_id,kind,content,run_id,created_at) VALUES('pref','alice','preference','关注中国市场','pref','today')")
    result = await finish(rt, await rt.create("alice", RunRequest(topic="研究流程", client_request_id=uid())))
    await rt.close()
    rt2 = await Runtime(settings).start()
    try:
        saved = await rt2.db.owned_run(result["id"], "alice")
        assert saved["report"] == result["report"]
        assert "关注中国市场" in await rt2.context(saved)
        assert not await rt2.db.owned_run(result["id"], "bob")
    finally:
        await rt2.close()

async def test_queue_cancel_is_persisted_and_blocks_a_late_worker(settings):
    runtime = await Runtime(settings).start()
    runtime.queue = AsyncMock()
    aborted = []

    class FakeJob:
        def __init__(self, job_id, _queue):
            self.job_id = job_id

        async def abort(self):
            aborted.append(self.job_id)
            return True

    try:
        run = await runtime.create("alice", RunRequest(topic="queued cancellation", client_request_id=uid()))
        scheduled = runtime.queue.enqueue_job.call_args.kwargs
        assert scheduled["_job_id"] == runtime.run_job_id(run["id"], 0, next_attempt=True)

        with patch("backend.services.runtime.Job", FakeJob):
            cancelled = await runtime.cancel(run["id"], "alice")

        assert cancelled["status"] == "cancelled"
        assert aborted == [runtime.run_job_id(run["id"], 0, next_attempt=False)]
        await runtime.execute_run(run["id"])
        assert (await runtime.db.owned_run(run["id"], "alice"))["status"] == "cancelled"
    finally:
        await runtime.close()


async def test_recovery_reuses_a_queue_job_and_only_one_worker_claims(runtime):
    runtime.queue = AsyncMock()
    thread = await runtime.new_thread("alice", "queue recovery")
    await runtime.db.execute("""INSERT INTO runs(id,user_id,thread_id,topic,mode,status,created_at,updated_at,
        client_request_id) VALUES('queue-run','alice',?,'research workflow','quick','queued','today','today','queue-request')""",
                             (thread["id"],))

    await runtime.recover_queued_runs()
    await runtime.recover_queued_runs()
    job_ids = [call.kwargs["_job_id"] for call in runtime.queue.enqueue_job.call_args_list]
    assert job_ids == ["research:queue-run:1", "research:queue-run:1"]

    runtime.queue = None
    await asyncio.gather(runtime.execute_run("queue-run"), runtime.execute_run("queue-run"))
    run = await runtime.db.owned_run("queue-run", "alice")
    assert run["status"] == "completed"
    assert run["attempt_count"] == 1


async def test_stale_worker_run_is_interrupted_then_requeued(settings):
    runtime = await Runtime(settings).start()
    runtime.queue = AsyncMock()
    thread = await runtime.new_thread("alice", "stale worker")
    await runtime.db.execute("""INSERT INTO runs(id,user_id,thread_id,topic,mode,status,created_at,updated_at,
        client_request_id,attempt_count,last_attempt_at) VALUES('stale-run','alice',?,'research workflow','quick',
        'running','today','today','stale-request',1,'2000-01-01T00:00:00+00:00')""", (thread["id"],))
    try:
        await runtime.recover_stale_runs()
        run = await runtime.db.owned_run("stale-run", "alice")
        assert run["status"] == "interrupted"
        scheduled = runtime.queue.enqueue_job.call_args.kwargs
        assert scheduled["_job_id"] == "research:stale-run:2"
        assert scheduled["resume"] is True
    finally:
        await runtime.close()
