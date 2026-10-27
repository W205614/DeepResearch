"""Business counterexamples for current ACL over live and historical evidence."""
import json
from unittest.mock import AsyncMock

import pytest

from backend.core.reliability import ServiceError
from backend.core.usage_governance import paid_scope
from backend.domain.models import RunRequest
from backend.research.source_validity import validate_sources
from backend.services.document_access import require_document, require_sources


async def restricted_fixture(app, client):
    await client.get('/api/workspaces')
    rt = app.state.runtime
    for subject, role in [('bob', 'researcher'), ('viewer', 'viewer'), ('operator', 'admin')]:
        await rt.db.execute("INSERT INTO memberships VALUES('alice',?,?,'now')", (subject, role))
    # The document owner retains access as a researcher, independently of grants.
    await rt.db.execute("UPDATE memberships SET role='researcher' WHERE workspace_id='alice' AND subject='alice'")
    with paid_scope('alice', 'alice'):
        document = await rt.documents.add('alice', 'restricted.txt', b'PRIVATE-ALPHA evidence')
        await rt.documents.ingest(document['id'])
    await rt.db.execute("INSERT INTO collections(id,workspace_id,name,visibility,created_by,created_at) VALUES('restricted','alice','Private','restricted','operator','now')")
    await rt.db.execute("UPDATE document_policies SET collection_id='restricted' WHERE document_id=?", (document['id'],))
    await rt.db.execute("INSERT INTO collection_grants VALUES('restricted','bob','read')")
    chunk = await rt.db.one('SELECT * FROM chunks WHERE document_id=?', (document['id'],))
    source = dict(id='L-private', kind='local', document_id=document['id'], chunk_id=chunk['id'],
                  document_hash=document['hash'], index_version=document['index_version'], text=chunk['text'])
    return rt, document, source


def headers(subject):
    return {'X-User-ID': subject, 'X-Workspace-ID': 'alice'}


async def test_collection_grant_guards_versions_diff_chunks_write_and_owner_contract(api_client):
    app, client = api_client
    rt, document, _ = await restricted_fixture(app, client)
    path = '/api/documents/' + document['id']
    for suffix in ('/versions', '/diff?from=1&to=1', '/chunks'):
        assert (await client.get(path + suffix, headers=headers('bob'))).status_code == 200
        denied = await client.get(path + suffix, headers=headers('viewer'))
        assert denied.status_code == 422 and denied.json()['error']['code'] == 'source_permission_denied'
    assert (await client.post(path + '/reindex', headers=headers('bob'))).status_code == 422
    await rt.db.execute("DELETE FROM collection_grants WHERE collection_id='restricted' AND subject='bob'")
    assert (await client.get('/api/documents', headers=headers('bob'))).json() == []
    assert (await client.get(path + '/chunks', headers=headers('bob'))).status_code == 422
    # Revoking grants does not strip owner/admin access, and read grants never imply write.
    assert (await client.get(path + '/chunks', headers=headers('alice'))).status_code == 200
    assert (await client.get(path + '/chunks', headers=headers('operator'))).status_code == 200
    await rt.db.execute("INSERT INTO collection_grants VALUES('restricted','bob','write')")
    with paid_scope('alice', 'bob'):
        await require_document(rt.db, 'alice', document['id'], write=True)


async def test_legacy_inheritance_and_cross_workspace_context_cannot_grant_owner_access(api_client):
    app, client = api_client
    rt, document, _ = await restricted_fixture(app, client)
    with paid_scope('different-workspace', 'alice'):
        with pytest.raises(ServiceError):
            await require_document(rt.db, 'alice', document['id'])
    await rt.db.execute('DELETE FROM document_policies WHERE document_id=?', (document['id'],))
    # A pre-migration document still inherits current workspace membership.
    response = await client.get('/api/documents/' + document['id'] + '/chunks', headers=headers('viewer'))
    assert response.status_code == 200


async def persisted_private_run(rt, source):
    rt.schedule = AsyncMock()
    run = await rt.create('alice', RunRequest(topic='private report', data_policy='public', client_request_id='acl-run-0001'), 'bob')
    await rt.db.execute("UPDATE runs SET status='completed',report='FROZEN-PRIVATE-TEXT',sources=? WHERE id=?",
                        (json.dumps([source]), run['id']))
    return run


async def test_revocation_blocks_run_report_bulk_download_and_historical_evidence_without_rewriting(api_client):
    app, client = api_client
    rt, document, source = await restricted_fixture(app, client)
    run = await persisted_private_run(rt, source)
    path = '/api/research/runs/' + run['id']
    before = await rt.db.one('SELECT report,sources FROM runs WHERE id=?', (run['id'],))
    assert (await client.get(path, headers=headers('bob'))).status_code == 200
    # Content changes do not retroactively replace an authorized historical snapshot.
    await rt.db.execute("UPDATE documents SET hash='changed',index_version=2 WHERE id=?", (document['id'],))
    assert (await client.get(path, headers=headers('bob'))).json()['sources'][0]['text'] == source['text']
    await rt.db.execute("DELETE FROM collection_grants WHERE collection_id='restricted' AND subject='bob'")
    for suffix in ('', '/report', '/events'):
        assert (await client.get(path + suffix, headers=headers('bob'))).status_code == 404
    assert (await client.get('/api/threads/' + run['thread_id'] + '/runs', headers=headers('bob'))).json() == []
    bulk = await client.get('/api/threads/' + run['thread_id'] + '/report', headers=headers('bob'))
    assert 'FROZEN-PRIVATE-TEXT' not in bulk.text and 'PRIVATE-ALPHA' not in bulk.text
    assert await rt.db.one('SELECT report,sources FROM runs WHERE id=?', (run['id'],)) == before
    with paid_scope('alice', 'bob'):
        with pytest.raises(ServiceError) as error:
            await require_sources(rt.db, 'alice', [source])
        assert error.value.code == 'source_permission_denied'


async def test_sse_rechecks_acl_before_each_batch_after_collection_revocation(api_client, monkeypatch):
    app, client = api_client
    rt, _, source = await restricted_fixture(app, client)
    run = await persisted_private_run(rt, source)
    await rt.db.execute("UPDATE runs SET status='running' WHERE id=?", (run['id'],))
    await rt.db.event(run['id'], 'visible', {'message': 'authorized-before-revoke'})
    original_rows = rt.db.rows
    revoked = False
    async def rows(sql, args=()):
        nonlocal revoked
        result = await original_rows(sql, args)
        if sql.startswith('SELECT * FROM events WHERE run_id=') and not revoked:
            revoked = True
            await rt.db.execute("DELETE FROM collection_grants WHERE collection_id='restricted' AND subject='bob'")
            await rt.db.event(run['id'], 'private', {'message': 'FORBIDDEN-AFTER-REVOCATION'})
        return result
    monkeypatch.setattr(rt.db, 'rows', rows)
    response = await client.get('/api/research/runs/' + run['id'] + '/events', headers=headers('bob'))
    assert response.status_code == 200 and 'authorized-before-revoke' in response.text
    assert 'FORBIDDEN-AFTER-REVOCATION' not in response.text and 'event: close' in response.text


async def test_final_source_validation_rejects_withdrawn_collection_grant(api_client):
    app, client = api_client
    rt, _, source = await restricted_fixture(app, client)
    with paid_scope('alice', 'bob'):
        await validate_sources(rt.db, 'alice', [source])
        await rt.db.execute("DELETE FROM collection_grants WHERE collection_id='restricted' AND subject='bob'")
        with pytest.raises(ServiceError) as error:
            await validate_sources(rt.db, 'alice', [source])
    assert error.value.code == 'source_permission_denied'


async def test_semantic_summary_and_model_history_follow_current_acl_while_personal_preferences_remain_private(api_client):
    app, client = api_client
    rt, _, source = await restricted_fixture(app, client)
    run = await persisted_private_run(rt, source)
    await rt.db.execute("""INSERT INTO memories(id,user_id,kind,content,run_id,created_at)
        VALUES('private-summary','alice','semantic','PRIVATE-SUMMARY',?,'now')""", (run['id'],))
    await rt.db.execute("""INSERT INTO memories(id,user_id,kind,content,run_id,created_at,owner_subject)
        VALUES('bob-preference','alice','preference','BOB-PERSONAL-PREFERENCE','bob-preference','now','bob')""")
    rt.providers.embed = AsyncMock(return_value=[[0.1, 0.2]])
    rt.vectors.search = AsyncMock(return_value=[{'id': 'private-summary', 'score': 0.99}])
    followup = dict(id='context-with-acl', user_id='alice', created_by='bob', thread_id=run['thread_id'],
                    topic='new question', data_policy='internal')
    assert len((await client.get('/api/memories', headers=headers('bob'))).json()) == 2
    before = await rt.context(followup)
    assert 'FROZEN-PRIVATE-TEXT' in before and 'PRIVATE-SUMMARY' in before
    await rt.db.execute("DELETE FROM collection_grants WHERE collection_id='restricted' AND subject='bob'")
    memories = (await client.get('/api/memories', headers=headers('bob'))).json()
    assert [memory['id'] for memory in memories] == ['bob-preference']
    assert (await client.get('/api/memories', headers=headers('viewer'))).json() == []
    assert (await client.delete('/api/memories/private-summary', headers=headers('bob'))).status_code == 404
    after = await rt.context(followup)
    assert 'FROZEN-PRIVATE-TEXT' not in after and 'PRIVATE-SUMMARY' not in after
    assert 'BOB-PERSONAL-PREFERENCE' in after
    assert rt.providers.embed.call_args.args == (['new question'],)
    assert await rt.db.one("SELECT content FROM memories WHERE id='private-summary'") == {'content': 'PRIVATE-SUMMARY'}
