import httpx
import pytest

from backend.api.app import create_app
from backend.core.auth import issue_development_token
from backend.core.db import uid


def bearer(settings, subject: str, **headers) -> dict:
    return {"Authorization": "Bearer " + issue_development_token(settings, subject), **headers}


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
