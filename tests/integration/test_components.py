"""Real adapters and networked services; model/search are explicit fixtures."""
import asyncio
import os
import json
import httpx
import pytest

pytestmark = pytest.mark.skipif(os.getenv('INTEGRATION') != '1', reason='Run isolated compose.verify.yaml')


@pytest.fixture
async def component():
    from scripts.component_fixture import install
    from backend.services.runtime import Runtime
    from backend.core.config import Settings
    install()
    rt = await Runtime(Settings(), worker_mode=True).start()
    try:
        yield rt
    finally:
        await rt.close()


async def test_postgres_migration_idempotency_quota(component, monkeypatch):
    from backend.core.db import uid
    from backend.domain.models import RunRequest
    from backend.services.runtime import ConflictError
    user = uid()
    async def hold(*a, **k):
        pass
    monkeypatch.setattr(component, 'schedule', hold)
    await component.db.ensure_personal_workspace(user, 'Integration', (3, 1))
    request = RunRequest(topic='Integration', client_request_id=uid())
    rows = await asyncio.gather(*(component.create(user, request) for _ in range(8)))
    assert len({r['id'] for r in rows}) == 1
    with pytest.raises(ConflictError):
        await component.create(user, request.model_copy(update={'client_request_id': uid()}))
    assert sum(await asyncio.gather(*(component.db.reserve_search(rows[0]['id'], 2) for _ in range(8)))) == 2
    assert (await component.db.one('SELECT search_calls FROM workspace_daily_usage WHERE workspace_id=?', (user,)))['search_calls'] == 2
    await component.purge_user(user, 'reports')
    assert (await component.db.one('SELECT search_calls FROM workspace_daily_usage WHERE workspace_id=?', (user,)))['search_calls'] == 2
    assert (await component.db.one('SELECT version_num FROM alembic_version'))['version_num'] == '0004_consistency'


async def test_real_scan_index_isolation_and_delete_race(component, monkeypatch):
    from backend.core.db import uid
    from backend.core.clamav import ScanRejected
    user, other = uid(), uid()
    doc = await component.documents.add(user, 'evidence.txt', b'Original research evidence on reproducible source validation.')
    await component.documents.ingest(doc['id'])
    assert await component.documents.search(user, ['source validation'])
    assert not await component.documents.search(other, ['source validation'])
    assert await component.documents.store.exists('uploads', doc['id'])
    await component.documents.reindex(doc['id'], user)
    entered, release = asyncio.Event(), asyncio.Event()
    original = component.providers.embed
    async def paused(texts):
        entered.set()
        await release.wait()
        return await original(texts)
    monkeypatch.setattr(component.providers, 'embed', paused)
    task = asyncio.create_task(component.documents.ingest(doc['id']))
    await asyncio.wait_for(entered.wait(), 15)
    await component.documents.delete(doc['id'], user)
    release.set()
    await task
    assert not await component.documents.search(user, ['source validation'])
    assert not await component.documents.store.exists('uploads', doc['id'])
    client, collection = component.vectors.ensure('documents')
    assert not await asyncio.to_thread(client.query, collection_name=collection, filter=f'document_id == {json.dumps(doc["id"])}')
    eicar = b'X5O!P%@AP[4' + b'\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'
    with pytest.raises(ScanRejected):
        await component.documents.add(user, 'eicar.txt', eicar)


async def test_api_worker_sse_cancel_and_roles(component):
    from backend.core.db import uid
    from backend.core.auth import issue_development_token
    user, viewer = uid(), uid()
    await component.db.ensure_personal_workspace(user, 'API integration', (100, 3))
    await component.db.upsert_membership(user, viewer, 'viewer')
    headers = {'Authorization': 'Bearer ' + issue_development_token(component.settings, user)}
    async with httpx.AsyncClient(base_url='http://api:8000', headers=headers, timeout=120) as client:
        response = await client.post('/api/research/runs', json={'topic': 'research workflow', 'client_request_id': uid()})
        assert response.status_code == 202
        run_id = response.json()['id']
        events = await client.get(f'/api/research/runs/{run_id}/events')
        assert '"type": "done"' in events.text
        result = (await client.get(f'/api/research/runs/{run_id}')).json()
        assert result['status'] == 'completed' and result['sources']
        assert len(await component.db.rows("SELECT id FROM events WHERE run_id=? AND type='done'", (run_id,))) == 1
        vheaders = {'Authorization': 'Bearer ' + issue_development_token(component.settings, viewer)}
        assert (await client.post('/api/research/runs', json={'topic': 'forbidden', 'client_request_id': uid()}, headers=vheaders)).status_code == 403
        response = await client.post('/api/research/runs', json={'topic': 'slow-recovery cancellation', 'client_request_id': uid()})
        cancel_id = response.json()['id']
        assert (await client.post(f'/api/research/runs/{cancel_id}/cancel')).status_code == 200
        await asyncio.sleep(1)
        assert (await client.get(f'/api/research/runs/{cancel_id}')).json()['status'] == 'cancelled'


async def test_upgrade_preserves_legacy_documents_and_consumption(component):
    import asyncpg
    import subprocess
    from backend.core.db import uid
    name = 'migration_' + uid()
    admin = await asyncpg.connect(component.settings.database_url)
    await admin.execute(f'CREATE DATABASE {name}')
    dsn = component.settings.database_url.rsplit('/', 1)[0] + '/' + name
    env = dict(os.environ, DATABASE_URL=dsn.replace('postgresql://', 'postgresql+asyncpg://'))
    try:
        await asyncio.to_thread(subprocess.run, ['alembic', 'upgrade', '0003_personal_memory_ownership'], env=env, check=True, capture_output=True)
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("INSERT INTO documents(id,user_id,name,hash,status) VALUES('old','u','legacy','hash','ready')")
            await conn.execute("INSERT INTO runs(id,user_id,thread_id,created_at) VALUES('r','u','t','2026-09-10T00:00:00+00:00')")
            await conn.execute("INSERT INTO counters(run_id,search_calls) VALUES('r',2)")
        finally:
            await conn.close()
        await asyncio.to_thread(subprocess.run, ['alembic', 'upgrade', 'head'], env=env, check=True, capture_output=True)
        conn = await asyncpg.connect(dsn)
        try:
            assert await conn.fetchval("SELECT index_version FROM documents WHERE id='old'") == 0
            assert await conn.fetchval("SELECT search_calls FROM workspace_daily_usage WHERE workspace_id='u'") == 2
        finally:
            await conn.close()
    finally:
        await admin.execute(f'DROP DATABASE {name} WITH (FORCE)')
        await admin.close()
