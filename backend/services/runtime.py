import asyncio
import io
import json
import sqlite3
import statistics
import zipfile
from contextlib import AsyncExitStack

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from ..core.config import Settings
from ..core.db import Database, now, uid
from .documents import Documents
from ..research.graph import ResearchGraph
from ..core.observability import configure_task_logger, error_category, run_label
from ..infrastructure.providers import Providers, ServiceError
from ..research.routing import decide
from ..infrastructure.vectors import VectorIndex

TERMINAL = {"completed", "insufficient", "failed", "cancelled", "interrupted"}


class ConflictError(Exception):
    pass


class Runtime:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.prepare()
        self.db = Database(settings.data_dir / "research.sqlite3")
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

    async def start(self):
        await self.db.init()
        self.checkpointer = await self.stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(self.settings.data_dir / "checkpoints.sqlite3")))
        self.graph = ResearchGraph(self.settings, self.db, self.providers, self.vectors, self.documents).build(self.checkpointer)
        return self

    async def close(self):
        self.stopping = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.documents.close()
        await self.stack.aclose()
        await self.providers.close()
        await self.vectors.close()

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
            self.schedule(run_id)
            return await self.db.owned_run(run_id, user)

    def schedule(self, run_id, resume=False):
        self.logger.info("run=%s phase=scheduled resume=%s", run_label(run_id), resume)
        task = asyncio.create_task(self.execute_run(run_id, resume), name=f"research-{run_id}")
        self.tasks[run_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(run_id, None))

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
        try:
            async with self.gate:
                run = await self.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
                self.logger.info("run=%s phase=started mode=%s resumed=%s", run_label(run_id), run["mode"], resume)
                await self.db.execute("UPDATE runs SET status='running',updated_at=?,error='' WHERE id=?", (now(), run_id))
                await self.db.event(run_id, "running", {"resumed": resume})
                config = {"configurable": {"thread_id": run_id}, "recursion_limit": 80}
                async with asyncio.timeout(self.settings.max_run_seconds):
                    initial = None
                    checkpoint = await self.graph.aget_state(config) if resume else None
                    if not resume or not checkpoint.values:
                        decision = decide(run["mode"], run["topic"])
                        static_response = decision and decision.signals in {("greeting",), ("help",), ("preference",),
                                                                              ("assistant_name_set",), ("assistant_name_query",)}
                        initial = {"run_id": run_id, "user_id": run["user_id"], "thread_id": run["thread_id"],
                                   "topic": run["topic"], "requested_mode": run["mode"],
                                   "context": "" if static_response else await self.context(run)}
                    result = await self.graph.ainvoke(initial, config)
                    report = result.get("report", "")
                    validation = result.get("validation", {})
                    if (self.settings.auto_save_semantic_memory and report and not validation.get("insufficient")
                            and validation.get("kind") not in {"greeting", "help", "preference", "chat"}):
                        await self.save_summary(run, report)
                    status = "insufficient" if validation.get("insufficient") else "completed"
                    await self.db.execute("""UPDATE runs SET status=?,updated_at=?,report=?,sources=?,validation=?,error=''
                        WHERE id=?""", (status, now(), report, json.dumps(result.get("evidence", []), ensure_ascii=False),
                                         json.dumps(validation, ensure_ascii=False), run_id))
                    await self.db.event(run_id, "done", {"status": status})
                    usage = await self.db.one("SELECT llm_calls,search_calls FROM counters WHERE run_id=?", (run_id,)) or {}
                    self.logger.info("run=%s phase=completed status=%s sources=%d llm_calls=%d search_calls=%d",
                                     run_label(run_id), status, len(result.get("evidence", [])),
                                     usage.get("llm_calls", 0), usage.get("search_calls", 0))
        except asyncio.CancelledError:
            status = "interrupted" if self.stopping else "cancelled"
            await self.db.execute("UPDATE runs SET status=?,updated_at=? WHERE id=?", (status, now(), run_id))
            await self.db.event(run_id, status, {"message": "任务已停止，可从检查点继续"})
            self.logger.warning("run=%s phase=cancelled status=%s", run_label(run_id), status)
        except TimeoutError:
            self.logger.error("run=%s phase=failed category=timeout", run_label(run_id))
            await self.fail(run_id, "达到任务时限，已停止外部调用；可从检查点继续", "interrupted")
        except Exception as exc:
            self.logger.error("run=%s phase=failed category=%s", run_label(run_id), error_category(exc))
            await self.fail(run_id, str(exc) if isinstance(exc, ServiceError) else "研究执行失败，请检查服务配置或重试")

    async def fail(self, run_id, message, status="failed"):
        await self.db.execute("UPDATE runs SET status=?,error=?,updated_at=? WHERE id=?", (status, message, now(), run_id))
        await self.db.event(run_id, "error", {"message": message, "status": status})

    async def cancel(self, run_id, user):
        if not await self.db.owned_run(run_id, user):
            raise LookupError("任务不存在")
        task = self.tasks.get(run_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
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
            self.schedule(run_id, resume=True)
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
                "identity_mode": "local_demo", "max_reflection_rounds": self.settings.max_reflection_rounds,
                "max_search_calls": self.settings.max_search_calls,
                "web_results_per_query": self.settings.web_results_per_query,
                "max_web_candidates": self.settings.max_web_candidates}

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
        documents = await self.db.rows("SELECT * FROM documents WHERE user_id=?", (user,))
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("research.json", json.dumps(runs, ensure_ascii=False, indent=2))
            archive.writestr("memories.json", json.dumps(memories, ensure_ascii=False, indent=2))
            archive.writestr("documents.json", json.dumps(documents, ensure_ascii=False, indent=2))
            for document in documents:
                path = self.settings.data_dir / "uploads" / document["id"]
                if path.is_file():
                    archive.writestr("uploads/" + document["name"], path.read_bytes())
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
