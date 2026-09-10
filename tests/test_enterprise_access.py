import asyncio

import httpx
import pytest

from backend.api.app import create_app
from backend.core.auth import issue_development_token
from backend.core.db import uid


def bearer(settings, subject: str, **headers) -> dict:
    return {"Authorization": "Bearer " + issue_development_token(settings, subject), **headers}


@pytest.mark.asyncio
async def test_first_authenticated_requests_bootstrap_one_workspace_idempotently(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = bearer(settings, "new-user")
            responses = await asyncio.gather(*[client.get("/api/threads", headers=headers) for _ in range(12)])
            assert [response.status_code for response in responses] == [200] * 12
            assert len(await app.state.runtime.db.memberships("new-user")) == 1


@pytest.mark.asyncio
async def test_jwt_workspace_roles_and_audit(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            alice = bearer(settings, "alice")
            assert (await client.get("/api/threads", headers=alice)).status_code == 200
            member = await client.put("/api/workspaces/alice/members",
                                      headers=alice, json={"subject": "bob", "role": "viewer"})
            assert member.status_code == 200

            bob = bearer(settings, "bob", **{"X-Workspace-ID": "alice"})
            assert (await client.get("/api/threads", headers=bob)).status_code == 200
            assert (await client.post("/api/research/runs", headers=bob,
                                      json={"topic": "viewer cannot run", "client_request_id": uid()})).status_code == 403
            assert (await client.get("/api/data/export", headers=bob)).status_code == 403

            run = await client.post("/api/research/runs", headers=alice,
                                    json={"topic": "admin research", "client_request_id": uid()})
            assert run.status_code == 202
            await app.state.runtime.tasks[run.json()["id"]]
            audit = await client.get("/api/workspaces/alice/audit", headers=alice)
            actions = [row["action"] for row in audit.json()]
            assert "membership.upsert" in actions and "research.create" in actions


@pytest.mark.asyncio
async def test_personal_preferences_are_private_inside_shared_workspace(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            alice = bearer(settings, "alice")
            assert (await client.get("/api/threads", headers=alice)).status_code == 200
            assert (await client.put("/api/workspaces/alice/members", headers=alice,
                                     json={"subject": "bob", "role": "researcher"})).status_code == 200
            alice_preference = (await client.post("/api/memories", headers=alice,
                                                  json={"content": "Alice 只关注中国市场"})).json()

            bob = bearer(settings, "bob", **{"X-Workspace-ID": "alice"})
            assert (await client.get("/api/memories", headers=bob)).json() == []
            bob_preference = (await client.post("/api/memories", headers=bob,
                                                json={"content": "Bob 只关注欧洲市场"})).json()
            assert [row["id"] for row in (await client.get("/api/memories", headers=bob)).json()] == [bob_preference["id"]]
            assert [row["id"] for row in (await client.get("/api/memories", headers=alice)).json()] == [alice_preference["id"]]
            assert (await client.put(f"/api/memories/{alice_preference['id']}", headers=bob,
                                     json={"content": "越权修改"})).status_code == 404

            run = await client.post("/api/research/runs", headers=bob,
                                    json={"topic": "Bob 的研究", "client_request_id": uid()})
            assert run.status_code == 202
            run_id = run.json()["id"]
            await app.state.runtime.tasks[run_id]
            saved = await app.state.runtime.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
            assert saved["created_by"] == "bob"
            context = await app.state.runtime.context(saved)
            assert "Bob 只关注欧洲市场" in context
            assert "Alice 只关注中国市场" not in context


@pytest.mark.asyncio
async def test_workspace_limits_do_not_expose_or_enforce_a_token_budget(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            alice = bearer(settings, "alice")
            response = await client.put("/api/workspaces/alice/limits", headers=alice,
                                        json={"daily_search_limit": 7, "concurrent_run_limit": 1,
                                              "daily_token_limit": 1})
            assert response.status_code == 200
            assert response.json() == {"daily_search_limit": 7, "concurrent_run_limit": 1}
