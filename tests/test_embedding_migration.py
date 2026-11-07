import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from backend.core.config import Settings
from backend.core.db import Database, now
from backend.core.reliability import ServiceError
from backend.infrastructure.providers import Providers
from backend.infrastructure.vectors import VectorIndex
from backend.services.embedding_migration import EmbeddingMigration, check_embedding_barrier, embedding_fingerprint


class MilvusFixture:
    def __init__(self):
        self.collections = {}

    def has_collection(self, name):
        return name in self.collections

    def create_collection(self, name, *, dimension, **kwargs):
        self.collections[name] = {'dimension': dimension, 'rows': {}}

    def describe_collection(self, name):
        return {'fields': [{'name': 'vector', 'params': {'dim': str(self.collections[name]['dimension'])}}]}

    def upsert(self, *, collection_name, data, **kwargs):
        self.collections[collection_name]['rows'].update({row['id']: dict(row) for row in data})

    def query(self, *, collection_name, filter, output_fields, **kwargs):
        ids = json.loads(filter.removeprefix('id in '))
        return [{key: row[key] for key in output_fields} for ident, row in self.collections[collection_name]['rows'].items() if ident in ids]

    def close(self):
        pass


@pytest.fixture
async def migration(tmp_path):
    db = Database(tmp_path / 'migration.sqlite3')
    await db.init()
    await db.ensure_personal_workspace('operator', 'Workspace', (0, 2))
    await db.execute("INSERT INTO platform_users(subject,display_name,created_at,updated_at) VALUES('operator','Operator',?,?) ON CONFLICT(subject) DO NOTHING", (now(), now()))
    await db.execute("INSERT INTO documents(id,user_id,name,hash,status,created_at,index_version) VALUES('doc','operator','policy.md','hash','ready',?,1)", (now(),))
    await db.execute("INSERT INTO chunks(id,document_id,user_id,text,locator,ordinal) VALUES('chunk','doc','operator','既有政策原文','section1',0)")
    await db.execute("INSERT INTO threads(id,user_id,title,created_at) VALUES('thread','operator','History',?)", (now(),))
    await db.execute("INSERT INTO runs(id,user_id,thread_id,topic,status,created_by,created_at,updated_at) VALUES('run','operator','thread','History','completed','operator',?,?)", (now(), now()))
    await db.execute("INSERT INTO memories(id,user_id,kind,content,run_id,owner_subject,created_at) VALUES('memory','operator','semantic','既有研究摘要','run','operator',?)", (now(),))
    source = Settings(_env_file=None, embedding_base_url='https://old-proxy.test/v1', embedding_dimension=3,
                      platform_operator_subjects='operator', allow_internal_model_processing=True)
    target = source.model_copy(update={'embedding_base_url': 'https://api.openai.com/v1', 'embedding_api_key': SecretStr('fixture-key')})
    calls = []
    def respond(request):
        calls.append(request)
        assert request.url.host == 'api.openai.com'
        return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1.0, 0.0, 0.0]}], 'usage': {'total_tokens': 7}})
    provider = Providers(target, db, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    vectors = VectorIndex(target, db)
    vectors._client = MilvusFixture()
    await VectorIndex(source, db).fingerprint()
    tool = EmbeddingMigration(db, source, target, provider, vectors)
    try:
        yield tool, db, source, target, calls, vectors
    finally:
        await provider.close()
        await vectors.close()


async def test_inspect_has_no_paid_calls_or_database_writes(migration):
    tool, db, source, target, calls, vectors = migration
    metadata = await db.rows('SELECT * FROM metadata ORDER BY key')
    report = await tool.inspect()
    assert report['eligible_items'] == {'documents': 1, 'memories': 1}
    assert report['estimated_input_tokens'] > 0 and report['provider_calls'] == 0
    assert not calls and not vectors._client.collections
    assert await db.rows('SELECT * FROM metadata ORDER BY key') == metadata
    assert '既有' not in json.dumps(report, ensure_ascii=False)


async def test_resume_enforces_cumulative_budget_and_activation_preserves_sources(migration):
    tool, db, source, target, calls, vectors = migration
    original = await db.rows('SELECT * FROM chunks')
    prepared = await tool.prepare(maintenance_acknowledged=True)
    ident = prepared['migration_id']
    with pytest.raises(ServiceError) as blocked:
        await check_embedding_barrier(db)
    assert blocked.value.code == 'embedding_migration_active'
    first = await tool.run(ident, subject='operator', max_requests=1, max_reserved_tokens=100000, allow_paid=True)
    assert first['counts'] == {'done': 1, 'pending': 1} and len(calls) == 1
    again = await tool.run(ident, subject='operator', max_requests=1, max_reserved_tokens=100000, allow_paid=True)
    assert again['budget']['requests'] == 1 and len(calls) == 1
    with pytest.raises(ServiceError) as incomplete:
        await tool.activate(ident, maintenance_acknowledged=True)
    assert incomplete.value.code == 'migration_incomplete'
    done = await tool.run(ident, subject='operator', max_requests=2, max_reserved_tokens=100000, allow_paid=True)
    assert done['counts'] == {'done': 2} and len(calls) == 2
    activated = await tool.activate(ident, maintenance_acknowledged=True)
    assert activated['status'] == 'activated'
    assert await db.rows('SELECT * FROM chunks') == original
    assert (await db.one("SELECT value FROM metadata WHERE key='embedding_fingerprint'"))['value'] == embedding_fingerprint(target)
    target_index = VectorIndex(target, db)
    await target_index.fingerprint()
    assert target_index._active_prefix == prepared['target_prefix']
    with pytest.raises(ServiceError):
        await VectorIndex(source, db).fingerprint()
    await check_embedding_barrier(db)


async def test_prepare_is_serialized_across_competing_migration_instances(migration):
    tool, db, source, target, calls, vectors = migration
    results = await asyncio.gather(*(tool.prepare(maintenance_acknowledged=True) for _ in range(2)), return_exceptions=True)
    assert sum(isinstance(row, dict) for row in results) == 1
    assert sum(isinstance(row, ServiceError) and row.code == 'migration_already_active' for row in results) == 1
    assert (await db.one('SELECT COUNT(*) AS total FROM embedding_migrations'))['total'] == 1
    assert not calls


async def test_source_and_acl_changes_prevent_spending_or_activation(migration):
    tool, db, source, target, calls, vectors = migration
    ident = (await tool.prepare(maintenance_acknowledged=True))['migration_id']
    await db.execute("UPDATE chunks SET text='修改后的政策' WHERE id='chunk'")
    with pytest.raises(ServiceError) as stale:
        await tool.run(ident, subject='operator', max_requests=2, max_reserved_tokens=100000, allow_paid=True)
    assert stale.value.code == 'migration_source_changed' and not calls
    aborted = await tool.abort(ident)
    assert aborted['status'] == 'aborted'
    assert (await db.one("SELECT value FROM metadata WHERE key='embedding_fingerprint'"))['value'] == embedding_fingerprint(source)
    await check_embedding_barrier(db)


async def test_missing_shadow_record_cannot_pass_activation(migration):
    tool, db, source, target, calls, vectors = migration
    prepared = await tool.prepare(maintenance_acknowledged=True)
    await tool.run(prepared['migration_id'], subject='operator', max_requests=2, max_reserved_tokens=100000, allow_paid=True)
    vectors._client.collections[prepared['target_prefix'] + '_documents']['rows'].clear()
    with pytest.raises(ServiceError) as coverage:
        await tool.activate(prepared['migration_id'], maintenance_acknowledged=True)
    assert coverage.value.code == 'migration_coverage_mismatch'
    assert (await db.one("SELECT value FROM metadata WHERE key='embedding_fingerprint'"))['value'] == embedding_fingerprint(source)


async def test_inflight_jobs_or_missing_maintenance_ack_prevent_prepare(migration):
    tool, db, source, target, calls, vectors = migration
    with pytest.raises(ServiceError) as maintenance:
        await tool.prepare(maintenance_acknowledged=False)
    assert maintenance.value.code == 'maintenance_required'
    await db.execute("UPDATE runs SET status='running' WHERE id='run'")
    with pytest.raises(ServiceError) as busy:
        await tool.prepare(maintenance_acknowledged=True)
    assert busy.value.code == 'migration_not_idle'
    assert await db.one("SELECT value FROM metadata WHERE key='embedding_migration_barrier'") is None


async def test_failed_unknown_attempt_is_not_automatically_repeated(migration):
    tool, db, source, target, calls, vectors = migration
    ident = (await tool.prepare(maintenance_acknowledged=True))['migration_id']
    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout('fixture', request=request)
    await tool.providers.client.aclose()
    tool.providers.client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    result = await tool.run(ident, subject='operator', max_requests=1, max_reserved_tokens=100000, allow_paid=True)
    assert result['counts'] == {'pending': 1, 'unknown': 1}
    await tool.run(ident, subject='operator', max_requests=1, max_reserved_tokens=100000, allow_paid=True)
    assert len(calls) == 1
    with pytest.raises(ServiceError):
        await tool.activate(ident, maintenance_acknowledged=True)


async def test_interruption_preserves_reserved_attempt_and_requires_explicit_retry(migration):
    tool, db, source, target, calls, vectors = migration
    ident = (await tool.prepare(maintenance_acknowledged=True))['migration_id']
    entered = asyncio.Event()
    async def blocked(request):
        calls.append(request)
        entered.set()
        await asyncio.Future()
    await tool.providers.client.aclose()
    tool.providers.client = httpx.AsyncClient(transport=httpx.MockTransport(blocked))
    task = asyncio.create_task(tool.run(ident, subject='operator', max_requests=1,
                                        max_reserved_tokens=100000, allow_paid=True))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    progress = await tool.progress(ident)
    assert progress['counts'] == {'processing': 1, 'pending': 1}
    assert progress['budget']['requests'] == 1 and progress['budget']['reserved_tokens'] > 0
    await tool.run(ident, subject='operator', max_requests=1, max_reserved_tokens=100000, allow_paid=True)
    assert len(calls) == 1


async def test_permission_or_source_change_during_remote_verification_blocks_activation(migration):
    tool, db, source, target, calls, vectors = migration
    ident = (await tool.prepare(maintenance_acknowledged=True))['migration_id']
    await tool.run(ident, subject='operator', max_requests=2, max_reserved_tokens=100000, allow_paid=True)
    verify = vectors.verify_shadow
    async def mutate(items, prefix):
        await verify(items, prefix)
        await db.execute("UPDATE memberships SET role='viewer' WHERE subject='operator'")
    vectors.verify_shadow = mutate
    with pytest.raises(ServiceError) as changed:
        await tool.activate(ident, maintenance_acknowledged=True)
    assert changed.value.code == 'migration_source_changed'
    assert (await db.one("SELECT value FROM metadata WHERE key='embedding_fingerprint'"))['value'] == embedding_fingerprint(source)


async def test_inspect_old_schema_is_read_only_and_requires_incremental_upgrade(migration):
    tool, db, source, target, calls, vectors = migration
    await db.execute('ALTER TABLE workspaces DROP COLUMN owner_subject')
    report = await tool.inspect()
    assert report['requires_schema_upgrade'] is True and report['source_snapshot'] is None
    assert report['eligible_items'] == {'documents': 1, 'memories': 1}
    assert not calls


async def test_actual_remote_dimension_is_rechecked_even_after_collection_cache(migration):
    tool, db, source, target, calls, vectors = migration
    prepared = await tool.prepare(maintenance_acknowledged=True)
    ident = prepared['migration_id']
    await tool.run(ident, subject='operator', max_requests=2, max_reserved_tokens=100000, allow_paid=True)
    vectors._client.collections[prepared['target_prefix'] + '_documents']['dimension'] = 4
    with pytest.raises(ServiceError) as changed:
        await tool.activate(ident, maintenance_acknowledged=True)
    assert changed.value.code == 'migration_dimension_mismatch'
    assert (await db.one("SELECT value FROM metadata WHERE key='embedding_fingerprint'"))['value'] == embedding_fingerprint(source)


async def test_workspace_writer_cannot_reembed_restricted_documents_without_write_grant(migration):
    tool, db, source, target, calls, vectors = migration
    await db.execute("UPDATE memberships SET role='researcher' WHERE subject='operator'")
    await db.execute("INSERT INTO collections(id,workspace_id,name,visibility,created_by,created_at) VALUES('restricted','operator','Restricted','restricted','owner',?)", (now(),))
    await db.execute("INSERT INTO document_policies(document_id,workspace_id,collection_id,owner_subject) VALUES('doc','operator','restricted','owner')")
    ident = (await tool.prepare(maintenance_acknowledged=True))['migration_id']
    with pytest.raises(ServiceError) as denied:
        await tool.run(ident, subject='operator', max_requests=2, max_reserved_tokens=100000, allow_paid=True)
    assert denied.value.code == 'source_permission_denied' and not calls
    assert (await tool.progress(ident))['budget']['requests'] == 0


async def test_migration_requires_existing_internal_model_processing_consent(migration):
    tool, db, source, target, calls, vectors = migration
    ident = (await tool.prepare(maintenance_acknowledged=True))['migration_id']
    tool.target_settings.allow_internal_model_processing = False
    with pytest.raises(ServiceError) as denied:
        await tool.run(ident, subject='operator', max_requests=2, max_reserved_tokens=100000, allow_paid=True)
    assert denied.value.code == 'egress_denied' and not calls
