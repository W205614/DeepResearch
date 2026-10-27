import asyncio
import hmac
import json
import httpx
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile, Query
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from prometheus_client import make_asgi_app
from ..core.config import Settings
from ..core.auth import TokenVerifier
from ..core.db import now, uid
from ..domain.models import DocumentSearchRequest, MemoryRequest, RunRequest, ThreadRequest
from ..core.observability import configure_telemetry
from ..core.metrics import ALERT_DELIVERIES, ALERT_SUPPRESSED
from ..core.usage_governance import paid_scope, paid_identity, UsageScope, UsageGovernance, provider_account
from ..core.reliability import fingerprint
from ..core.alerts import AlertRelay, deliver_with_retry, format_alert_message, post_alert
from ..domain.models import MembershipRequest, WorkspaceLimitRequest, WorkspaceRequest
from ..infrastructure.providers import ServiceError
from ..services.document_parsing import InvalidDocument
from ..services.document_access import require_document, require_sources, access_sql, access_args
from ..services.runtime import ConflictError, Runtime, TERMINAL


async def read_upload(file: UploadFile) -> bytes:
    try:
        return await file.read(10 * 1024 * 1024 + 1)
    finally:
        await file.close()


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
    app.state.alert_relay = AlertRelay()
    app.state.alert_sender = post_alert

    relay_token = settings.alert_relay_token.get_secret_value()
    if settings.alert_relay_token_file:
        try:
            relay_token = settings.alert_relay_token_file.read_text(encoding="utf-8").strip()
        except OSError:
            relay_token = ""
    app.state.alert_relay_token = relay_token
    internal_token = settings.internal_service_token.get_secret_value()
    if settings.internal_service_token_file:
        try:
            internal_token = settings.internal_service_token_file.read_text(encoding="utf-8").strip()
        except OSError:
            internal_token = ""
    app.state.internal_service_token = internal_token
    configure_telemetry(app, settings.otel_exporter_otlp_endpoint)
    app.mount("/metrics", make_asgi_app())
    def rt(request: Request) -> Runtime:
        return request.app.state.runtime

    async def owned_thread(runtime: Runtime, thread_id: str, user: str):
        thread = await runtime.find_thread(thread_id, user)
        if not thread:
            raise HTTPException(404, "会话不存在")
        return thread

    async def owned_run(runtime: Runtime, run_id: str, user: str):
        run = await runtime.db.owned_run(run_id, user)
        if not run:
            raise HTTPException(404, "任务不存在")
        caller = paid_identity()
        member = await runtime.db.membership(user, caller[1]) if caller and caller[0] == user else None
        if not member or (member["role"] != "admin" and run.get("created_by") != caller[1]):
            raise HTTPException(404, "任务不存在")
        try:
            await require_sources(runtime.db, user, run["sources"])
        except ServiceError:
            raise HTTPException(404, "任务不存在或资料访问权限已撤销") from None
        return run

    async def visible_thread_runs(runtime: Runtime, thread_id: str, user: str):
        rows = await runtime.db.rows("SELECT id FROM runs WHERE thread_id=? AND user_id=? ORDER BY created_at", (thread_id, user))
        visible = []
        for row in rows:
            try:
                visible.append(await owned_run(runtime, row["id"], user))
            except HTTPException as error:
                if error.status_code != 404:
                    raise
        return visible

    async def identity(request: Request, authorization: str = Header(""), x_workspace_id: str = Header("")):
        principal = await app.state.token_verifier.verify(authorization)
        runtime = rt(request)
        memberships = await runtime.db.memberships(principal.subject)
        if not memberships:
            membership = await runtime.db.ensure_personal_workspace(
                principal.subject, principal.display_name + " 的工作空间",
                (0, settings.workspace_concurrent_run_limit))
        elif x_workspace_id:
            membership = await runtime.db.membership(x_workspace_id, principal.subject)
            if not membership:
                raise HTTPException(403, "你不是该工作空间成员")
        else:
            membership = memberships[0]
        request.state.principal = principal
        request.state.workspace_role = membership["role"]
        with paid_scope(membership["id"], principal.subject):
            yield membership["id"]

    def require_role(request: Request, *roles: str) -> None:
        if getattr(request.state, "workspace_role", "") not in roles:
            raise HTTPException(403, "当前工作空间角色没有此权限")

    async def researcher(request: Request, user=Depends(identity)):
        require_role(request, "admin", "researcher")
        return user

    async def administrator(request: Request, user=Depends(identity)):
        require_role(request, "admin")
        return user
    @app.exception_handler(InvalidDocument)
    async def invalid_document(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({"detail": str(exc), "error": exc.detail()}, status_code=503 if exc.retryable else 422)

    @app.exception_handler(ConflictError)
    async def conflict_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(LookupError)
    async def lookup_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.post("/internal/alerts")
    async def forward_alert(payload: dict, authorization: str = Header("")):
        expected = app.state.alert_relay_token
        supplied = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
        if not expected:
            raise HTTPException(503, "告警转发令牌未配置")
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(401, "告警来源未授权")
        events = app.state.alert_relay.pending(payload)
        if not events:
            ALERT_SUPPRESSED.inc()
            return {"delivered": False, "reason": "deduplicated"}
        webhook = settings.feishu_webhook_url.get_secret_value()
        if not webhook:
            ALERT_DELIVERIES.labels("disabled").inc()
            return {"delivered": False, "reason": "not_configured"}
        try:
            await deliver_with_retry(app.state.alert_sender, webhook, format_alert_message(events))
        except httpx.HTTPError as exc:
            ALERT_DELIVERIES.labels("failed").inc()
            raise HTTPException(502, "alert delivery failed") from exc
        app.state.alert_relay.mark_delivered(events)
        ALERT_DELIVERIES.labels("succeeded").inc()
        return {"delivered": True, "count": len(events), "statuses": sorted({event["status"] for event in events})}

    def require_internal(authorization: str) -> None:
        expected = app.state.internal_service_token
        supplied = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
        if not expected:
            raise HTTPException(503, "内部服务令牌未配置")
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(401, "内部服务调用未授权")

    @app.post("/internal/agent/runs/{run_id}/schedule", status_code=202)
    async def schedule_existing_run(run_id: str, payload: dict, authorization: str = Header(""), runtime=Depends(rt)):
        """Idempotent command adapter used only by the Java business outbox."""
        require_internal(authorization)
        run = await runtime.db.one("SELECT id,status FROM runs WHERE id=?", (run_id,))
        if not run:
            raise HTTPException(404, "任务不存在")
        if run["status"] != "queued":
            return {"accepted": False, "status": run["status"]}
        try:
            return await runtime.accept_schedule(run_id, payload)
        except ValueError:
            raise HTTPException(422, "研究命令合同无效或执行版本不匹配") from None

    @app.post("/internal/agent/runs/{run_id}/abort", status_code=202)
    async def abort_existing_run(run_id: str, authorization: str = Header(""), runtime=Depends(rt)):
        """Best-effort process/queue abort; Java has already made cancellation durable."""
        require_internal(authorization)
        run = await runtime.db.one("SELECT id,status,attempt_count FROM runs WHERE id=?", (run_id,))
        if not run:
            raise HTTPException(404, "任务不存在")
        if run["status"] != "cancelled":
            return {"accepted": False, "status": run["status"]}
        # Outbox delivery can arrive after resume. Abort only an attempt that
        # actually started; attempt zero would target the future first job.
        if not run.get("attempt_count"):
            return {"accepted": True}
        task = runtime.tasks.get(run_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if runtime.queue:
            from arq.jobs import Job
            try:
                await asyncio.wait_for(
                    Job(runtime.run_job_id(run_id, int(run.get("attempt_count") or 0), next_attempt=False), runtime.queue).abort(),
                    1,
                )
            except Exception:
                pass
        return {"accepted": True}
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

    @app.get("/api/capabilities")
    async def capabilities(runtime=Depends(rt), user=Depends(identity)):
        return await runtime.capabilities()

    @app.get("/api/workspaces")
    async def workspaces(request: Request, runtime=Depends(rt), user=Depends(identity)):
        return await runtime.db.memberships(request.state.principal.subject)

    @app.post("/api/workspaces", status_code=201)
    async def create_workspace(body: WorkspaceRequest, request: Request, runtime=Depends(rt), user=Depends(identity)):
        workspace = await runtime.db.create_workspace(
            request.state.principal.subject, body.name,
            (0, settings.workspace_concurrent_run_limit))
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
        async with runtime.db.guard("workspace:" + user):
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
        await runtime.db.execute("""UPDATE workspace_limits SET daily_search_limit=0,concurrent_run_limit=?
                                 WHERE workspace_id=?""",
                                 (body.concurrent_run_limit, user))
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
        return Response(await runtime.export_user(user, request.state.principal.subject), media_type="application/zip",
                        headers={"Content-Disposition": "attachment; filename=deepresearch-export.zip"})

    @app.delete("/api/data")
    async def purge_data(scope: str, request: Request, x_confirm_delete: str = Header(""), runtime=Depends(rt), user=Depends(administrator)):
        if x_confirm_delete != "DELETE":
            raise HTTPException(400, "请使用 X-Confirm-Delete: DELETE 确认清理")
        try:
            async with runtime.db.guard("publication:" + user):
                result = await runtime.purge_user(user, scope, request.state.principal.subject)
            await runtime.db.audit(user, request.state.principal.subject, "data.purge", "scope", scope)
            return result
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/config/check")
    async def check(runtime=Depends(rt), user=Depends(identity)):
        """Static checks are safe for members and never contact a paid provider."""
        return {
            "mode": "static",
            "llm": "configured" if settings.llm_api_key.get_secret_value() else "missing LLM_API_KEY",
            "embedding": "configured" if settings.embedding_api_key.get_secret_value() else "missing EMBEDDING_API_KEY",
            "search": "configured" if (settings.bocha_api_key.get_secret_value() if settings.web_search_provider == "bocha"
                                        else settings.llm_api_key.get_secret_value()) else "missing search credential",
            "live_probe": "platform_operator_only",
        }

    @app.post("/api/config/probe")
    async def probe(request: Request, target: str = Query("llm", pattern="^(llm|embedding|milvus)$"),
                    runtime=Depends(rt), user=Depends(identity)):
        actor = request.state.principal.subject
        if actor not in settings.operator_subjects:
            raise HTTPException(403, "真实连接探测仅允许运维配置的主体执行")
        from ..domain.models import Route
        scope = UsageScope(user, actor, "probe")
        config = {"llm": (settings.llm_base_url, settings.llm_api_key.get_secret_value(), settings.llm_model_id),
                  "embedding": (settings.embedding_base_url, settings.embedding_api_key.get_secret_value(), settings.embedding_model),
                  "milvus": (settings.milvus_uri, "", "")}[target]
        probe_key = fingerprint([target, provider_account(config[0], config[1]), config[2]])
        governance = UsageGovernance(runtime.db, settings)
        try:
            claimed = await governance.claim_probe(probe_key, scope)
        except Exception:
            raise HTTPException(503, "探测冷却账本不可用，未发送外部请求") from None
        if not claimed:
            cached = await runtime.db.one("SELECT result,next_allowed FROM provider_probes WHERE id=?", (probe_key,))
            await runtime.db.audit(user, actor, "provider.probe.cached", "provider", target)
            return {"cached": True, "result": json.loads(cached["result"]) or {"status": "in_progress_or_interrupted"},
                    "next_allowed": cached["next_allowed"]}
        # Record intent before sending. An audit failure prevents the paid probe.
        await runtime.db.audit(user, actor, "provider.probe.started", "provider", target)
        result = {"target": target}
        with paid_scope(user, actor, "probe"):
            try:
                if target == "llm":
                    await runtime.providers.structured("router", "返回 mode=quick 和 reason=连接测试。",
                        {"topic": "连接测试"}, Route, "")
                elif target == "embedding":
                    vector = (await runtime.providers.embed(["连接测试"]))[0]
                    result["actual_embedding_dimension"] = len(vector)
                    await runtime.vectors.fingerprint()
                else:
                    await runtime.vectors.ping()
                result["status"] = "ok"
            except ServiceError as exc:
                result.update(status="failed", code=exc.code)
            except Exception:
                result.update(status="failed", code="probe_failed")
        await runtime.db.execute("UPDATE provider_probes SET result=? WHERE id=?", (json.dumps(result), probe_key))
        await runtime.db.audit(user, actor, "provider.probe.finished", "provider", target, result["status"])
        return {"cached": False, "result": result}

    @app.post("/api/threads", status_code=201)
    async def create_thread(body: ThreadRequest, runtime=Depends(rt), user=Depends(researcher)):
        return await runtime.new_thread(user, body.title)

    @app.get("/api/threads")
    async def threads(runtime=Depends(rt), user=Depends(identity)):
        return await runtime.db.rows("SELECT * FROM threads WHERE user_id=? ORDER BY created_at DESC", (user,))

    @app.patch("/api/threads/{thread_id}")
    async def rename_thread(thread_id: str, body: ThreadRequest, runtime=Depends(rt), user=Depends(researcher)):
        thread = await owned_thread(runtime, thread_id, user)
        if not await runtime.db.execute("UPDATE threads SET title=? WHERE id=? AND user_id=?", (body.title, thread["id"], user)):
            raise HTTPException(404, "会话不存在")
        return {"ok": True}

    @app.delete("/api/threads/{thread_id}")
    async def delete_thread(thread_id: str, request: Request, runtime=Depends(rt), user=Depends(administrator)):
        try:
            async with runtime.db.guard("publication:" + user):
                result = await runtime.delete_thread(thread_id, user)
            await runtime.db.audit(user, request.state.principal.subject, "thread.delete", "thread", thread_id)
            return result
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.get("/api/threads/{thread_id}/runs")
    async def thread_runs(thread_id: str, runtime=Depends(rt), user=Depends(identity)):
        thread = await owned_thread(runtime, thread_id, user)
        return await visible_thread_runs(runtime, thread["id"], user)
    @app.get("/api/threads/{thread_id}/report")
    async def thread_report(thread_id: str, runtime=Depends(rt), user=Depends(identity)):
        thread = await owned_thread(runtime, thread_id, user)
        runs = await visible_thread_runs(runtime, thread["id"], user)
        parts = [f"# {thread['title'] or '研究会话'}", "", "本文件包含该会话中的全部研究记录。"]
        for index, run in enumerate(runs, start=1):
            parts.extend(["", f"## {index}. {run['topic']}", "", f"- 状态：{run['status']}", f"- 创建时间：{run['created_at']}", "", run["report"] or "_此研究尚未生成 Markdown 报告。_"])
        return PlainTextResponse("\n".join(parts) + "\n", media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="conversation-{thread["id"]}.md"'})

    @app.post("/api/research/runs", status_code=202)
    async def start_run(body: RunRequest, request: Request, runtime=Depends(rt), user=Depends(researcher)):
        run = await runtime.create(user, body, request.state.principal.subject)
        await runtime.db.audit(user, request.state.principal.subject, "research.create", "run", run["id"])
        return run

    @app.get("/api/research/runs/{run_id}")
    async def get_run(run_id: str, runtime=Depends(rt), user=Depends(identity)):
        return await owned_run(runtime, run_id, user)

    @app.get("/api/research/runs/{run_id}/report")
    async def report(run_id: str, runtime=Depends(rt), user=Depends(identity)):
        run = await owned_run(runtime, run_id, user)
        return PlainTextResponse(run["report"], media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="research-{run_id}.md"'})

    @app.post("/api/research/runs/{run_id}/cancel")
    async def cancel(run_id: str, request: Request, runtime=Depends(rt), user=Depends(researcher)):
        await owned_run(runtime, run_id, user)
        run = await runtime.cancel(run_id, user)
        await runtime.db.audit(user, request.state.principal.subject, "research.cancel", "run", run_id)
        return run

    @app.post("/api/research/runs/{run_id}/resume", status_code=202)
    async def resume(run_id: str, runtime=Depends(rt), user=Depends(researcher)):
        await owned_run(runtime, run_id, user)
        return await runtime.resume(run_id, user)

    @app.post("/api/research/runs/{run_id}/memory", status_code=201)
    async def save_run_memory(run_id: str, runtime=Depends(rt), user=Depends(identity)):
        await owned_run(runtime, run_id, user)
        try:
            return await runtime.save_run_memory(run_id, user)
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/research/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = 0, last_event_id: str = Header("0"),
                     runtime=Depends(rt), user=Depends(identity)):
        await owned_run(runtime, run_id, user)
        try:
            cursor = max(after, int(last_event_id), 0)
        except ValueError:
            raise HTTPException(400, "事件序号无效") from None

        async def stream():
            nonlocal cursor
            ticks = 0
            while not await request.is_disconnected():
                membership = await runtime.db.membership(user, request.state.principal.subject)
                if not membership:
                    yield "event: close\ndata: {}\n\n"
                    return
                try:
                    await owned_run(runtime, run_id, user)
                except HTTPException:
                    yield "event: close\ndata: {}\n\n"
                    return
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

    @app.post("/api/attachments", status_code=201)
    async def upload_attachment(request: Request, file: UploadFile = File(), runtime=Depends(rt), user=Depends(researcher)):
        content = await read_upload(file)
        async with runtime.upload_lock:
            return await runtime.attachments.add(user, request.state.principal.subject, file.filename or "image", content)

    @app.get("/api/attachments/{image_id}")
    async def attachment_content(image_id: str, request: Request, runtime=Depends(rt), user=Depends(identity)):
        row = await runtime.attachments.visible(image_id, user, request.state.principal.subject)
        content = await runtime.documents.store.get("uploads", image_id)
        return Response(content, media_type=row["media_type"], headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.delete("/api/attachments/{image_id}")
    async def delete_attachment(image_id: str, request: Request, runtime=Depends(rt), user=Depends(researcher)):
        await runtime.attachments.remove(image_id, user, request.state.principal.subject)
        return {"ok": True}

    @app.post("/api/documents", status_code=202)
    async def upload(request: Request, file: UploadFile = File(), runtime=Depends(rt), user=Depends(researcher)):
        content = await read_upload(file)
        async with runtime.upload_lock:
            try:
                document = await runtime.upload_document(user, file.filename or "document", content)
            except ServiceError as exc:
                await runtime.db.audit(user, request.state.principal.subject, "document.upload", "document",
                                       str(getattr(exc, "document_id", "upload")), "rejected")
                raise
        await runtime.db.audit(user, request.state.principal.subject, "document.upload", "document",
                               document["id"], document["status"])
        return public_document(document)

    def public_document(row: dict) -> dict:
        return {key: row.get(key) for key in ("id", "name", "status", "error", "created_at", "updated_at",
                                                "index_version", "pending_version")}

    async def require_api_document(runtime, user: str, document_id: str, *, write=False):
        # Preserve the object's workspace/not-found contract before evaluating
        # current collection grants; an existing but revoked document still uses
        # the structured source_permission_denied error.
        if not await runtime.db.one("SELECT id FROM documents WHERE id=? AND user_id=? AND status!='deleted'",
                                    (document_id, user)):
            raise HTTPException(404, "资料不存在")
        await require_document(runtime.db, user, document_id, write=write)

    @app.get("/api/documents")
    async def documents(runtime=Depends(rt), user=Depends(identity)):
        rows = await runtime.db.rows(f"""SELECT d.id,d.name,d.status,d.error,d.created_at,d.updated_at,d.index_version,d.pending_version
            FROM documents d WHERE d.user_id=? AND d.status!='deleted' AND {access_sql()} ORDER BY d.created_at DESC""", (user, *access_args(user)))
        return [public_document(row) for row in rows]

    @app.get("/api/documents/capacity")
    async def document_capacity(runtime=Depends(rt), user=Depends(identity)):
        return await runtime.documents.capacity(user)

    @app.put("/api/documents/{document_id}", status_code=202)
    async def replace_document(document_id: str, request: Request, file: UploadFile = File(),
                               runtime=Depends(rt), user=Depends(researcher)):
        await require_api_document(runtime, user, document_id, write=True)
        content = await read_upload(file)
        try:
            document = await runtime.replace_document(document_id, user, file.filename or "document", content)
        except LookupError as exc:
            await runtime.db.audit(user, request.state.principal.subject, "document.replace", "document", document_id, "rejected")
            raise HTTPException(404, str(exc)) from None
        except ServiceError as exc:
            await runtime.db.audit(user, request.state.principal.subject, "document.replace", "document", document_id, "rejected")
            if exc.code in {"document_busy", "document_unchanged", "document_duplicate", "source_changed"}:
                raise HTTPException(409, str(exc)) from None
            raise
        await runtime.db.audit(user, request.state.principal.subject, "document.replace", "document", document_id, "rebuilding")
        return public_document(document)

    @app.get("/api/documents/{document_id}/versions")
    async def document_versions(document_id: str, runtime=Depends(rt), user=Depends(identity)):
        await require_api_document(runtime, user, document_id)
        try:
            return await runtime.documents.lifecycle.versions(document_id, user)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.get("/api/documents/{document_id}/diff")
    async def document_diff(document_id: str, from_version: int = Query(alias="from", ge=1),
                            to_version: int = Query(alias="to", ge=1), runtime=Depends(rt), user=Depends(identity)):
        await require_api_document(runtime, user, document_id)
        try:
            return await runtime.documents.lifecycle.diff(document_id, user, from_version, to_version)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.get("/api/documents/{document_id}/chunks")
    async def document_chunks(document_id: str, limit: int = 100, runtime=Depends(rt), user=Depends(identity)):
        await require_api_document(runtime, user, document_id)
        limit = max(1, min(limit, 400))
        return await runtime.db.rows("SELECT id,locator,text,ordinal FROM chunks WHERE document_id=? AND user_id=? ORDER BY ordinal,id LIMIT ?",
                                     (document_id, user, limit))

    @app.post("/api/documents/search")
    async def search_documents(body: DocumentSearchRequest, diagnostics: bool = False, runtime=Depends(rt), user=Depends(identity)):
        results = await runtime.documents.search(user, [body.query], limit=body.limit, rerank_query=body.query)
        return {"results": results, "reasons": getattr(results, "reasons", [])} if diagnostics else results

    @app.post("/api/documents/{document_id}/reindex", status_code=202)
    async def reindex_document(document_id: str, request: Request, runtime=Depends(rt), user=Depends(researcher)):
        await require_api_document(runtime, user, document_id, write=True)
        try:
            document = await runtime.reindex_document(document_id, user)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        await runtime.db.audit(user, request.state.principal.subject, "document.reindex", "document", document_id)
        return public_document(document)

    @app.delete("/api/documents/{document_id}")
    async def delete_document(document_id: str, request: Request, runtime=Depends(rt), user=Depends(administrator)):
        if not await runtime.db.one("SELECT id FROM documents WHERE id=? AND user_id=?", (document_id, user)):
            raise HTTPException(404, "资料不存在")
        await runtime.documents.delete(document_id, user)
        await runtime.db.audit(user, request.state.principal.subject, "document.delete", "document", document_id)
        return {"ok": True}

    @app.get("/api/memories")
    async def memories(request: Request, runtime=Depends(rt), user=Depends(identity)):
        rows = await runtime.db.rows("""SELECT id,kind,content,created_at,run_id FROM memories
            WHERE (kind IN ('profile','preference') AND owner_subject=?)
               OR (kind='semantic' AND user_id=?) ORDER BY created_at DESC""",
                                     (request.state.principal.subject, user))
        visible = []
        for row in rows:
            if row["kind"] == "semantic":
                try:
                    await owned_run(runtime, row["run_id"], user)
                except HTTPException as error:
                    if error.status_code != 404:
                        raise
                    continue
            row.pop("run_id", None)
            visible.append(row)
        return visible

    @app.post("/api/memories", status_code=201)
    async def add_memory(body: MemoryRequest, request: Request, runtime=Depends(rt), user=Depends(researcher)):
        memory_id = uid()
        await runtime.db.execute("""INSERT INTO memories(
            id,user_id,kind,content,run_id,created_at,owner_subject) VALUES(?,?,'preference',?,?,?,?)""",
            (memory_id, user, body.content, memory_id, now(), request.state.principal.subject))
        return {"id": memory_id, "content": body.content}

    @app.put("/api/memories/{memory_id}")
    async def edit_memory(memory_id: str, body: MemoryRequest, request: Request,
                          runtime=Depends(rt), user=Depends(researcher)):
        if not await runtime.db.execute("""UPDATE memories SET content=?
            WHERE id=? AND owner_subject=? AND kind='preference'""",
                                         (body.content, memory_id, request.state.principal.subject)):
            raise HTTPException(404, "可编辑的用户偏好不存在")
        return {"ok": True}

    @app.delete("/api/memories/{memory_id}")
    async def delete_memory(memory_id: str, request: Request, runtime=Depends(rt), user=Depends(researcher)):
        memory = await runtime.db.one("""SELECT * FROM memories WHERE id=? AND (
            (kind IN ('profile','preference') AND owner_subject=?) OR (kind='semantic' AND user_id=?))""",
                                      (memory_id, request.state.principal.subject, user))
        if not memory:
            raise HTTPException(404, "记忆不存在")
        if memory["kind"] == "semantic":
            await owned_run(runtime, memory["run_id"], user)
        await runtime.db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        if memory["kind"] == "semantic":
            await runtime.vectors.delete("memories", user, "id", memory_id)
        return {"ok": True}

    from .source_rechecks import register_source_rechecks
    register_source_rechecks(app, rt, identity, researcher)
    from .usage import register_usage
    register_usage(app, rt, identity)
    return app
