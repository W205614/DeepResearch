import asyncio
import hmac
import json
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from ..core.config import Settings
from ..core.db import now, uid
from ..domain.models import DocumentSearchRequest, MemoryRequest, RunRequest, ThreadRequest
from ..infrastructure.providers import ServiceError
from ..services.runtime import ConflictError, Runtime, TERMINAL


def create_app(settings: Settings | None = None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        runtime = await Runtime(settings).start()
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.close()

    app = FastAPI(title="DeepResearch", version="0.1.0", lifespan=lifespan)

    def rt(request: Request) -> Runtime:
        return request.app.state.runtime

    async def identity(x_user_id: str = Header("local-user"), authorization: str = Header("")):
        token = settings.app_access_token.get_secret_value()
        if token and not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "请填写工作台访问令牌")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", x_user_id):
            raise HTTPException(400, "演示用户标识仅支持字母、数字、下划线和连字符")
        return x_user_id

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(ConflictError)
    async def conflict_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(LookupError)
    async def lookup_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/api/status")
    async def status(runtime=Depends(rt), user=Depends(identity)):
        return await runtime.status()

    @app.get("/api/metrics")
    async def metrics(runtime=Depends(rt), user=Depends(identity)):
        return await runtime.metrics(user)

    @app.get("/api/data/export")
    async def export_data(runtime=Depends(rt), user=Depends(identity)):
        return Response(await runtime.export_user(user), media_type="application/zip",
                        headers={"Content-Disposition": "attachment; filename=deepresearch-export.zip"})

    @app.delete("/api/data")
    async def purge_data(scope: str, x_confirm_delete: str = Header(""), runtime=Depends(rt), user=Depends(identity)):
        if x_confirm_delete != "DELETE":
            raise HTTPException(400, "请使用 X-Confirm-Delete: DELETE 确认清理")
        try:
            return await runtime.purge_user(user, scope)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/config/check")
    async def check(runtime=Depends(rt), user=Depends(identity)):
        from ..domain.models import Route
        results = {}
        for name in ("llm", "embedding", "milvus"):
            try:
                if name == "llm":
                    await runtime.providers.structured("router", "返回 mode=quick 和 reason=连接测试。",
                        {"topic": "连接测试"}, Route, "")
                elif name == "embedding":
                    vector = (await runtime.providers.embed(["连接测试"]))[0]
                    results["actual_embedding_dimension"] = len(vector)
                    await runtime.vectors.fingerprint()
                else:
                    await runtime.vectors.ping()
                results[name] = "ok"
            except ServiceError as exc:
                results[name] = str(exc)
        if settings.web_search_provider == "deepseek":
            results["search"] = "DeepSeek web_search configured" if settings.llm_api_key.get_secret_value() else "missing LLM_API_KEY"
        elif settings.web_search_provider == "bocha":
            results["search"] = "Bocha configured" if settings.bocha_api_key.get_secret_value() else "missing BOCHA_API_KEY"
        else:
            results["search"] = "DeepSeek primary; Bocha fallback " + ("configured" if settings.bocha_api_key.get_secret_value() else "not configured")
        return results

    @app.post("/api/threads", status_code=201)
    async def create_thread(body: ThreadRequest, runtime=Depends(rt), user=Depends(identity)):
        return await runtime.new_thread(user, body.title)

    @app.get("/api/threads")
    async def threads(runtime=Depends(rt), user=Depends(identity)):
        return await runtime.db.rows("SELECT * FROM threads WHERE user_id=? ORDER BY created_at DESC", (user,))

    @app.patch("/api/threads/{thread_id}")
    async def rename_thread(thread_id: str, body: ThreadRequest, runtime=Depends(rt), user=Depends(identity)):
        thread = await runtime.find_thread(thread_id, user)
        if not thread or not await runtime.db.execute("UPDATE threads SET title=? WHERE id=? AND user_id=?", (body.title, thread["id"], user)):
            raise HTTPException(404, "会话不存在")
        return {"ok": True}

    @app.delete("/api/threads/{thread_id}")
    async def delete_thread(thread_id: str, runtime=Depends(rt), user=Depends(identity)):
        try:
            return await runtime.delete_thread(thread_id, user)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.get("/api/threads/{thread_id}/runs")
    async def thread_runs(thread_id: str, runtime=Depends(rt), user=Depends(identity)):
        thread = await runtime.find_thread(thread_id, user)
        if not thread:
            raise HTTPException(404, "会话不存在")
        rows = await runtime.db.rows("SELECT id FROM runs WHERE thread_id=? AND user_id=? ORDER BY created_at", (thread["id"], user))
        return [await runtime.db.owned_run(row["id"], user) for row in rows]

    @app.post("/api/research/runs", status_code=202)
    async def start_run(body: RunRequest, runtime=Depends(rt), user=Depends(identity)):
        return await runtime.create(user, body)

    @app.get("/api/research/runs/{run_id}")
    async def get_run(run_id: str, runtime=Depends(rt), user=Depends(identity)):
        run = await runtime.db.owned_run(run_id, user)
        if not run:
            raise HTTPException(404, "任务不存在")
        return run

    @app.get("/api/research/runs/{run_id}/report")
    async def report(run_id: str, runtime=Depends(rt), user=Depends(identity)):
        run = await runtime.db.owned_run(run_id, user)
        if not run:
            raise HTTPException(404, "任务不存在")
        return PlainTextResponse(run["report"], media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="research-{run_id}.md"'})

    @app.post("/api/research/runs/{run_id}/cancel")
    async def cancel(run_id: str, runtime=Depends(rt), user=Depends(identity)):
        return await runtime.cancel(run_id, user)

    @app.post("/api/research/runs/{run_id}/resume", status_code=202)
    async def resume(run_id: str, runtime=Depends(rt), user=Depends(identity)):
        return await runtime.resume(run_id, user)

    @app.post("/api/research/runs/{run_id}/memory", status_code=201)
    async def save_run_memory(run_id: str, runtime=Depends(rt), user=Depends(identity)):
        try:
            return await runtime.save_run_memory(run_id, user)
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/research/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = 0, last_event_id: str = Header("0"),
                     runtime=Depends(rt), user=Depends(identity)):
        if not await runtime.db.owned_run(run_id, user):
            raise HTTPException(404, "任务不存在")
        try:
            cursor = max(after, int(last_event_id), 0)
        except ValueError:
            raise HTTPException(400, "事件序号无效") from None

        async def stream():
            nonlocal cursor
            ticks = 0
            while not await request.is_disconnected():
                rows = await runtime.db.rows("SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 200", (run_id, cursor))
                for row in rows:
                    cursor = row["id"]
                    data = json.dumps({"id": cursor, "type": row["type"], "created_at": row["created_at"],
                                       "data": json.loads(row["data"])}, ensure_ascii=False)
                    yield f"id: {cursor}\ndata: {data}\n\n"
                run = await runtime.db.one("SELECT status FROM runs WHERE id=? AND user_id=?", (run_id, user))
                if not run or (run["status"] in TERMINAL and not rows):
                    yield "event: close\ndata: {}\n\n"
                    break
                if ticks % 20 == 0:
                    yield ": keep-alive\n\n"
                ticks += 1
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/documents", status_code=202)
    async def upload(file: UploadFile = File(), runtime=Depends(rt), user=Depends(identity)):
        content = await file.read(10 * 1024 * 1024 + 1)
        await file.close()
        async with runtime.upload_lock:
            return await runtime.documents.add(user, file.filename or "document", content)

    @app.get("/api/documents")
    async def documents(runtime=Depends(rt), user=Depends(identity)):
        return await runtime.db.rows("SELECT * FROM documents WHERE user_id=? AND status!='deleted' ORDER BY created_at DESC", (user,))

    @app.get("/api/documents/{document_id}/chunks")
    async def document_chunks(document_id: str, limit: int = 100, runtime=Depends(rt), user=Depends(identity)):
        if not await runtime.db.one("SELECT id FROM documents WHERE id=? AND user_id=? AND status!='deleted'", (document_id, user)):
            raise HTTPException(404, "资料不存在")
        limit = max(1, min(limit, 400))
        return await runtime.db.rows("SELECT id,locator,text FROM chunks WHERE document_id=? AND user_id=? ORDER BY id LIMIT ?",
                                     (document_id, user, limit))

    @app.post("/api/documents/search")
    async def search_documents(body: DocumentSearchRequest, runtime=Depends(rt), user=Depends(identity)):
        return await runtime.documents.search(user, [body.query], limit=body.limit)

    @app.post("/api/documents/{document_id}/reindex", status_code=202)
    async def reindex_document(document_id: str, runtime=Depends(rt), user=Depends(identity)):
        try:
            return await runtime.documents.reindex(document_id, user)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.delete("/api/documents/{document_id}")
    async def delete_document(document_id: str, runtime=Depends(rt), user=Depends(identity)):
        if not await runtime.db.one("SELECT id FROM documents WHERE id=? AND user_id=?", (document_id, user)):
            raise HTTPException(404, "资料不存在")
        await runtime.documents.delete(document_id, user)
        return {"ok": True}

    @app.get("/api/memories")
    async def memories(runtime=Depends(rt), user=Depends(identity)):
        return await runtime.db.rows("SELECT id,kind,content,created_at FROM memories WHERE user_id=? ORDER BY created_at DESC", (user,))

    @app.post("/api/memories", status_code=201)
    async def add_memory(body: MemoryRequest, runtime=Depends(rt), user=Depends(identity)):
        memory_id = uid()
        await runtime.db.execute("INSERT INTO memories(id,user_id,kind,content,run_id,created_at) VALUES(?,?,'preference',?,?,?)",
                                  (memory_id, user, body.content, memory_id, now()))
        return {"id": memory_id, "content": body.content}

    @app.put("/api/memories/{memory_id}")
    async def edit_memory(memory_id: str, body: MemoryRequest, runtime=Depends(rt), user=Depends(identity)):
        if not await runtime.db.execute("UPDATE memories SET content=? WHERE id=? AND user_id=? AND kind='preference'",
                                         (body.content, memory_id, user)):
            raise HTTPException(404, "可编辑的用户偏好不存在")
        return {"ok": True}

    @app.delete("/api/memories/{memory_id}")
    async def delete_memory(memory_id: str, runtime=Depends(rt), user=Depends(identity)):
        memory = await runtime.db.one("SELECT * FROM memories WHERE id=? AND user_id=?", (memory_id, user))
        if not memory:
            raise HTTPException(404, "记忆不存在")
        await runtime.db.execute("DELETE FROM memories WHERE id=? AND user_id=?", (memory_id, user))
        if memory["kind"] == "semantic":
            await runtime.vectors.delete("memories", user, "id", memory_id)
        return {"ok": True}

    return app
