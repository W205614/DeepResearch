import asyncio
import json
import httpx
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from prometheus_client import make_asgi_app
from ..core.config import Settings
from ..core.auth import TokenVerifier
from ..core.db import now, uid
from ..domain.models import DocumentSearchRequest, MemoryRequest, RunRequest, ThreadRequest
from ..core.observability import configure_telemetry
from ..core.metrics import ALERT_DELIVERIES
from ..domain.models import MembershipRequest, WorkspaceLimitRequest, WorkspaceRequest
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

    app.state.token_verifier = TokenVerifier(settings)
    configure_telemetry(app, settings.otel_exporter_otlp_endpoint)
    app.mount("/metrics", make_asgi_app())
    def rt(request: Request) -> Runtime:
        return request.app.state.runtime

    async def identity(request: Request, authorization: str = Header(""), x_workspace_id: str = Header("")):
        principal = await app.state.token_verifier.verify(authorization)
        runtime = rt(request)
        memberships = await runtime.db.memberships(principal.subject)
        if not memberships:
            workspace = await runtime.db.create_workspace(
                principal.subject, principal.display_name + " 的工作空间",
                (settings.workspace_daily_search_limit, settings.workspace_concurrent_run_limit), principal.subject)
            membership = {**workspace, "role": "admin"}
        elif x_workspace_id:
            membership = await runtime.db.membership(x_workspace_id, principal.subject)
            if not membership:
                raise HTTPException(403, "你不是该工作空间成员")
        else:
            membership = memberships[0]
        request.state.principal = principal
        request.state.workspace_role = membership["role"]
        return membership["id"]

    def require_role(request: Request, *roles: str) -> None:
        if getattr(request.state, "workspace_role", "") not in roles:
            raise HTTPException(403, "当前工作空间角色没有此权限")

    async def researcher(request: Request, user=Depends(identity)):
        require_role(request, "admin", "researcher")
        return user

    async def administrator(request: Request, user=Depends(identity)):
        require_role(request, "admin")
        return user
    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(ConflictError)
    async def conflict_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(LookupError)
    async def lookup_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.post("/internal/alerts")
    async def forward_alert(payload: dict):
        webhook = settings.feishu_webhook_url.get_secret_value()
        if not webhook:
            ALERT_DELIVERIES.labels("disabled").inc()
            return {"delivered": False, "reason": "not_configured"}
        alerts = payload.get("alerts", [])
        summary = "; ".join(str(a.get("annotations", {}).get("summary", a.get("labels", {}).get("alertname", "alert"))) for a in alerts)[:1500]
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.post(webhook, json={"msg_type":"text","content":{"text":"DeepResearch Alert: " + summary}})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            ALERT_DELIVERIES.labels("failed").inc()
            raise HTTPException(502, "alert delivery failed") from exc
        ALERT_DELIVERIES.labels("succeeded").inc()
        return {"delivered": True, "count": len(alerts)}
    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/api/auth/config")
    async def auth_config():
        """Public mode metadata; it never exposes a key, token, or issuer secret."""
        return {"mode": settings.auth_mode, "issuer": settings.oidc_issuer if settings.auth_mode == "oidc" else ""}

    @app.get("/livez")
    async def livez():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(runtime=Depends(rt)):
        try:
            await runtime.ready()
        except ServiceError as exc:
            raise HTTPException(503, str(exc)) from None
        return {"status": "ready"}

    @app.get("/api/workspaces")
    async def workspaces(request: Request, runtime=Depends(rt), user=Depends(identity)):
        return await runtime.db.memberships(request.state.principal.subject)

    @app.post("/api/workspaces", status_code=201)
    async def create_workspace(body: WorkspaceRequest, request: Request, runtime=Depends(rt), user=Depends(identity)):
        workspace = await runtime.db.create_workspace(
            request.state.principal.subject, body.name,
            (settings.workspace_daily_search_limit, settings.workspace_concurrent_run_limit))
        await runtime.db.audit(workspace["id"], request.state.principal.subject, "workspace.create",
                               "workspace", workspace["id"])
        return workspace

    @app.get("/api/workspaces/{workspace_id}/members")
    async def members(workspace_id: str, request: Request, runtime=Depends(rt), user=Depends(identity)):
        if workspace_id != user:
            raise HTTPException(403, "请先切换到目标工作空间")
        require_role(request, "admin")
        return await runtime.db.rows("SELECT subject,role,created_at FROM memberships WHERE workspace_id=? ORDER BY created_at", (user,))

    @app.put("/api/workspaces/{workspace_id}/members")
    async def upsert_member(workspace_id: str, body: MembershipRequest, request: Request,
                            runtime=Depends(rt), user=Depends(identity)):
        if workspace_id != user:
            raise HTTPException(403, "请先切换到目标工作空间")
        require_role(request, "admin")
        await runtime.db.upsert_membership(user, body.subject, body.role)
        await runtime.db.audit(user, request.state.principal.subject, "membership.upsert", "member", body.subject)
        return {"ok": True}

    @app.get("/api/workspaces/{workspace_id}/audit")
    async def audit(workspace_id: str, request: Request, runtime=Depends(rt), user=Depends(identity)):
        if workspace_id != user:
            raise HTTPException(403, "请先切换到目标工作空间")
        require_role(request, "admin")
        return await runtime.db.audit_rows(user)

    @app.get("/api/workspaces/{workspace_id}/dead-letters")
    async def dead_letters(workspace_id: str, request: Request, runtime=Depends(rt), user=Depends(identity)):
        if workspace_id != user:
            raise HTTPException(403, "请先切换到目标工作空间")
        require_role(request, "admin")
        return await runtime.dead_letters(user)

    @app.post("/api/workspaces/{workspace_id}/dead-letters/{run_id}/recover", status_code=202)
    async def recover_dead_letter(workspace_id: str, run_id: str, request: Request, runtime=Depends(rt), user=Depends(identity)):
        if workspace_id != user:
            raise HTTPException(403, "请先切换到目标工作空间")
        require_role(request, "admin")
        try:
            run = await runtime.recover_dead_letter(run_id, user)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        await runtime.db.audit(user, request.state.principal.subject, "dead_letter.recover", "run", run_id)
        return run

    @app.put("/api/workspaces/{workspace_id}/limits")
    async def update_limits(workspace_id: str, body: WorkspaceLimitRequest, request: Request,
                            runtime=Depends(rt), user=Depends(identity)):
        if workspace_id != user:
            raise HTTPException(403, "请先切换到目标工作空间")
        require_role(request, "admin")
        await runtime.db.execute("""UPDATE workspace_limits SET daily_search_limit=?,concurrent_run_limit=?
                                 WHERE workspace_id=?""",
                                 (body.daily_search_limit, body.concurrent_run_limit, user))
        await runtime.db.audit(user, request.state.principal.subject, "workspace.limits.update", "workspace", user)
        return await runtime.db.workspace_limits(user)
    @app.get("/api/status")
    async def status(runtime=Depends(rt), user=Depends(identity)):
        return await runtime.status()

    @app.get("/api/metrics")
    async def metrics(runtime=Depends(rt), user=Depends(identity)):
        return await runtime.metrics(user)

    @app.get("/api/data/export")
    async def export_data(request: Request, runtime=Depends(rt), user=Depends(administrator)):
        await runtime.db.audit(user, request.state.principal.subject, "data.export", "workspace", user)
        return Response(await runtime.export_user(user), media_type="application/zip",
                        headers={"Content-Disposition": "attachment; filename=deepresearch-export.zip"})

    @app.delete("/api/data")
    async def purge_data(scope: str, request: Request, x_confirm_delete: str = Header(""), runtime=Depends(rt), user=Depends(administrator)):
        if x_confirm_delete != "DELETE":
            raise HTTPException(400, "请使用 X-Confirm-Delete: DELETE 确认清理")
        try:
            result = await runtime.purge_user(user, scope)
            await runtime.db.audit(user, request.state.principal.subject, "data.purge", "scope", scope)
            return result
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
    async def create_thread(body: ThreadRequest, runtime=Depends(rt), user=Depends(researcher)):
        return await runtime.new_thread(user, body.title)

    @app.get("/api/threads")
    async def threads(runtime=Depends(rt), user=Depends(identity)):
        return await runtime.db.rows("SELECT * FROM threads WHERE user_id=? ORDER BY created_at DESC", (user,))

    @app.patch("/api/threads/{thread_id}")
    async def rename_thread(thread_id: str, body: ThreadRequest, runtime=Depends(rt), user=Depends(researcher)):
        thread = await runtime.find_thread(thread_id, user)
        if not thread or not await runtime.db.execute("UPDATE threads SET title=? WHERE id=? AND user_id=?", (body.title, thread["id"], user)):
            raise HTTPException(404, "会话不存在")
        return {"ok": True}

    @app.delete("/api/threads/{thread_id}")
    async def delete_thread(thread_id: str, request: Request, runtime=Depends(rt), user=Depends(administrator)):
        try:
            result = await runtime.delete_thread(thread_id, user)
            await runtime.db.audit(user, request.state.principal.subject, "thread.delete", "thread", thread_id)
            return result
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.get("/api/threads/{thread_id}/runs")
    async def thread_runs(thread_id: str, runtime=Depends(rt), user=Depends(identity)):
        thread = await runtime.find_thread(thread_id, user)
        if not thread:
            raise HTTPException(404, "会话不存在")
        rows = await runtime.db.rows("SELECT id FROM runs WHERE thread_id=? AND user_id=? ORDER BY created_at", (thread["id"], user))
        return [await runtime.db.owned_run(row["id"], user) for row in rows]
    @app.get("/api/threads/{thread_id}/report")
    async def thread_report(thread_id: str, runtime=Depends(rt), user=Depends(identity)):
        thread = await runtime.find_thread(thread_id, user)
        if not thread:
            raise HTTPException(404, "会话不存在")
        rows = await runtime.db.rows("SELECT id FROM runs WHERE thread_id=? AND user_id=? ORDER BY created_at", (thread["id"], user))
        runs = [await runtime.db.owned_run(row["id"], user) for row in rows]
        parts = [f"# {thread['title'] or '研究会话'}", "", "本文件包含该会话中的全部研究记录。"]
        for index, run in enumerate(runs, start=1):
            parts.extend(["", f"## {index}. {run['topic']}", "", f"- 状态：{run['status']}", f"- 创建时间：{run['created_at']}", "", run["report"] or "_此研究尚未生成 Markdown 报告。_"])
        return PlainTextResponse("\n".join(parts) + "\n", media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="conversation-{thread["id"]}.md"'})

    @app.post("/api/research/runs", status_code=202)
    async def start_run(body: RunRequest, request: Request, runtime=Depends(rt), user=Depends(researcher)):
        run = await runtime.create(user, body)
        await runtime.db.audit(user, request.state.principal.subject, "research.create", "run", run["id"])
        return run

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
    async def cancel(run_id: str, request: Request, runtime=Depends(rt), user=Depends(researcher)):
        run = await runtime.cancel(run_id, user)
        await runtime.db.audit(user, request.state.principal.subject, "research.cancel", "run", run_id)
        return run

    @app.post("/api/research/runs/{run_id}/resume", status_code=202)
    async def resume(run_id: str, runtime=Depends(rt), user=Depends(researcher)):
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
    async def upload(request: Request, file: UploadFile = File(), runtime=Depends(rt), user=Depends(researcher)):
        content = await file.read(10 * 1024 * 1024 + 1)
        await file.close()
        async with runtime.upload_lock:
            try:
                document = await runtime.upload_document(user, file.filename or "document", content)
            except ServiceError as exc:
                await runtime.db.audit(user, request.state.principal.subject, "document.upload", "document",
                                       str(getattr(exc, "document_id", "upload")), "rejected")
                raise
        await runtime.db.audit(user, request.state.principal.subject, "document.upload", "document",
                               document["id"], document["status"])
        return document

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
    async def reindex_document(document_id: str, request: Request, runtime=Depends(rt), user=Depends(researcher)):
        try:
            document = await runtime.reindex_document(document_id, user)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        await runtime.db.audit(user, request.state.principal.subject, "document.reindex", "document", document_id)
        return document

    @app.delete("/api/documents/{document_id}")
    async def delete_document(document_id: str, request: Request, runtime=Depends(rt), user=Depends(administrator)):
        if not await runtime.db.one("SELECT id FROM documents WHERE id=? AND user_id=?", (document_id, user)):
            raise HTTPException(404, "资料不存在")
        await runtime.documents.delete(document_id, user)
        await runtime.db.audit(user, request.state.principal.subject, "document.delete", "document", document_id)
        return {"ok": True}

    @app.get("/api/memories")
    async def memories(runtime=Depends(rt), user=Depends(identity)):
        return await runtime.db.rows("SELECT id,kind,content,created_at FROM memories WHERE user_id=? ORDER BY created_at DESC", (user,))

    @app.post("/api/memories", status_code=201)
    async def add_memory(body: MemoryRequest, runtime=Depends(rt), user=Depends(researcher)):
        memory_id = uid()
        await runtime.db.execute("INSERT INTO memories(id,user_id,kind,content,run_id,created_at) VALUES(?,?,'preference',?,?,?)",
                                  (memory_id, user, body.content, memory_id, now()))
        return {"id": memory_id, "content": body.content}

    @app.put("/api/memories/{memory_id}")
    async def edit_memory(memory_id: str, body: MemoryRequest, runtime=Depends(rt), user=Depends(researcher)):
        if not await runtime.db.execute("UPDATE memories SET content=? WHERE id=? AND user_id=? AND kind='preference'",
                                         (body.content, memory_id, user)):
            raise HTTPException(404, "可编辑的用户偏好不存在")
        return {"ok": True}

    @app.delete("/api/memories/{memory_id}")
    async def delete_memory(memory_id: str, runtime=Depends(rt), user=Depends(researcher)):
        memory = await runtime.db.one("SELECT * FROM memories WHERE id=? AND user_id=?", (memory_id, user))
        if not memory:
            raise HTTPException(404, "记忆不存在")
        await runtime.db.execute("DELETE FROM memories WHERE id=? AND user_id=?", (memory_id, user))
        if memory["kind"] == "semantic":
            await runtime.vectors.delete("memories", user, "id", memory_id)
        return {"ok": True}

    return app
