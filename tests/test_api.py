import asyncio
import json

import httpx
import pytest
from backend.core.auth import issue_development_token

from backend.api.app import create_app
from backend.core.db import uid


@pytest.fixture
async def app_client(settings):
    app = create_app(settings)
    async def attach_test_identity(request):
        request.headers["Authorization"] = "Bearer " + issue_development_token(settings, request.headers.get("X-User-ID", "alice"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", event_hooks={"request": [attach_test_identity]}) as client:
            yield app, client


async def test_api_isolation_sse_replay_and_download(app_client):
    app, client = app_client
    response = await client.post('/api/research/runs',json={"topic":"研究流程","client_request_id":uid()})
    assert response.status_code == 202
    run = response.json()
    await app.state.runtime.tasks[run['id']]
    result = (await client.get(f"/api/research/runs/{run['id']}")).json()
    assert result['status'] == 'completed'
    assert (await client.get(f"/api/research/runs/{run['id']}",headers={"X-User-ID":"bob"})).status_code == 404
    assert (await client.post(f"/api/research/runs/{run['id']}/cancel",headers={"X-User-ID":"bob"})).status_code == 404
    stream = await client.get(f"/api/research/runs/{run['id']}/events")
    ids = [int(line[4:]) for line in stream.text.splitlines() if line.startswith('id: ')]
    assert len(ids) == len(set(ids)) and ids == sorted(ids)
    replay = await client.get(f"/api/research/runs/{run['id']}/events",headers={"Last-Event-ID":str(ids[-1])})
    assert 'id:' not in replay.text and 'event: close' in replay.text
    report = await client.get(f"/api/research/runs/{run['id']}/report")
    assert report.text == result['report']
    assert 'attachment' in report.headers['content-disposition']


async def test_delete_thread_removes_its_runs_events_and_checkpoints(app_client):
    app, client = app_client
    run = (await client.post('/api/research/runs', json={"topic":"删除会话测试", "client_request_id":uid()})).json()
    await app.state.runtime.tasks[run['id']]
    await app.state.runtime.save_run_memory(run['id'], 'alice')
    assert await app.state.runtime.db.one("SELECT id FROM memories WHERE run_id=?", (run['id'],))

    assert (await client.delete(f"/api/threads/{run['thread_id']}", headers={"X-User-ID":"bob"})).status_code == 404
    response = await client.delete(f"/api/threads/{run['thread_id']}")
    assert response.status_code == 200
    assert response.json()['deleted_runs'] == 1
    assert (await client.get('/api/threads')).json() == []
    assert (await client.get(f"/api/research/runs/{run['id']}")).status_code == 404
    assert not await app.state.runtime.db.rows("SELECT * FROM events WHERE run_id=?", (run['id'],))
    assert await app.state.runtime.db.one("SELECT id FROM memories WHERE run_id=?", (run['id'],))
    snapshot = await app.state.runtime.graph.aget_state({"configurable": {"thread_id": run['id']}})
    assert not snapshot.values


async def test_report_purge_removes_runs_events_dead_letters_and_checkpoints(app_client):
    app, client = app_client
    run = (await client.post('/api/research/runs', json={
        "topic": "完整清理检查点测试", "client_request_id": uid()})).json()
    await app.state.runtime.tasks[run['id']]
    config = {"configurable": {"thread_id": run['id']}}
    assert (await app.state.runtime.graph.aget_state(config)).values
    await app.state.runtime.db.execute("""INSERT INTO dead_letter_runs(
        run_id,user_id,category,message,failed_at,recovered_at) VALUES(?,?,?,?,?,?)""",
        (run['id'], 'alice', 'test', 'test', 'today', ''))

    response = await client.delete('/api/data?scope=reports', headers={"X-Confirm-Delete": "DELETE"})

    assert response.status_code == 200
    assert not await app.state.runtime.db.one("SELECT id FROM runs WHERE id=?", (run['id'],))
    assert not await app.state.runtime.db.one("SELECT run_id FROM dead_letter_runs WHERE run_id=?", (run['id'],))
    assert not (await app.state.runtime.graph.aget_state(config)).values


async def test_thread_keys_are_short_per_user_and_resolve_in_routes(app_client):
    app, client = app_client
    first = (await client.post('/api/research/runs', json={"topic":"短会话标识", "client_request_id":uid()})).json()
    await app.state.runtime.tasks[first['id']]
    threads = (await client.get('/api/threads')).json()
    assert threads[0]['thread_key'] == 'thread01'
    assert len((await client.get('/api/threads/thread01/runs')).json()) == 1

    second = (await client.post('/api/research/runs', json={"topic":"另一用户", "client_request_id":uid()}, headers={"X-User-ID":"user01"})).json()
    await app.state.runtime.tasks[second['id']]
    other = (await client.get('/api/threads', headers={"X-User-ID":"user01"})).json()
    assert other[0]['thread_key'] == 'thread01'
    assert (await client.get('/api/threads/thread01/runs', headers={"X-User-ID":"user01"})).status_code == 200


async def test_document_import_retrieval_deletion(app_client):
    app, client = app_client
    response = await client.post('/api/documents',files={"file":("sample.md","这是本地资料。研究报告需要证据核查。".encode(),"text/markdown")})
    assert response.status_code == 202
    doc_id = response.json()['id']
    for _ in range(30):
        docs = (await client.get('/api/documents')).json()
        if docs and docs[0]['status'] != 'indexing':
            break
        await asyncio.sleep(.02)
    assert docs[0]['status'] == 'ready'
    chunks = (await client.get(f'/api/documents/{doc_id}/chunks')).json()
    assert chunks and chunks[0]['locator']
    search_response = await client.post('/api/documents/search', json={'query': '证据核查'})
    assert search_response.status_code == 200, search_response.text
    matches = search_response.json()
    assert matches and {'score', 'vector_score', 'bm25_score'} <= set(matches[0])
    assert (await client.post(f'/api/documents/{doc_id}/reindex')).status_code == 202
    for _ in range(30):
        docs = (await client.get('/api/documents')).json()
        if docs[0]['status'] != 'indexing':
            break
        await asyncio.sleep(.02)
    assert docs[0]['status'] == 'ready'
    assert (await client.get('/api/documents',headers={"X-User-ID":"bob"})).json() == []
    assert (await client.delete(f'/api/documents/{doc_id}',headers={"X-User-ID":"bob"})).status_code == 404
    rt = app.state.runtime
    vector = (await rt.providers.embed(['证据核查']))[0]
    assert await rt.vectors.search('documents','alice',vector)
    assert not await rt.vectors.search('documents','bob',vector)
    assert (await client.delete(f'/api/documents/{doc_id}')).status_code == 200
    assert not await rt.vectors.search('documents','alice',vector)
    assert not (rt.settings.data_dir / 'uploads' / doc_id).exists()


async def test_preferences_crud_and_isolation(app_client):
    app, client = app_client
    first = (await client.post('/api/memories',json={"content":"关注中国市场"})).json()
    second = await client.post('/api/memories',json={"content":"中文报告"})
    assert second.status_code == 201
    assert (await client.get('/api/memories',headers={"X-User-ID":"bob"})).json() == []
    assert (await client.put(f"/api/memories/{first['id']}",json={"content":"关注全球市场"})).status_code == 200
    assert (await client.delete(f"/api/memories/{first['id']}")).status_code == 200
    contents = json.dumps((await client.get('/api/memories')).json(),ensure_ascii=False)
    assert '全球市场' not in contents and '中文报告' in contents


async def test_status_never_exposes_keys(app_client):
    _, client = app_client
    response = await client.get('/api/status')
    assert response.status_code == 200
    assert 'api_key' not in response.text.lower()


async def test_api_requires_a_jwt(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            assert (await client.get('/api/threads')).status_code == 401
            token = issue_development_token(settings, 'alice')
            assert (await client.get('/api/threads',headers={'Authorization':'Bearer ' + token})).status_code == 200


async def test_runtime_never_exposes_development_login(api_client):
    _, client = api_client
    assert (await client.post("/api/auth/development/login")).status_code == 404

async def test_oidc_mode_exposes_issuer_but_refuses_development_auto_login(tmp_path):
    from backend.core.config import Settings

    settings = Settings(
        _env_file=None, demo_mode=True, data_dir=tmp_path, auth_mode="oidc", queue_backend="local", document_scan_mode="disabled",
        oidc_issuer="http://localhost:8180/realms/deepresearch",
        oidc_jwks_url="http://keycloak:8080/realms/deepresearch/protocol/openid-connect/certs",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            config = await client.get("/api/auth/config")
            assert config.json() == {"mode": "oidc", "issuer": settings.oidc_issuer}
            assert (await client.post("/api/auth/development/login")).status_code == 404

async def test_alert_forwarding_is_disabled_without_webhook(settings):
    from pydantic import SecretStr

    settings.alert_relay_token = SecretStr("relay-test-token")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/internal/alerts", headers={"Authorization": "Bearer relay-test-token"},
                                         json={"alerts": [{"annotations": {"summary": "test"}}]})
    assert response.status_code == 200
    assert response.json() == {"delivered": False, "reason": "not_configured"}
