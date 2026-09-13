import asyncio
import io
import json
import sqlite3
import asyncpg
import statistics
import time
import zipfile
from contextlib import AsyncExitStack
from datetime import datetime, timezone

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.errors import UniqueViolation
from arq.jobs import Job

from ..core.config import Settings
from ..core.db import Database, now, uid
from ..core.postgres import PostgresDatabase
from .documents import Documents
from .attachments import Attachments
from ..research.graph import ResearchGraph
from ..core.observability import configure_task_logger, error_category, inject_trace_context, run_label
from ..core.metrics import DLQ_DEPTH, DLQ_EVENTS, FAILURES, QUEUE_DEPTH, RUNS, RUN_SECONDS, FALLBACKS
from ..infrastructure.providers import Providers, ServiceError
from ..infrastructure.vectors import VectorIndex
from ..core.reliability import Execution, execution, quality, ServiceError as ReliabilityError
from ..core.checkpoints import FencedCheckpointer
from ..core.consistency import affected
from ..research.source_validity import validate_sources

TERMINAL = {"completed", "insufficient", "failed", "cancelled", "interrupted"}
RUN_HEARTBEAT_SECONDS = 10
RUN_STALE_SECONDS = 45


class ConflictError(Exception):
    pass


class Runtime:
    def __init__(self, settings: Settings, *, worker_mode: bool = False, isolated_mode: bool = False):
        self.settings = settings
        if not settings.database_url and not settings.demo_mode and not isolated_mode:
            raise RuntimeError("DATABASE_URL is required outside isolated offline tests")
        settings.prepare()
        self.db = PostgresDatabase(settings.database_url) if settings.database_url else Database(settings.data_dir / "research.sqlite3")
        self.db.reliability_settings = settings
        self.providers = Providers(settings, self.db)
        self.vectors = VectorIndex(settings, self.db)
        self.documents = Documents(settings, self.db, self.providers, self.vectors)
        self.attachments = Attachments(self.db, settings, self.documents.store)
        self.attachment_cleanup_task = None
        self.logger = configure_task_logger(settings.task_log_level)
        self.stack = AsyncExitStack()
        self.tasks = {}
        self.lock = asyncio.Lock()
        self.upload_lock = asyncio.Lock()
        self.gate = asyncio.Semaphore(settings.max_concurrent_runs)
        self.stopping = False

        self.worker_mode = worker_mode
        self.queue = None
        self.recovery_task = None
    async def start(self):
        await self.db.init()
        try:
            await self.documents.start()
            await self.attachments.cleanup()
        except Exception:
            self.logger.warning("component=object_store phase=degraded_start")
        self.attachment_cleanup_task = asyncio.create_task(self.cleanup_attachments_loop())
        if self.settings.database_url:
            # Workers can resume a run started by another replica, so checkpoints
            # live in the durable PostgreSQL source of truth in enterprise mode.
            checkpoint_url = self.settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
            self.checkpointer = await self.stack.enter_async_context(
                AsyncPostgresSaver.from_conn_string(checkpoint_url))
            for attempt in range(3):
                try:
                    await self.checkpointer.setup()
                    break
                except UniqueViolation:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.5 * (attempt + 1))
        else:
            # Explicit offline fixtures retain the small SQLite implementation only.
            self.checkpointer = await self.stack.enter_async_context(
                AsyncSqliteSaver.from_conn_string(str(self.settings.data_dir / "checkpoints.sqlite3")))
            await self.checkpointer.setup()
        self.checkpointer = FencedCheckpointer(self.checkpointer)
        self.graph = ResearchGraph(self.settings, self.db, self.providers, self.vectors, self.documents).build(self.checkpointer)
        if not self.worker_mode:
            if self.settings.queue_backend == "redis":
                await self.connect_queue()
                await self.recover_queued_runs()
                self.recovery_task = asyncio.create_task(self.recover_stale_runs_loop(), name="run-recovery")
            await self.recover_indexing_documents()
        return self

    async def connect_queue(self):
        if self.queue is None:
            from arq import create_pool
            from arq.connections import RedisSettings
            config = RedisSettings.from_dsn(self.settings.redis_url)
            config.conn_retries = 0
            try:
                self.queue = await create_pool(config)
            except Exception:
                self.logger.warning("component=queue phase=degraded_start")

    async def cleanup_attachments_loop(self):
        while True:
            await asyncio.sleep(300)
            try:
                await self.attachments.cleanup()
            except Exception:
                self.logger.warning("component=attachment_cleanup phase=error")

    async def close(self):
        self.stopping = True
        if self.attachment_cleanup_task:
            self.attachment_cleanup_task.cancel()
            await asyncio.gather(self.attachment_cleanup_task, return_exceptions=True)
        if self.recovery_task:
            self.recovery_task.cancel()
            await asyncio.gather(self.recovery_task, return_exceptions=True)
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.documents.close()
        await self.stack.aclose()
        await self.providers.close()
        await self.vectors.close()

        if hasattr(self.db, "close"):
            await self.db.close()
        if self.queue:
            await self.queue.aclose()
    async def schedule_document(self, document_id: str) -> None:
        key = f"document:{document_id}"
        if self.settings.queue_backend == "redis" and not self.queue:
            return
        if self.queue:
            document = await self.db.one("SELECT index_version FROM documents WHERE id=?", (document_id,))
            if document:
                try:
                    await self.queue.enqueue_job("ingest_document", document_id, trace_context=inject_trace_context(),
                                                 _job_id=f"{key}:{document['index_version']}", _queue_name="arq:documents")
                except Exception:
                    self.logger.warning("phase=document_delivery_pending")
            return
        if key in self.tasks:
            return
        task = asyncio.create_task(self.documents.ingest(document_id), name=key)
        self.tasks[key] = task
        task.add_done_callback(lambda _: self.tasks.pop(key, None))

    async def upload_document(self, user: str, name: str, content: bytes) -> dict:
        document = await self.documents.add(user, name, content)
        if document["status"] == "indexing":
            await self.schedule_document(document["id"])
        return document

    async def reindex_document(self, document_id: str, user: str) -> dict:
        document = await self.documents.reindex(document_id, user)
        await self.schedule_document(document["id"])
        return document

    async def find_thread(self, thread_ref: str, user: str):
        return await self.db.one(
            "SELECT * FROM threads WHERE user_id=? AND (id=? OR thread_key=?)", (user, thread_ref, thread_ref))

    async def new_thread(self, user, title):
        index = 1
        while await self.db.one("SELECT id FROM threads WHERE user_id=? AND thread_key=?", (user, f"thread{index:02d}")):
            index += 1
        thread = {"id": uid(), "user_id": user, "title": title[:100], "thread_key": f"thread{index:02d}", "created_at": now()}
        await self.db.execute("INSERT INTO threads(id,user_id,title,thread_key,created_at) VALUES(?,?,?,?,?)", tuple(thread.values()))
        return thread

    async def delete_thread(self, thread_ref: str, user: str):
        """Remove one conversation and every record created for its runs."""
        async with self.lock:
            thread = await self.find_thread(thread_ref, user)
            if not thread:
                raise LookupError("会话不存在")
            thread_id = thread["id"]
            runs = await self.db.rows("SELECT id,status FROM runs WHERE thread_id=? AND user_id=?", (thread_id, user))
            active_tasks = []
            for run in runs:
                if run["status"] in {"queued", "running"}:
                    task = self.tasks.get(run["id"])
                    if task:
                        task.cancel()
                        active_tasks.append(task)
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)

            run_ids = [run["id"] for run in runs]
            await self.attachments.delete_runs(run_ids)
            for run_id in run_ids:
                await self.checkpointer.adelete_thread(run_id)
                await self.db.execute("DELETE FROM events WHERE run_id=?", (run_id,))
                await self.db.execute("DELETE FROM counters WHERE run_id=?", (run_id,))
                await self.db.execute("DELETE FROM search_cache WHERE run_id=?", (run_id,))
                await self.db.execute("DELETE FROM verified_claims WHERE run_id=?", (run_id,))
                await self.db.execute("DELETE FROM call_attempts WHERE run_id=?", (run_id,))
            await self.db.execute("DELETE FROM runs WHERE thread_id=? AND user_id=?", (thread_id, user))
            await self.db.execute("DELETE FROM threads WHERE id=? AND user_id=?", (thread_id, user))
        await self.attachments.cleanup()
        return {"ok": True, "thread_id": thread["thread_key"], "deleted_runs": len(run_ids)}

    async def create(self, user, request, actor_subject: str | None = None):
        actor_subject = actor_subject or user
        async with self.lock, self.db.guard("admission"), self.db.guard("workspace:" + user):
            previous = await self.db.one("SELECT id,topic,mode,thread_id,data_policy FROM runs WHERE user_id=? AND client_request_id=?",
                                         (user, request.client_request_id))
            if previous:
                thread = await self.find_thread(request.thread_id, user) if request.thread_id else None
                attached = await self.attachments.for_run(previous["id"])
                if ([item["id"] for item in attached] != request.attachment_ids
                        or previous["topic"] != request.topic or previous["mode"] != request.mode or previous["data_policy"] != request.data_policy
                        or (request.thread_id and (not thread or thread["id"] != previous["thread_id"]))):
                    raise ConflictError("同一个请求编号不能提交不同内容")
                return await self.db.owned_run(previous["id"], user)
            if request.data_policy == "restricted" or (request.data_policy == "internal" and
                    not self.settings.demo_mode and not self.settings.allow_internal_model_processing):
                raise ReliabilityError("内部资料尚未获准由已配置模型处理；敏感资料禁止外发", code="egress_denied", retryable=False)
            if not self.settings.demo_mode:
                from ..core.reliability import fingerprint
                from ..core.config import endpoint
                await self.providers.provider_available(fingerprint([endpoint(self.settings.llm_base_url, "chat/completions"),
                    self.settings.llm_api_key.get_secret_value(), self.settings.llm_model_id]))
            waiting = await self.db.one("SELECT COUNT(*) AS total FROM runs WHERE status IN ('queued','interrupted')")
            if waiting["total"] >= self.settings.max_queued_runs:
                raise ReliabilityError("待处理任务已满，请稍后提交", code="queue_full")
            await self.attachments.staged(request.attachment_ids, user, actor_subject)
            limits = await self.db.workspace_limits(user)
            if limits:
                active = await self.db.one("SELECT COUNT(*) AS total FROM runs WHERE user_id=? AND status IN ('queued','running')", (user,))
                if int(active["total"]) >= int(limits["concurrent_run_limit"]):
                    raise ConflictError("工作空间正在执行的研究已达到并发上限")
            thread_id = request.thread_id
            if thread_id:
                thread = await self.find_thread(thread_id, user)
                if not thread:
                    raise LookupError("会话不存在")
                thread_id = thread["id"]
            else:
                thread_id = (await self.new_thread(user, request.topic))["id"]
            run_id, stamp = uid(), now()
            try:
                async with self.db.connection() as conn:
                    await conn.execute("""INSERT INTO runs(
                        id,user_id,thread_id,topic,mode,status,created_at,updated_at,client_request_id,created_by,data_policy,deadline_at)
                        VALUES(?,?,?,?,?,'queued',?,?,?,?,?,?)""", (run_id, user, thread_id, request.topic, request.mode,
                                                            stamp, stamp, request.client_request_id, actor_subject, request.data_policy,
                                                            time.time() + self.settings.max_run_total_seconds))
                    for position, image_id in enumerate(request.attachment_ids):
                        await conn.execute("UPDATE attachments SET run_id=?,position=? WHERE id=?", (run_id, position, image_id))
                    await conn.commit()
            except (sqlite3.IntegrityError, asyncpg.UniqueViolationError):
                raise ConflictError("同一会话已有正在执行的研究") from None
            await self.db.event(run_id, "queued", {"message": "研究任务已创建"})
            await self.refresh_queue_depth()
            await self.schedule(run_id)
            return await self.db.owned_run(run_id, user)

    async def schedule(self, run_id, resume=False):
        self.logger.info("run=%s phase=scheduled resume=%s", run_label(run_id), resume)
        if self.settings.queue_backend == "redis" and not self.queue:
            return
        if self.queue:
            run = await self.db.one("SELECT attempt_count FROM runs WHERE id=?", (run_id,))
            if not run:
                return
            try:
                await self.queue.enqueue_job("run_research", run_id, resume=resume, trace_context=inject_trace_context(),
                                             _job_id=self.run_job_id(run_id, int(run.get("attempt_count") or 0), next_attempt=True))
            except Exception:
                self.logger.warning("run=%s phase=delivery_pending", run_label(run_id))
            return
        task = asyncio.create_task(self.execute_run(run_id, resume), name=f"research-{run_id}")
        self.tasks[run_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(run_id, None))

    @staticmethod
    def run_job_id(run_id: str, attempt_count: int, *, next_attempt: bool) -> str:
        """Use one queue job per persisted attempt, so recovery is idempotent."""
        number = attempt_count + 1 if next_attempt else attempt_count
        return f"research:{run_id}:{max(1, number)}"

    async def context(self, run):
        if run.get("data_policy") == "public":
            # Public search must never inherit private thread reports or memories.
            return ""
        owner_subject = run.get("created_by") or run["user_id"]
        preferences = await self.db.rows("""SELECT content FROM memories
            WHERE owner_subject=? AND kind='preference' ORDER BY created_at DESC LIMIT 20""", (owner_subject,))
        profile = await self.db.one("SELECT content FROM memories WHERE owner_subject=? AND kind='profile'",
                                    (owner_subject,))
        history_params = (run["thread_id"], run["user_id"], run["id"])
        turn_index = await self.db.rows("""SELECT topic,status FROM runs WHERE thread_id=? AND user_id=?
            AND id!=? AND status IN ('completed','insufficient') ORDER BY created_at DESC LIMIT ?""",
            history_params + (self.settings.conversation_turn_limit,))
        detailed_history = await self.db.rows("""SELECT topic,report,status FROM runs WHERE thread_id=? AND user_id=?
            AND id!=? AND status IN ('completed','insufficient') ORDER BY created_at DESC LIMIT ?""",
            history_params + (self.settings.conversation_recent_runs,))
        summaries, seen = [], set()
        if await self.db.one("SELECT id FROM memories WHERE user_id=? AND kind='semantic' LIMIT 1", (run["user_id"],)):
            try:
                vector = (await self.providers.embed([run["topic"]]))[0]
                hits = await self.vectors.search("memories", run["user_id"], vector, limit=3, thread_id=run["thread_id"])
                for hit in hits:
                    try:
                        score = float(hit.get("score", -1))
                    except (TypeError, ValueError):
                        continue
                    if score < self.settings.semantic_memory_min_score:
                        continue
                    memory = await self.db.one("""SELECT m.content FROM memories m JOIN runs r ON r.id=m.run_id
                        WHERE m.id=? AND m.user_id=? AND m.kind='semantic' AND r.thread_id=?""",
                        (hit["id"], run["user_id"], run["thread_id"]))
                    if memory and memory["content"] not in seen:
                        seen.add(memory["content"])
                        summaries.append({"content": memory["content"], "similarity": round(score, 3)})
            except ServiceError as exc:
                await self.db.event(run["id"], "warning", {"message": str(exc)})
        await self.db.event(run["id"], "memory_context", {"preferences": len(preferences),
                            "session_runs": len(turn_index), "session_detail_runs": len(detailed_history),
                            "semantic_memories": len(summaries),
                            "semantic_min_score": self.settings.semantic_memory_min_score})
        return json.dumps({"当前用户个人设定": profile["content"] if profile else "",
            "用户明确保存的偏好": [p["content"] for p in preferences],
            "会话问题索引_仅用于延续对话_不是新事实证据": [
                {"topic": h["topic"][:160], "status": h["status"]} for h in reversed(turn_index)],
            "最近会话记录_仅用于延续对话_不是新事实证据": [
                {"topic": h["topic"], "report": h["report"][:1400], "status": h["status"]}
                for h in reversed(detailed_history)],
            "相关语义记忆_仅供理解上下文_必须重新核查": summaries}, ensure_ascii=False)[:14000]

    async def save_summary(self, run, report):
        summary = f"主题：{run['topic']}\n历史研究摘要（需重新核查）：\n{report[:2000]}"
        memory_id = "s" + run["id"]
        try:
            vector = (await self.providers.embed([summary]))[0]
            await self.vectors.upsert("memories", [{"id": memory_id, "user_id": run["user_id"], "thread_id": run["thread_id"],
                "document_id": "", "locator": "历史研究摘要", "title": run["topic"][:160],
                "text": summary, "vector": vector}])
            await self.db.execute("""INSERT OR REPLACE INTO memories(id,user_id,kind,content,run_id,created_at,vector)
                VALUES(?,?,'semantic',?,?,?,?)""", (memory_id, run["user_id"], summary, run["id"], now(),
                                                  json.dumps(vector) if self.settings.demo_mode else "[]"))
        except ServiceError as exc:
            await self.db.event(run["id"], "warning", {"message": "报告已生成，语义记忆保存失败：" + str(exc)})

    async def execute_run(self, run_id, resume=False):
        started = time.monotonic()
        heartbeat = None
        attempt = None
        context_token = None
        try:
            async with self.gate:
                eligible = "('queued','interrupted')" if resume else "('queued')"
                async with self.db.guard("execution:" + run_id):
                    prior = await self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
                    if not prior:
                        return
                    if prior["deadline_at"] and prior["deadline_at"] <= time.time():
                        await self.exhaust(run_id)
                        return
                    claimed = await self.db.execute(f"""UPDATE runs SET status='running',updated_at=?,error='',error_info='{{}}',
                        deadline_at=CASE WHEN deadline_at=0 THEN ? ELSE deadline_at END,
                        attempt_count=attempt_count+1,last_attempt_at=? WHERE id=? AND status IN {eligible}""",
                        (now(), time.time() + self.settings.max_run_total_seconds, now(), run_id))
                if not claimed:
                    return
                run = await self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
                self.logger.info("run=%s phase=started mode=%s resumed=%s", run_label(run_id), run["mode"], resume)
                attempt = run["attempt_count"]
                context_token = execution.set(Execution(self.db, self.settings, run_id, attempt))
                heartbeat = asyncio.create_task(self.heartbeat(run_id, attempt, asyncio.current_task()), name=f"heartbeat-{run_id}")
                await self.refresh_queue_depth()
                await self.db.event(run_id, "running", {"resumed": resume})
                config = {"configurable": {"thread_id": run_id}, "recursion_limit": 80}
                async with asyncio.timeout(min(self.settings.max_run_seconds, max(0.01, run["deadline_at"] - time.time()))):
                    initial = None
                    checkpoint = await self.graph.aget_state(config) if resume else None
                    if not resume or not checkpoint.values:
                        initial = {"run_id": run_id, "user_id": run["user_id"], "thread_id": run["thread_id"],
                                   "owner_subject": run.get("created_by") or run["user_id"],
                                   "topic": run["topic"], "requested_mode": run["mode"],
                                   "attachment_ids": [item["id"] for item in await self.attachments.for_run(run_id)],
                                   "context": await self.context(run)}
                    result = await self.graph.ainvoke(initial, config)
                    await execution.get().check()
                    if not self.settings.demo_mode:
                        await validate_sources(self.db, run["user_id"], result.get("evidence", []))
                    report = result.get("report", "")
                    validation = result.get("validation", {})
                    validation["quality"] = quality(validation)
                    from ..core.metrics import RESULT_QUALITY
                    outcomes = result.get("web_outcomes", []) + result.get("local_outcomes", [])
                    reasons = list(validation.get("reasons", []))
                    reasons += sorted({item["outcome"] for item in outcomes if item["outcome"] in {"empty", "error", "search_limit_reached", "invalid_records", "vector_unavailable"}})
                    if validation.get("insufficient"):
                        reasons.append("insufficient_evidence")
                    validation["reasons"] = list(dict.fromkeys(reasons))
                    validation["retrieval_outcomes"] = outcomes
                    status = "failed" if validation.get("retrieval_failed") else "insufficient" if validation.get("insufficient") else "completed"
                    messages = {"invalid_records": "部分检索字段无效，已剔除并使用有效证据", "vector_unavailable": "向量检索不可用，已降级为关键词检索", "empty": "部分检索未找到结果", "error": "部分检索服务发生故障", "search_limit_reached": "本轮研究已达到检索上限，可继续发起研究",
                                "insufficient_evidence": "证据不足，请补充资料或调整问题", "image_unreadable": "图片无法清晰识别，请补充清晰图片"}
                    if reasons:
                        report += "\n\n> " + "；".join(messages[key] for key in validation["reasons"] if key in messages)
                    async with self.db.guard("workspace:" + run["user_id"]), self.db.guard("documents:" + run["user_id"]), self.db.guard("execution:" + run_id):
                        await execution.get().check()
                        if not self.settings.demo_mode:
                            await validate_sources(self.db, run["user_id"], result.get("evidence", []))
                        async with self.db.connection() as conn:
                            completed = await conn.execute("""UPDATE runs SET status=?,updated_at=?,report=?,sources=?,validation=?,error=?
                            WHERE id=? AND status='running' AND attempt_count=?""", (status, now(), report,
                                                                  json.dumps(result.get("evidence", []), ensure_ascii=False),
                                                                  json.dumps(validation, ensure_ascii=False), "检索服务暂不可用，请重试或检查服务配置" if status == "failed" else "", run_id, attempt))
                            if affected(completed):
                                await conn.execute("INSERT INTO events(run_id,type,data,created_at) VALUES(?,?,?,?)",
                                                   (run_id, "done", json.dumps({"status": status}), now()))
                            await conn.commit()
                    if not affected(completed):
                        return
                    RESULT_QUALITY.labels(quality=validation["quality"]).inc()
                    for reason in validation["reasons"]:
                        FALLBACKS.labels(reason=reason).inc()
                    await self.db.event(run_id, "result_status", {"status": status, "reasons": validation["reasons"]})
                    if (self.settings.auto_save_semantic_memory and report and not validation.get("insufficient")
                            and validation.get("kind") not in {"greeting", "help", "preference", "chat", "vision"}):
                        await self.save_summary(run, report)
                    dead_letter = await self.db.one(
                        "SELECT category FROM dead_letter_runs WHERE run_id=?", (run_id,))
                    recovered = await self.db.execute(
                        "UPDATE dead_letter_runs SET recovered_at=? WHERE run_id=?", (now(), run_id))
                    if recovered:
                        DLQ_EVENTS.labels(action="recovered", category=dead_letter["category"]).inc()
                        await self.refresh_dead_letter_depth()
                    usage = await self.db.one("SELECT llm_calls,search_calls FROM counters WHERE run_id=?", (run_id,)) or {}
                    RUNS.labels(status=status).inc()
                    RUN_SECONDS.observe(time.monotonic() - started)
                    self.logger.info("run=%s phase=completed status=%s sources=%d llm_calls=%d search_calls=%d",
                                     run_label(run_id), status, len(result.get("evidence", [])),
                                     usage.get("llm_calls", 0), usage.get("search_calls", 0))
        except asyncio.CancelledError:
            current = await self.db.one("SELECT status FROM runs WHERE id=?", (run_id,))
            if current and current["status"] == "cancelled":
                self.logger.warning("run=%s phase=cancelled status=cancelled", run_label(run_id))
            else:
                status = "interrupted" if self.stopping else "cancelled"
                if await self.db.execute("UPDATE runs SET status=?,updated_at=? WHERE id=? AND status='running' AND attempt_count=?",
                                         (status, now(), run_id, attempt)):
                    await self.db.event(run_id, status, {"message": "任务已停止，可从检查点继续"})
                self.logger.warning("run=%s phase=cancelled status=%s", run_label(run_id), status)
        except TimeoutError:
            self.logger.error("run=%s phase=failed category=timeout", run_label(run_id))
            await self.fail(run_id, "本次执行达到时限；已停止后续调用，上游请求结果可能未知", "interrupted", "timeout", attempt=attempt)
        except Exception as exc:
            FAILURES.labels(category=error_category(exc)).inc()
            RUNS.labels(status="failed").inc()
            RUN_SECONDS.observe(time.monotonic() - started)
            self.logger.error("run=%s phase=failed category=%s", run_label(run_id), error_category(exc))
            if not isinstance(exc, ReliabilityError) or exc.code != "execution_lost":
                await self.fail(run_id, str(exc) if isinstance(exc, ServiceError) else "研究执行失败，请检查服务配置或重试", category=error_category(exc), attempt=attempt,
                                detail=exc.detail() if isinstance(exc, ReliabilityError) else None)
        finally:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            if context_token is not None:
                execution.reset(context_token)

    async def fail(self, run_id, message, status="failed", category="runtime_failure", attempt=None, detail=None):
        detail = detail or {"code": category, "retryable": True, "action": "恢复后继续", "result_unknown": category == "timeout"}
        report, sources = "", []
        if detail.get("code") not in {"permission_revoked", "source_changed", "egress_denied", "execution_lost"}:
            report, sources = await self.partial_report(run_id)
        validation = {"quality": "partial" if sources else "unverified", "verification_pending": True,
                      "kind": "research", "reasons": [detail["code"]]}
        async with self.db.connection() as conn:
            changed = await conn.execute("UPDATE runs SET status=?,error=?,updated_at=?,error_info=?,report=?,sources=?,validation=? WHERE id=? AND status='running'"
                                        + (" AND attempt_count=?" if attempt is not None else ""),
                                        (status, message, now(), json.dumps(detail, ensure_ascii=False), report,
                                         json.dumps(sources, ensure_ascii=False), json.dumps(validation, ensure_ascii=False), run_id) + ((attempt,) if attempt is not None else ()))
            if affected(changed):
                await conn.execute("INSERT INTO events(run_id,type,data,created_at) VALUES(?,?,?,?)",
                                   (run_id, "error", json.dumps({"message": message, "status": status}), now()))
            await conn.commit()
        changed = affected(changed)
        if changed:
            if status == "failed":
                run = await self.db.one("SELECT user_id FROM runs WHERE id=?", (run_id,))
                await self.db.execute("DELETE FROM dead_letter_runs WHERE run_id=?", (run_id,))
                await self.db.execute("INSERT INTO dead_letter_runs(run_id,user_id,category,message,failed_at,recovered_at) VALUES(?,?,?,?,?,'' )", (run_id, run["user_id"], category, message[:240], now()))
                DLQ_EVENTS.labels(action="created", category=category).inc()
                await self.refresh_dead_letter_depth()
        await self.refresh_queue_depth()

    async def partial_report(self, run_id):
        from ..research.graph import supported_indices, md_text
        from ..domain.models import Verification
        run = await self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
        if not run:
            return "", []
        claims, sources = {}, {}
        for row in await self.db.rows("SELECT result FROM verified_claims WHERE run_id=?", (run_id,)):
            data = json.loads(row["result"])
            verdict = Verification.model_validate(data["verdict"])
            inputs = {item["index"]: item for item in data["input"]["claims"]}
            approved = supported_indices(verdict, set(inputs))
            for index in approved:
                claim = inputs[index]
                cited = [data["input"]["sources"][key] for key in claim["source_ids"]]
                try:
                    if not self.settings.demo_mode:
                        await validate_sources(self.db, run["user_id"], cited)
                except ServiceError:
                    continue
                claims[claim["text"]] = claim["source_ids"]
                sources.update({item["id"]: item for item in cited})
        if not claims:
            return "", []
        report = "# 已核验的部分结果\n\n其余内容尚未完成核验；以下不构成完整研究报告。\n\n"
        report += "\n\n".join(md_text(text) + " " + " ".join(f"[{key}]" for key in ids) for text, ids in claims.items())
        return report, list(sources.values())

    async def dead_letters(self, user):
        rows = await self.db.rows("""SELECT d.run_id,d.category,d.message,d.failed_at,d.recovered_at,r.status
            FROM dead_letter_runs d JOIN runs r ON r.id=d.run_id AND r.user_id=d.user_id
            WHERE d.user_id=? ORDER BY d.failed_at DESC""", (user,))
        for row in rows:
            current = await self.db.owned_run(row["run_id"], user)
            row["can_recover"] = current["can_resume"]
            row["recovery_status"] = (
                "succeeded" if row["status"] == "completed" else
                "insufficient" if row["status"] == "insufficient" else
                "retrying" if row["status"] in {"queued", "running"} else "pending")
        return rows

    async def recover_dead_letter(self, run_id, user):
        row = await self.db.one("SELECT run_id FROM dead_letter_runs WHERE run_id=? AND user_id=?", (run_id, user))
        if not row:
            raise LookupError("死信任务不存在或已恢复")
        return await self.resume(run_id, user)

    async def refresh_dead_letter_depth(self):
        row = await self.db.one("""SELECT COUNT(*) AS total FROM dead_letter_runs d
            JOIN runs r ON r.id=d.run_id AND r.user_id=d.user_id
            WHERE r.status IN ('failed','cancelled','interrupted')""")
        DLQ_DEPTH.set(int((row or {}).get("total", 0)))

    async def cancel(self, run_id, user):
        async with self.lock, self.db.guard("execution:" + run_id):
            run = await self.db.owned_run(run_id, user)
            if not run:
                raise LookupError("任务不存在")
            changed = await self.db.execute("""UPDATE runs SET status='cancelled',updated_at=? WHERE id=? AND user_id=?
                                            AND status IN ('queued','running','interrupted')""", (now(), run_id, user))
            if changed:
                await self.db.event(run_id, "cancelled", {"message": "任务已停止"})
                await self.refresh_queue_depth()
        task = self.tasks.get(run_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if changed and self.queue:
            try:
                await asyncio.wait_for(Job(self.run_job_id(run_id, int(run.get("attempt_count") or 0), next_attempt=False), self.queue).abort(), 1)
            except Exception:
                # The persisted cancellation state still prevents a late worker from claiming this run.
                pass
        return await self.db.owned_run(run_id, user)

    async def resume(self, run_id, user):
        async with self.lock, self.db.guard("workspace:" + user):
            run = await self.db.owned_run(run_id, user)
            if not run:
                raise LookupError("任务不存在")
            if run["status"] not in {"cancelled", "interrupted", "failed"}:
                raise ConflictError("当前任务状态不能继续；需要重新研究时请提交新任务")
            if not run.get("can_resume", False) or run["call_attempts"] >= self.settings.max_run_call_attempts or run["reserved_tokens"] >= self.settings.max_run_reserved_tokens:
                raise ReliabilityError("该任务不可继续；请处理错误原因或缩小问题重新提交", code="resume_denied", retryable=False)
            if run_id in self.tasks:
                raise ConflictError("任务尚未停止")
            if run.get("validation", {}).get("retrieval_failed"):
                # A terminal graph has no pending search node. Restart its graph,
                # retaining the durable visual result and consumed quota.
                await self.checkpointer.adelete_thread(run_id)
            limits = await self.db.workspace_limits(user)
            active = await self.db.one("SELECT COUNT(*) AS total FROM runs WHERE user_id=? AND status IN ('queued','running')", (user,))
            if limits and active["total"] >= limits["concurrent_run_limit"]:
                raise ConflictError("工作空间正在执行的研究已达到并发上限")
            try:
                await self.db.execute("UPDATE runs SET status='queued',updated_at=? WHERE id=?", (now(), run_id))
            except (sqlite3.IntegrityError, asyncpg.UniqueViolationError):
                raise ConflictError("当前会话已有其他研究任务") from None
            await self.refresh_queue_depth()
            await self.schedule(run_id, resume=True)
            await self.refresh_dead_letter_depth()
            return await self.db.owned_run(run_id, user)

    async def status(self):
        vector_error = ""
        try:
            await self.vectors.ping()
        except ServiceError as exc:
            vector_error = str(exc)
        return {"mode": "demo" if self.settings.demo_mode else "live", "missing": self.settings.missing(),
                "milvus": "unavailable" if vector_error else "ready", "milvus_message": vector_error,
                "llm_model": self.settings.llm_model_id, "embedding_model": self.settings.embedding_model,
                "embedding_dimension": self.settings.embedding_dimension,
                "web_search_provider": self.settings.web_search_provider,
                "identity_mode": self.settings.auth_mode, "max_reflection_rounds": self.settings.max_reflection_rounds,
                "max_search_calls": self.settings.max_search_calls,
                "web_results_per_query": self.settings.web_results_per_query,
                "max_web_candidates": self.settings.max_web_candidates}

    async def recover_queued_runs(self):
        """Re-enqueue persisted work after an API restart."""
        rows = await self.db.rows("SELECT * FROM runs WHERE status IN ('queued','interrupted')")
        for row in rows:
            if row["id"] not in self.tasks:
                if row["deadline_at"] and row["deadline_at"] <= time.time():
                    await self.exhaust(row["id"])
                    continue
                if row["status"] == "interrupted":
                    if row["auto_recoveries"] >= self.settings.max_job_retries:
                        await self.exhaust(row["id"])
                        continue
                    changed = await self.db.execute("""UPDATE runs SET status='queued',auto_recoveries=auto_recoveries+1
                        WHERE id=? AND status='interrupted' AND auto_recoveries=?""", (row["id"], row["auto_recoveries"]))
                    if not changed:
                        continue
                await self.schedule(row["id"], resume=row["status"] == "interrupted" or row["attempt_count"] > 0)

    async def exhaust(self, run_id):
        detail = json.dumps({"code": "budget_exhausted", "retryable": False, "action": "缩小问题后创建新任务"}, ensure_ascii=False)
        async with self.db.connection() as conn:
            result = await conn.execute("""UPDATE runs SET status='failed',error=?,error_info=?,updated_at=?
                WHERE id=? AND status IN ('queued','interrupted')""",
                ("累计时限或自动恢复次数已耗尽，已停止自动恢复", detail, now(), run_id))
            if affected(result):
                await conn.execute("INSERT INTO events(run_id,type,data,created_at) VALUES(?,?,?,?)",
                                   (run_id, "done", json.dumps({"status": "failed", "code": "budget_exhausted"}), now()))
            await conn.commit()

    async def heartbeat(self, run_id: str, attempt: int, owner=None) -> None:
        while True:
            await asyncio.sleep(RUN_HEARTBEAT_SECONDS)
            try:
                await Execution(self.db, self.settings, run_id, attempt).check()
                changed = await self.db.execute("UPDATE runs SET last_attempt_at=?,updated_at=? WHERE id=? AND status='running' AND attempt_count=?",
                                                (now(), now(), run_id, attempt))
                if not changed and owner:
                    owner.cancel()
                    return
            except ReliabilityError as exc:
                if exc.code != "execution_lost":
                    await self.fail(run_id, str(exc), attempt=attempt, detail=exc.detail())
                if owner:
                    owner.cancel()
                return
            except Exception:
                if owner:
                    owner.cancel()
                return

    async def recover_stale_runs_loop(self) -> None:
        while not self.stopping:
            await asyncio.sleep(RUN_HEARTBEAT_SECONDS)
            try:
                if self.settings.queue_backend == "redis":
                    await self.connect_queue()
                await self.recover_stale_runs()
                await self.recover_queued_runs()
                await self.recover_indexing_documents()
                await self.documents.cleanup_pending()
                await self.refresh_queue_depth()
            except Exception:
                self.logger.warning("phase=recovery_pending")

    async def recover_stale_runs(self) -> None:
        """Resume only workers whose heartbeat has actually expired."""
        cutoff = time.time() - RUN_STALE_SECONDS
        rows = await self.db.rows("SELECT id,last_attempt_at FROM runs WHERE status='running'")
        for row in rows:
            try:
                heartbeat_at = datetime.fromisoformat(row["last_attempt_at"]).astimezone(timezone.utc).timestamp()
            except (TypeError, ValueError):
                heartbeat_at = 0
            if heartbeat_at >= cutoff:
                continue
            async with self.db.guard("execution:" + row["id"]):
                changed = await self.db.execute("""UPDATE runs SET status='interrupted',updated_at=? WHERE id=?
                                                AND status='running' AND last_attempt_at=?""",
                                                (now(), row["id"], row["last_attempt_at"]))
            if changed:
                await self.db.event(row["id"], "interrupted", {"message": "Worker 心跳超时，正在从检查点恢复"})
                await self.recover_queued_runs()

    async def recover_indexing_documents(self):
        """Resume persisted, clean documents after an API restart."""
        rows = await self.db.rows("SELECT id FROM documents WHERE status IN ('indexing','rebuilding')")
        for row in rows:
            await self.schedule_document(row["id"])

    async def refresh_queue_depth(self) -> None:
        row = await self.db.one("SELECT COUNT(*) AS total FROM runs WHERE status='queued'")
        QUEUE_DEPTH.set(int((row or {}).get("total", 0)))
        from ..core.metrics import QUEUE_AGE, DISK_FREE, HEARTBEAT_LAG, PROVIDER_BLOCKED
        import shutil
        queued = await self.db.one("SELECT MIN(created_at) AS stamp FROM runs WHERE status='queued'")
        running = await self.db.one("SELECT MIN(last_attempt_at) AS stamp FROM runs WHERE status='running'")
        def age(value):
            try:
                return max(0, time.time() - datetime.fromisoformat(value).timestamp()) if value else 0
            except (TypeError, ValueError):
                return 0
        QUEUE_AGE.set(age(queued["stamp"]))
        HEARTBEAT_LAG.set(age(running["stamp"]))
        DISK_FREE.set(shutil.disk_usage(self.settings.data_dir).free)
        blocked = await self.db.one("SELECT COUNT(*) AS total FROM provider_health WHERE blocked=1 OR open_until>?", (time.time(),))
        PROVIDER_BLOCKED.set(blocked["total"])

    async def ready(self):
        try:
            async with asyncio.timeout(3):
                await self.db.one("SELECT 1 AS ready")
        except Exception as exc:
            raise ServiceError("核心数据库未就绪") from exc

    async def capabilities(self):
        result = {"read_history": True, "submit_research": True, "vector_search": True,
                  "upload_documents": True, "reasons": []}
        try:
            await self.ready()
        except ServiceError:
            return {key: False for key in result if key != "reasons"} | {"reasons": ["database_unavailable"]}
        try:
            async with asyncio.timeout(3):
                await self.vectors.ping()
        except Exception:
            result["vector_search"] = False
            result["reasons"].append("vector_unavailable")
        if self.settings.queue_backend == "redis":
            try:
                async with asyncio.timeout(3):
                    if not self.queue:
                        raise ServiceError("queue unavailable")
                    await self.queue.ping()
            except Exception:
                result["submit_research"] = False
                result["upload_documents"] = False
                result["reasons"].append("queue_unavailable")
        try:
            async with asyncio.timeout(3):
                await self.documents.store.ping()
        except Exception:
            result["upload_documents"] = False
            result["reasons"].append("object_store_unavailable")
        if self.settings.document_scan_mode == "clamav":
            try:
                async with asyncio.timeout(2):
                    _, writer = await asyncio.open_connection(self.settings.clamav_host, self.settings.clamav_port)
                    writer.close()
                    await writer.wait_closed()
            except Exception:
                result["upload_documents"] = False
                result["reasons"].append("scanner_unavailable")
        from ..core.reliability import fingerprint
        from ..core.config import endpoint
        try:
            await self.providers.provider_available(fingerprint([endpoint(self.settings.llm_base_url, "chat/completions"),
                self.settings.llm_api_key.get_secret_value(), self.settings.llm_model_id]))
        except ServiceError as exc:
            result["submit_research"] = False
            result["reasons"].append(exc.code)
        return result

    async def save_run_memory(self, run_id: str, user: str):
        run = await self.db.owned_run(run_id, user)
        if not run or run["status"] != "completed":
            raise LookupError("仅已完成的研究可以保存为语义记忆")
        await self.save_summary(run, run["report"])
        return {"ok": True, "run_id": run_id}

    async def metrics(self, user: str) -> dict:
        runs = await self.db.rows("SELECT id,status,validation FROM runs WHERE user_id=?", (user,))
        counters = await self.db.rows("""SELECT c.* FROM counters c JOIN runs r ON r.id=c.run_id
            WHERE r.user_id=?""", (user,))
        durations = [json.loads(row["data"]).get("duration_ms", 0) for row in await self.db.rows(
            """SELECT e.data FROM events e JOIN runs r ON r.id=e.run_id WHERE r.user_id=? AND e.type='node_end'""", (user,))]
        durations = sorted(value for value in durations if isinstance(value, (int, float)) and value >= 0)
        def percentile(value):
            return durations[min(len(durations) - 1, max(0, round((len(durations) - 1) * value)))] if durations else 0
        evidence = [json.loads(run["validation"]).get("evidence_metrics", {}) for run in runs]
        return {"runs": len(runs), "completed": sum(r["status"] == "completed" for r in runs),
                "insufficient": sum(r["status"] == "insufficient" for r in runs),
                "llm_calls": sum(r.get("llm_calls", 0) for r in counters),
                "search_calls": sum(r.get("search_calls", 0) for r in counters),
                "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in counters),
                "completion_tokens": sum(r.get("completion_tokens", 0) for r in counters),
                "node_latency_ms": {"p50": percentile(.5), "p95": percentile(.95)},
                "avg_sources": round(statistics.mean([m.get("source_count", 0) for m in evidence]), 2) if evidence else 0,
                "avg_web_domains": round(statistics.mean([m.get("unique_web_domains", 0) for m in evidence]), 2) if evidence else 0}

    async def export_user(self, user: str, owner_subject: str | None = None) -> bytes:
        owner_subject = owner_subject or user
        runs = await self.db.rows("SELECT * FROM runs WHERE user_id=?", (user,))
        memories = await self.db.rows("""SELECT id,kind,content,run_id,created_at FROM memories WHERE
            (kind IN ('profile','preference') AND owner_subject=?) OR (kind='semantic' AND user_id=?)""",
                                      (owner_subject, user))
        documents = await self.db.rows("SELECT * FROM documents WHERE user_id=? AND status='ready'", (user,))
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("research.json", json.dumps(runs, ensure_ascii=False, indent=2))
            archive.writestr("memories.json", json.dumps(memories, ensure_ascii=False, indent=2))
            archive.writestr("documents.json", json.dumps(documents, ensure_ascii=False, indent=2))
            for document in documents:
                if await self.documents.store.exists("uploads", document["id"]):
                    archive.writestr("uploads/" + document["name"], await self.documents.store.get("uploads", document["id"]))
        return output.getvalue()

    async def purge_user(self, user: str, scope: str, owner_subject: str | None = None):
        owner_subject = owner_subject or user
        if scope not in {"reports", "memories", "documents", "all"}:
            raise ValueError("scope 必须是 reports、memories、documents 或 all")
        if scope in {"documents", "all"}:
            for document in await self.db.rows("SELECT id FROM documents WHERE user_id=? AND status!='deleted'", (user,)):
                await self.documents.delete(document["id"], user)
        if scope in {"memories", "all"}:
            memories = await self.db.rows("""SELECT id,kind FROM memories WHERE
                (kind IN ('profile','preference') AND owner_subject=?) OR (kind='semantic' AND user_id=?)""",
                                          (owner_subject, user))
            for memory in memories:
                if memory["kind"] == "semantic":
                    await self.vectors.delete("memories", user, "id", memory["id"])
                await self.db.execute("DELETE FROM memories WHERE id=?", (memory["id"],))
        if scope in {"reports", "all"}:
            active = await self.db.rows("""SELECT id FROM runs WHERE user_id=?
                AND status IN ('queued','running','interrupted')""", (user,))
            for row in active:
                await self.cancel(row["id"], user)
            run_ids = await self.db.rows("SELECT id FROM runs WHERE user_id=?", (user,))
            await self.attachments.delete_runs([row["id"] for row in run_ids])
            for row in run_ids:
                await self.checkpointer.adelete_thread(row["id"])
                await self.db.execute("DELETE FROM events WHERE run_id=?", (row["id"],))
                await self.db.execute("DELETE FROM counters WHERE run_id=?", (row["id"],))
                await self.db.execute("DELETE FROM search_cache WHERE run_id=?", (row["id"],))
                await self.db.execute("DELETE FROM verified_claims WHERE run_id=?", (row["id"],))
                await self.db.execute("DELETE FROM call_attempts WHERE run_id=?", (row["id"],))
                await self.db.execute("DELETE FROM dead_letter_runs WHERE run_id=?", (row["id"],))
            await self.db.execute("DELETE FROM runs WHERE user_id=?", (user,))
            await self.db.execute("DELETE FROM threads WHERE user_id=?", (user,))
        if scope == "all":
            await self.db.execute("UPDATE attachments SET status='deleted' WHERE user_id=? AND run_id='' AND owner_subject=?", (user, owner_subject))
        await self.attachments.cleanup()
        return {"ok": True, "scope": scope}
