from unittest.mock import AsyncMock

import httpx
import pytest

from backend.api.app import create_app
from backend.core.db import now, uid


@pytest.mark.asyncio
async def test_java_outbox_schedule_endpoint_is_authenticated_and_idempotent(settings):
    from pydantic import SecretStr

    settings.internal_service_token = SecretStr("java-agent-contract-token")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        thread_id, run_id, stamp = uid(), uid(), now()
        await runtime.db.execute(
            "INSERT INTO threads(id,user_id,title,thread_key,created_at) VALUES(?,?,?,?,?)",
            (thread_id, "alice", "Java 调度", "thread01", stamp),
        )
        await runtime.db.execute(
            """INSERT INTO runs(id,user_id,thread_id,topic,mode,status,created_at,updated_at,
               client_request_id,created_by,data_policy) VALUES(?,?,?,?,?,'queued',?,?,?,?,?)""",
            (run_id, "alice", thread_id, "边界测试", "auto", stamp, stamp, uid(), "alice", "public"),
        )
        runtime.schedule = AsyncMock()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            denied = await client.post(f"/internal/agent/runs/{run_id}/schedule", json={"resume": False})
            accepted = await client.post(
                f"/internal/agent/runs/{run_id}/schedule",
                headers={"Authorization": "Bearer java-agent-contract-token"},
                json={"resume": False},
            )
    assert denied.status_code == 401
    assert accepted.status_code == 202
    runtime.schedule.assert_awaited_once_with(run_id, resume=False)
