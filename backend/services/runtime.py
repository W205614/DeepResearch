import asyncio
import io
import json
import sqlite3
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
from ..research.graph import ResearchGraph
from ..core.observability import configure_task_logger, error_category, inject_trace_context, run_label
from ..core.metrics import DLQ_DEPTH, DLQ_EVENTS, FAILURES, QUEUE_DEPTH, RUNS, RUN_SECONDS
from ..infrastructure.providers import Providers, ServiceError
from ..infrastructure.vectors import VectorIndex

TERMINAL = {"completed", "insufficient", "failed", "cancelled", "interrupted"}
RUN_HEARTBEAT_SECONDS = 10
RUN_STALE_SECONDS = 45


class ConflictError(Exception):
    pass


class Runtime:
    def __init__(self, settings: Settings, *, worker_mode: bool = False):
        self.settings = settings
        if not settings.database_url and not settings.demo_mode:
            raise RuntimeError("DATABASE_URL is required outside isolated offline tests")
        settings.prepare()
        self.db = PostgresDatabase(settings.database_url) if settings.database_url else Database(settings.data_dir / "research.sqlite3")
        self.providers = Providers(settings, self.db)
        self.vectors = VectorIndex(settings, self.db)
        self.documents = Documents(settings, self.db, self.providers, self.vectors)
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
        await self.documents.start()
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
        self.graph = ResearchGraph(self.settings, self.db, self.providers, self.vectors, self.documents).build(self.checkpointer)
        if not self.worker_mode:
            if self.settings.queue_backend == "redis":
                from arq import create_pool
                from arq.connections import RedisSettings
                self.queue = await create_pool(RedisSettings.from_dsn(self.settings.redis_url))
                await self.recover_queued_runs()
                self.recovery_task = asyncio.create_task(self.recover_stale_runs_loop(), name="run-recovery")
            await self.recover_indexing_documents()
        return self

    async def close(self):
        self.stopping = True
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
        if self.queue:
            await self.queue.enqueue_job("ingest_document", document_id, trace_context=inject_trace_context())
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
            for run_id in run_ids:
                await self.checkpointer.adelete_thread(run_id)
                await self.db.execute("DELETE FROM events WHERE run_id=?", (run_id,))
                await self.db.execute("DELETE FROM counters WHERE run_id=?", (run_id,))
                await self.db.execute("DELETE FROM search_cache WHERE run_id=?", (run_id,))
            await self.db.execute("DELETE FROM runs WHERE thread_id=? AND user_id=?", (thread_id, user))
            await self.db.execute("DELETE FROM threads WHERE id=? AND user_id=?", (thread_id, user))
        return {"ok": True, "thread_id": thread["thread_key"], "deleted_runs": len(run_ids)}

    async def create(self, user, request):
        async with self.lock:
            limits = await self.db.workspace_limits(user)
            if limits:
                active = await self.db.one("SELECT COUNT(*) AS total FROM runs WHERE user_id=? AND status IN ('queued','running')", (user,))
                if int(active["total"]) >= int(limits["concurrent_run_limit"]):
                    raise ConflictError("工作空间正在执行的研究已达到并发上限")
                usage = await self.db.one("""SELECT COALESCE(SUM(c.search_calls),0) AS searches FROM counters c JOIN runs r ON r.id=c.run_id
                    WHERE r.user_id=? AND substr(r.created_at,1,10)=substr(?,1,10)""", (user, now())) or {}
                if int(usage.get("searches", 0)) >= int(limits["daily_search_limit"]):
                    raise ConflictError("工作空间今日搜索预算已用完")
            previous = await self.db.one("SELECT id,topic,mode FROM runs WHERE user_id=? AND client_request_id=?",
                                         (user, request.client_request_id))
            if previous:
                if previous["topic"] != request.topic or previous["mode"] != request.mode:
                    raise ConflictError("同一个请求编号不能提交不同内容")
                return await self.db.owned_run(previous["id"], user)
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
                await self.db.execute("""INSERT INTO runs(id,user_id,thread_id,topic,mode,status,created_at,updated_at,client_request_id)
                    VALUES(?,?,?,?,?,'queued',?,?,?)""", (run_id, user, thread_id, request.topic, request.mode,
                                                        stamp, stamp, request.client_request_id))
            except sqlite3.IntegrityError:
                raise ConflictError("同一会话已有正在执行的研究") from None
            await self.db.event(run_id, "queued", {"message": "研究任务已创建"})
            await self.refresh_queue_depth()
            await self.schedule(run_id)
            return await self.db.owned_run(run_id, user)

    async def schedule(self, run_id, resume=False):
        self.logger.info("run=%s phase=scheduled resume=%s", run_label(run_id), resume)
        if self.queue:
            run = await self.db.one("SELECT attempt_count FROM runs WHERE id=?", (run_id,))
            if not run:
                return
            await self.queue.enqueue_job("run_research", run_id, resume=resume, trace_context=inject_trace_context(),
                                         _job_id=self.run_job_id(run_id, int(run.get("attempt_count") or 0), next_attempt=True))
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
        preferences = await self.db.rows("SELECT content FROM memories WHERE user_id=? AND kind='preference' ORDER BY created_at DESC LIMIT 20", (run["user_id"],))
        profile = await self.db.one("SELECT content FROM memories WHERE user_id=? AND kind='profile'", (run["user_id"],))
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
        try:
            async with self.gate:
                eligible = "('queued','interrupted')" if resume else "('queued')"
                claimed = await self.db.execute(f"""UPDATE runs SET status='running',updated_at=?,error='',
                    attempt_count=attempt_count+1,last_attempt_at=? WHERE id=? AND status IN {eligible}""",
                                                (now(), now(), run_id))
                if not claimed:
                    return
                run = await self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
                self.logger.info("run=%s phase=started mode=%s resumed=%s", run_label(run_id), run["mode"], resume)
                heartbeat = asyncio.create_task(self.heartbeat(run_id), name=f"heartbeat-{run_id}")
                await self.refresh_queue_depth()
                await self.db.event(run_id, "running", {"resumed": resume})
                config = {"configurable": {"thread_id": run_id}, "recursion_limit": 80}
                async with asyncio.timeout(self.settings.max_run_seconds):
                    initial = None
                    checkpoint = await self.graph.aget_state(config) if resume else None
                    if not resume or not checkpoint.values:
                        initial = {"run_id": run_id, "user_id": run["user_id"], "thread_id": run["thread_id"],
                                   "topic": run["topic"], "requested_mode": run["mode"],
                                   "context": await self.context(run)}
                    result = await self.graph.ainvoke(initial, config)
                    report = result.get("report", "")
                    validation = result.get("validation", {})
                    if (self.settings.auto_save_semantic_memory and report and not validation.get("insufficient")
                            and validation.get("kind") not in {"greeting", "help", "preference", "chat"}):
                        await self.save_summary(run, report)
                    status = "insufficient" if validation.get("insufficient") else "completed"
                    completed = await self.db.execute("""UPDATE runs SET status=?,updated_at=?,report=?,sources=?,validation=?,error=''
                        WHERE id=? AND status='running'""", (status, now(), report,
                                                              json.dumps(result.get("evidence", []), ensure_ascii=False),
                                                              json.dumps(validation, ensure_ascii=False), run_id))
                    if not completed:
                        return
                    await self.db.event(run_id, "done", {"status": status})
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
                if await self.db.execute("UPDATE runs SET status=?,updated_at=? WHERE id=? AND status='running'",
                                         (status, now(), run_id)):
                    await self.db.event(run_id, status, {"message": "任务已停止，可从检查点继续"})
                self.logger.warning("run=%s phase=cancelled status=%s", run_label(run_id), status)
        except TimeoutError:
            self.logger.error("run=%s phase=failed category=timeout", run_label(run_id))
            await self.fail(run_id, "达到任务时限，已停止外部调用；可从检查点继续", "interrupted", "timeout")
        except Exception as exc:
            FAILURES.labels(category=error_category(exc)).inc()
            RUNS.labels(status="failed").inc()
            RUN_SECONDS.observe(time.monotonic() - started)
            self.logger.error("run=%s phase=failed category=%s", run_label(run_id), error_category(exc))
            await self.fail(run_id, str(exc) if isinstance(exc, ServiceError) else "研究执行失败，请检查服务配置或重试", category=error_category(exc))
        finally:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    async def fail(self, run_id, message, status="failed", category="runtime_failure"):
        changed = await self.db.execute("UPDATE runs SET status=?,error=?,updated_at=? WHERE id=? AND status='running'",
                                        (status, message, now(), run_id))
        if changed:
            await self.db.event(run_id, "error", {"message": message, "status": status})
            if status == "failed":
                run = await self.db.one("SELECT user_id FROM runs WHERE id=?", (run_id,))
                await self.db.execute("DELETE FROM dead_letter_runs WHERE run_id=?", (run_id,))
                await self.db.execute("INSERT INTO dead_letter_runs(run_id,user_id,category,message,failed_at,recovered_at) VALUES(?,?,?,?,?,'' )", (run_id, run["user_id"], category, message[:240], now()))
                DLQ_EVENTS.labels(action="created", category=category).inc()
                await self.refresh_dead_letter_depth()
        await self.refresh_queue_depth()

    async def dead_letters(self, user):
        return await self.db.rows("SELECT run_id,category,message,failed_at FROM dead_letter_runs WHERE user_id=? AND recovered_at='' ORDER BY failed_at DESC", (user,))

    async def recover_dead_letter(self, run_id, user):
        row = await self.db.one("SELECT run_id,category FROM dead_letter_runs WHERE run_id=? AND user_id=? AND recovered_at=''", (run_id, user))
        if not row:
            raise LookupError("死信任务不存在或已恢复")
        run = await self.resume(run_id, user)
        await self.db.execute("UPDATE dead_letter_runs SET recovered_at=? WHERE run_id=?", (now(), run_id))
        DLQ_EVENTS.labels(action="recovered", category=row.get("category", "runtime_failure")).inc()
        await self.refresh_dead_letter_depth()
        return run

    async def refresh_dead_letter_depth(self):
        row = await self.db.one("SELECT COUNT(*) AS total FROM dead_letter_runs WHERE recovered_at='' ")
        DLQ_DEPTH.set(int((row or {}).get("total", 0)))

    async def cancel(self, run_id, user):
        async with self.lock:
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
        async with self.lock:
            run = await self.db.owned_run(run_id, user)
            if not run:
                raise LookupError("任务不存在")
            if run["status"] not in {"cancelled", "interrupted", "failed"}:
                raise ConflictError("当前任务状态不能继续；需要重新研究时请提交新任务")
            if run_id in self.tasks:
                raise ConflictError("任务尚未停止")
            try:
                await self.db.execute("UPDATE runs SET status='queued',updated_at=? WHERE id=?", (now(), run_id))
            except sqlite3.IntegrityError:
                raise ConflictError("当前会话已有其他研究任务") from None
            await self.refresh_queue_depth()
            await self.schedule(run_id, resume=True)
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
        rows = await self.db.rows("SELECT id,status FROM runs WHERE status IN ('queued','interrupted')")
        for row in rows:
            if row["id"] not in self.tasks:
                await self.schedule(row["id"], resume=row["status"] == "interrupted")

    async def heartbeat(self, run_id: str) -> None:
        while True:
            await asyncio.sleep(RUN_HEARTBEAT_SECONDS)
            changed = await self.db.execute("UPDATE runs SET last_attempt_at=?,updated_at=? WHERE id=? AND status='running'",
                                            (now(), now(), run_id))
            if not changed:
                return

    async def recover_stale_runs_loop(self) -> None:
        while not self.stopping:
            await asyncio.sleep(RUN_HEARTBEAT_SECONDS)
            await self.recover_stale_runs()

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
            changed = await self.db.execute("""UPDATE runs SET status='interrupted',updated_at=? WHERE id=?
                                            AND status='running' AND last_attempt_at=?""",
                                            (now(), row["id"], row["last_attempt_at"]))
            if changed:
                await self.db.event(row["id"], "interrupted", {"message": "Worker 心跳超时，正在从检查点恢复"})
                await self.schedule(row["id"], resume=True)

    async def recover_indexing_documents(self):
        """Resume persisted, clean documents after an API restart."""
        rows = await self.db.rows("SELECT id FROM documents WHERE status='indexing'")
        for row in rows:
            await self.schedule_document(row["id"])

    async def refresh_queue_depth(self) -> None:
        row = await self.db.one("SELECT COUNT(*) AS total FROM runs WHERE status='queued'")
        QUEUE_DEPTH.set(int((row or {}).get("total", 0)))

    async def ready(self):
        try:
            await self.vectors.ping()
            if self.queue:
                await self.queue.ping()
        except Exception as exc:
            raise ServiceError("依赖服务未就绪") from exc

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

    async def export_user(self, user: str) -> bytes:
        runs = await self.db.rows("SELECT * FROM runs WHERE user_id=?", (user,))
        memories = await self.db.rows("SELECT id,kind,content,run_id,created_at FROM memories WHERE user_id=?", (user,))
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

    async def purge_user(self, user: str, scope: str):
        if scope not in {"reports", "memories", "documents", "all"}:
            raise ValueError("scope 必须是 reports、memories、documents 或 all")
        if scope in {"documents", "all"}:
            for document in await self.db.rows("SELECT id FROM documents WHERE user_id=? AND status!='deleted'", (user,)):
                await self.documents.delete(document["id"], user)
        if scope in {"memories", "all"}:
            for memory in await self.db.rows("SELECT id,kind FROM memories WHERE user_id=?", (user,)):
                if memory["kind"] == "semantic":
                    await self.vectors.delete("memories", user, "id", memory["id"])
            await self.db.execute("DELETE FROM memories WHERE user_id=?", (user,))
        if scope in {"reports", "all"}:
            run_ids = await self.db.rows("SELECT id FROM runs WHERE user_id=?", (user,))
            for row in run_ids:
                await self.db.execute("DELETE FROM events WHERE run_id=?", (row["id"],))
                await self.db.execute("DELETE FROM counters WHERE run_id=?", (row["id"],))
                await self.db.execute("DELETE FROM search_cache WHERE run_id=?", (row["id"],))
            await self.db.execute("DELETE FROM runs WHERE user_id=?", (user,))
            await self.db.execute("DELETE FROM threads WHERE user_id=?", (user,))
        return {"ok": True, "scope": scope}
