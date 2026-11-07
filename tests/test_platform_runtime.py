import asyncio
import time
from unittest.mock import AsyncMock, Mock

import httpx
import jwt
import pytest
from pydantic import SecretStr

from backend.api.app import create_app
from backend.core.auth import Principal, issue_development_token
from backend.core.db import now, uid
from backend.core.governance import register_account, require_current_account
from backend.core.reliability import Execution, ServiceError
from backend.core.usage_governance import UsageGovernance, paid_scope
from backend.services.runtime import Runtime


async def seed_run(runtime, workspace='alice'):
    thread_id, run_id, stamp = uid(), uid(), now()
    await runtime.db.execute('INSERT INTO threads(id,user_id,title,created_at) VALUES(?,?,?,?)',
                             (thread_id, workspace, 'Fixture', stamp))
    await runtime.db.execute('''INSERT INTO runs(id,user_id,thread_id,topic,mode,status,created_at,updated_at,
        client_request_id,created_by,data_policy) VALUES(?,?,?,?,?,'queued',?,?,?,?,?)''',
        (run_id, workspace, thread_id, 'Fixture', 'research', stamp, stamp, uid(), workspace, 'public'))
    return run_id


async def test_disabled_account_cannot_bootstrap_workspace_and_restore_does_not_revive_old_token(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        db = app.state.runtime.db
        await register_account(db, Principal('alice', 'Alice', int(time.time())))
        await db.execute("UPDATE platform_users SET status='disabled' WHERE subject='alice'")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            headers = {'Authorization': 'Bearer ' + issue_development_token(settings, 'alice')}
            assert (await client.get('/api/threads', headers=headers)).status_code == 403
            assert not await db.memberships('alice')
            cutoff = int(time.time()) - 30
            await db.execute("UPDATE platform_users SET status='active',revoked_before=? WHERE subject='alice'", (cutoff,))
            old = jwt.encode({'sub': 'alice', 'aud': settings.oidc_audience, 'iat': cutoff - 1,
                              'exp': int(time.time()) + 300}, settings.development_jwt_secret.get_secret_value())
            assert (await client.get('/api/threads', headers={'Authorization': 'Bearer ' + old})).status_code == 401
            assert (await client.get('/api/threads', headers=headers)).status_code == 200


async def test_registry_insert_never_reenables_a_disabled_account(runtime):
    principal = Principal('alice', 'Alice', int(time.time()))
    await register_account(runtime.db, principal)
    await runtime.db.execute("UPDATE platform_users SET status='disabled',revoked_before=123 WHERE subject='alice'")
    await register_account(runtime.db, principal)
    with pytest.raises(ServiceError) as failure:
        await require_current_account(runtime.db, principal)
    assert failure.value.code == 'account_disabled'
    assert (await runtime.db.one("SELECT revoked_before FROM platform_users WHERE subject='alice'"))['revoked_before'] == 123


async def test_redis_outage_preserves_reads_but_rejects_authenticated_writes_and_outbox_ack(settings):
    settings.internal_service_token = SecretStr('fixture-internal-token')
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        headers = {'Authorization': 'Bearer ' + issue_development_token(settings, 'alice')}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/api/threads', headers=headers)).status_code == 200
            run_id = await seed_run(runtime)
            runtime.settings.queue_backend = 'redis'
            runtime.queue = Mock(ping=AsyncMock(side_effect=ConnectionError('fixture outage')), aclose=AsyncMock())
            assert (await client.get('/api/threads', headers=headers)).status_code == 200
            denied = await client.post('/api/threads', headers=headers, json={'title': 'must not be created'})
            assert denied.status_code == 503 and denied.json()['error']['code'] == 'queue_unavailable'
            response = await client.post(f'/internal/agent/runs/{run_id}/schedule',
                headers={'Authorization': 'Bearer fixture-internal-token'}, json={'resume': False})
            assert response.status_code == 503
            row = await runtime.db.one('SELECT status,attempt_count FROM runs WHERE id=?', (run_id,))
            assert row['status'] == 'queued' and row['attempt_count'] == 0


async def test_two_runtime_replicas_share_global_claim_capacity_and_defer_without_spending(runtime, settings):
    replica = await Runtime(settings).start()
    try:
        ids = [await seed_run(runtime, 'workspace-' + str(index)) for index in range(7)]
        results = await asyncio.gather(*[(runtime if index % 2 else replica).claim_run(run_id)
                                         for index, run_id in enumerate(ids)])
        assert sum(result is not None for result in results) == settings.platform_active_run_limit == 5
        rows = await runtime.db.rows('SELECT id,status,attempt_count,call_attempts,reserved_tokens,deadline_at FROM runs')
        deferred = [row for row in rows if row['status'] == 'queued']
        assert len(deferred) == 2
        assert all(row['attempt_count'] == row['call_attempts'] == row['reserved_tokens'] == row['deadline_at'] == 0
                   for row in deferred)
        await runtime.db.execute("UPDATE runs SET status='completed' WHERE status='running'")
        assert await replica.claim_run(deferred[0]['id']) is not None
    finally:
        await replica.close()


async def test_workspace_claim_limit_applies_across_replicas(runtime):
    await runtime.db.ensure_personal_workspace('alice', 'Alice', (0, 2))
    ids = [await seed_run(runtime) for _ in range(3)]
    results = await asyncio.gather(*(runtime.claim_run(run_id) for run_id in ids))
    assert sum(result is not None for result in results) == 2
    queued = await runtime.db.one("SELECT COUNT(*) AS total FROM runs WHERE status='queued'")
    assert queued['total'] == 1


async def test_disabled_job_author_cannot_call_provider_or_spend_usage_budget(runtime):
    await runtime.db.ensure_personal_workspace('alice', 'Alice', (0, 2))
    await register_account(runtime.db, Principal('alice', 'Alice', int(time.time())))
    run_id = await seed_run(runtime)
    await runtime.db.execute("UPDATE runs SET status='running',attempt_count=1 WHERE id=?", (run_id,))
    runtime.settings.demo_mode = False
    await runtime.db.execute("UPDATE platform_users SET status='disabled' WHERE subject='alice'")
    with pytest.raises(ServiceError) as failure:
        await Execution(runtime.db, runtime.settings, run_id, 1).check()
    assert failure.value.code == 'account_disabled'
    with paid_scope('alice', 'alice'):
        with pytest.raises(ServiceError) as failure:
            await UsageGovernance(runtime.db, runtime.settings).reserve('fixture-account', 'model', 100)
    assert failure.value.code == 'account_disabled'
    assert (await runtime.db.one('SELECT COUNT(*) AS total FROM usage_attempts'))['total'] == 0


async def test_sse_closes_before_next_batch_after_account_is_disabled(settings, monkeypatch):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        headers = {'Authorization': 'Bearer ' + issue_development_token(settings, 'alice')}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/api/threads', headers=headers)).status_code == 200
            run_id = await seed_run(runtime)
            await runtime.db.event(run_id, 'before_disable', {})
            original_sleep = asyncio.sleep

            async def disable_between_batches(seconds):
                if seconds == .5:
                    await runtime.db.execute("UPDATE platform_users SET status='disabled' WHERE subject='alice'")
                    await runtime.db.event(run_id, 'after_disable', {})
                    return
                await original_sleep(seconds)

            monkeypatch.setattr('backend.api.app.asyncio.sleep', disable_between_batches)
            response = await client.get(f'/api/research/runs/{run_id}/events', headers=headers)
            assert response.status_code == 200
            assert 'before_disable' in response.text and 'after_disable' not in response.text
            assert 'event: close' in response.text


async def test_embedding_maintenance_defers_claim_without_mutating_attempt_or_status(runtime):
    run_id = await seed_run(runtime)
    await runtime.db.execute("INSERT INTO metadata(key,value) VALUES('embedding_migration_barrier','fixture-migration')")
    await runtime.execute_run(run_id)
    run = await runtime.db.one('SELECT status,attempt_count,call_attempts FROM runs WHERE id=?', (run_id,))
    assert run == {'status': 'queued', 'attempt_count': 0, 'call_attempts': 0}
    capabilities = await runtime.capabilities()
    assert capabilities['read_history'] and capabilities['read_only'] and not capabilities['new_writes']


async def test_fenced_old_abort_does_not_cancel_a_newer_generation(settings):
    settings.internal_service_token = SecretStr('fixture-internal-token')
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        run_id = await seed_run(runtime)
        await runtime.db.execute("UPDATE runs SET status='cancelled',attempt_count=2 WHERE id=?", (run_id,))
        task = Mock(research_attempt=2)
        runtime.tasks[run_id] = task
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post(f'/internal/agent/runs/{run_id}/abort',
                    headers={'Authorization': 'Bearer fixture-internal-token'}, json={'expected_attempt': 1})
                assert response.status_code == 202 and response.json()['stale']
                task.cancel.assert_not_called()
        finally:
            runtime.tasks.pop(run_id)


async def test_job_queued_after_account_disable_is_cancelled_without_claim_or_budget(runtime):
    await register_account(runtime.db, Principal('alice', 'Alice', int(time.time())))
    await runtime.db.execute("UPDATE platform_users SET status='disabled' WHERE subject='alice'")
    run_id = await seed_run(runtime)
    runtime.settings.demo_mode = False
    assert await runtime.claim_run(run_id) is None
    row = await runtime.db.one('SELECT status,attempt_count,call_attempts FROM runs WHERE id=?', (run_id,))
    assert row == {'status': 'cancelled', 'attempt_count': 0, 'call_attempts': 0}


async def test_intake_can_queue_behind_two_running_workspace_jobs(runtime):
    from backend.domain.models import RunRequest
    await runtime.db.ensure_personal_workspace('alice', 'Alice', (0, 2))
    for _ in range(2):
        run_id = await seed_run(runtime)
        await runtime.db.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
    runtime.schedule = AsyncMock(return_value=True)
    run = await runtime.create('alice', RunRequest(topic='Queued fixture', client_request_id=uid(), data_policy='public'))
    assert run['status'] == 'queued'
    assert await runtime.claim_run(run['id']) is None


async def test_usage_budget_rejection_metric_counts_the_actual_denial_once(runtime):
    from backend.core.metrics import USAGE_BUDGET_REJECTIONS
    runtime.settings.subject_period_calls = 1
    governance = UsageGovernance(runtime.db, runtime.settings)
    baseline = USAGE_BUDGET_REJECTIONS._value.get()
    with paid_scope('alice', 'alice', 'evaluation'):
        await governance.reserve('fixture-account', 'model', 100)
        with pytest.raises(ServiceError) as failure:
            await governance.reserve('fixture-account', 'model', 100)
    assert failure.value.code == 'usage_budget_exhausted'
    assert USAGE_BUDGET_REJECTIONS._value.get() == baseline + 1


async def test_scan_failure_gauge_uses_persisted_state_and_clears_after_recovery(runtime):
    from backend.core.metrics import SCAN_FAILED_DOCUMENTS
    await runtime.db.execute("INSERT INTO documents(id,user_id,name,hash,status,created_at) VALUES(?,?,?,?,?,?)",
                             (uid(), 'alice', 'failed.txt', uid(), 'scan_failed', now()))
    await runtime.refresh_queue_depth()
    assert SCAN_FAILED_DOCUMENTS._value.get() == 1
    await runtime.db.execute("UPDATE documents SET status='ready' WHERE status='scan_failed'")
    await runtime.refresh_queue_depth()
    assert SCAN_FAILED_DOCUMENTS._value.get() == 0


async def test_indexing_recovery_dispatches_bounded_batches_and_revisits_remaining_documents(runtime):
    runtime.settings.retention_cleanup_batch_size = 2
    identifiers = sorted(uid() for _ in range(3))
    stamp = now()
    for document_id in identifiers:
        await runtime.db.execute("INSERT INTO documents(id,user_id,name,hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (document_id, 'alice', 'fixture.txt', document_id, 'indexing', stamp, stamp))
    runtime.schedule_document = AsyncMock()
    await runtime.recover_indexing_documents()
    assert [call.args[0] for call in runtime.schedule_document.await_args_list] == identifiers[:2]
    await runtime.db.execute("UPDATE documents SET status='ready' WHERE id IN (?,?)", tuple(identifiers[:2]))
    runtime.schedule_document.reset_mock()
    await runtime.recover_indexing_documents()
    runtime.schedule_document.assert_awaited_once_with(identifiers[2])
