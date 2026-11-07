"""Read-only profiling of an existing corpus; stops before provider/cache writes.

Reports contain plan node names, timings and row/buffer counts, never SQL values,
document text, subject IDs, query text, token contents or endpoint credentials.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg

from backend.core.config import Settings
from backend.core.postgres import PostgresDatabase
from backend.core.usage_governance import paid_scope
from backend.services.documents import Documents
from scripts.production_load import read_tokens


def category(sql):
    if 'FROM document_search_cache' in sql:
        return 'result_cache_lookup'
    if 'WITH query_terms' in sql:
        return 'lexical_scores'
    if 'AVG(l.length)' in sql:
        return 'lexical_stats'
    if 'LEFT JOIN lexical_chunks' in sql:
        return 'lexical_missing_count' if 'COUNT(*)' in sql else 'lexical_missing_batch'
    if 'COUNT(*)' in sql and 'FROM chunks c' in sql:
        return 'authorized_chunk_count'
    if 'c.id IN (' in sql:
        return 'candidate_hydration'
    if 'SELECT d.id,d.hash,d.index_version' in sql:
        return 'authorized_document_snapshot'
    return 'other_read'


SAFE_PLAN_KEYS = {'Node Type', 'Parent Relationship', 'Relation Name', 'Index Name', 'CTE Name',
    'Join Type', 'Strategy', 'Scan Direction', 'Parallel Aware', 'Async Capable',
    'Startup Cost', 'Total Cost', 'Plan Rows', 'Plan Width', 'Actual Startup Time',
    'Actual Total Time', 'Actual Rows', 'Actual Loops', 'Rows Removed by Filter',
    'Rows Removed by Join Filter', 'Heap Fetches', 'Shared Hit Blocks', 'Shared Read Blocks',
    'Temp Read Blocks', 'Temp Written Blocks', 'Sort Method', 'Sort Space Used', 'Sort Space Type',
    'Hash Batches', 'Original Hash Batches', 'Hash Buckets', 'Peak Memory Usage', 'Disk Usage',
    'Cache Hits', 'Cache Misses', 'Cache Evictions', 'Cache Overflows'}


def plan_summary(value):
    value = json.loads(value) if isinstance(value, str) else value
    document = value[0]
    flattened = []
    def visit(node, path):
        row = {key: node[key] for key in SAFE_PLAN_KEYS if key in node}
        row['path'] = path
        total = node.get('Actual Total Time', 0) * node.get('Actual Loops', 1)
        children = sum(child.get('Actual Total Time', 0) * child.get('Actual Loops', 1) for child in node.get('Plans', []))
        row['inclusive_time_ms'] = round(total, 3)
        row['self_time_estimate_ms'] = round(max(0, total - children), 3)
        flattened.append(row)
        for index, child in enumerate(node.get('Plans', [])):
            visit(child, path + '.' + str(index))
    visit(document['Plan'], '0')
    return {'planning_ms': document.get('Planning Time'), 'execution_ms': document.get('Execution Time'),
            'jit': document.get('JIT'), 'nodes': flattened,
            'largest_self_time_nodes': sorted(flattened, key=lambda row: row['self_time_estimate_ms'], reverse=True)[:8]}


class StopBeforeProvider(Exception):
    pass


class ProfileDatabase(PostgresDatabase):
    def __init__(self, dsn):
        super().__init__(dsn)
        self.operations, self.statements = [], {}
        self.cache = None

    @asynccontextmanager
    async def guard(self, key):
        # Index repair is prohibited. Its advisory write lock is unnecessary for
        # the measured read path, which is additionally protected by SQL read-only.
        yield

    async def execute(self, *args, **kwargs):
        raise RuntimeError('Profile attempted a mutation; stopped before executing it')

    async def rows(self, sql, args=()):
        label, started = category(sql), time.perf_counter()
        result = await super().rows(sql, args)
        self.operations.append({'stage': label, 'elapsed_ms': round((time.perf_counter() - started) * 1000, 3),
                                'returned_rows': len(result)})
        self.statements.setdefault(label, (sql, args))
        return result

    async def one(self, sql, args=()):
        result = await super().one(sql, args)
        if category(sql) == 'result_cache_lookup':
            cached = json.loads(result['result']) if result else None
            self.cache = {'stored_hit': bool(result), 'cached_candidates': len(cached) if isinstance(cached, list) else 0}
            raise StopBeforeProvider
        return result


async def explain(database, sql, parameters, settings=None):
    async with database.pool.acquire() as connection:
        async with connection.transaction(readonly=True):
            for name, value in (settings or {}).items():
                # Setting names/values are fixed below, never external strings.
                await connection.execute('SELECT set_config($1,$2,true)', name, value)
            value = await connection.fetchval('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + database._sql(sql), *parameters)
            return plan_summary(value)


async def main(args):
    settings = Settings()
    database = ProfileDatabase(settings.database_url)
    database.pool = await asyncpg.create_pool(database.dsn, min_size=1, max_size=1,
        server_settings={'default_transaction_read_only': 'on', 'statement_timeout': '60000', 'lock_timeout': '5000'})
    documents = Documents(settings, database, None, None)
    try:
        subject = next(iter(read_tokens(args.tokens_json)))
        member = await database.membership(args.workspace, subject)
        if not member:
            raise ValueError('Profile requires an existing workspace member')
        database.operations.clear()
        database.statements.clear()
        settings_result = await database.pool.fetch('SELECT name,setting,unit FROM pg_settings WHERE name=ANY($1::text[])',
            ['jit', 'work_mem', 'shared_buffers', 'effective_cache_size', 'max_parallel_workers_per_gather', 'default_statistics_target', 'plan_cache_mode'])
        started = time.perf_counter()
        with paid_scope(args.workspace, subject):
            try:
                await documents.search(args.workspace, ['Source validation workflow'], 6)
            except StopBeforeProvider:
                pass
            else:
                raise RuntimeError('Profile did not reach the expected cache boundary')
        result = {'scope': 'one isolated read-only profile; no model/vector/S3 external client initialized or called',
                  'read_only': True, 'supplier_http_requests': 0, 'source_mutations': 0,
                  'maintenance_advisory_lock_omitted': True,
                  'pre_cache_elapsed_ms': round((time.perf_counter() - started) * 1000, 3),
                  'cache': database.cache, 'operations_before_cache': database.operations,
                  'settings': [dict(row) for row in settings_result], 'baseline_explain': {}}
        print(json.dumps({'phase': 'read_path_complete', 'pre_cache_elapsed_ms': result['pre_cache_elapsed_ms'],
                          'cache': result['cache']}), flush=True)
        for label, (sql, parameters) in database.statements.items():
            result['baseline_explain'][label] = await explain(database, sql, parameters)
        if 'lexical_scores' in database.statements:
            sql, parameters = database.statements['lexical_scores']
            result['session_only_variants'] = {
                'jit_off': await explain(database, sql, parameters, {'jit': 'off'}),
                'jit_off_work_mem_64mb': await explain(database, sql, parameters, {'jit': 'off', 'work_mem': '64MB'})}
        relation_names = ['documents', 'chunks', 'lexical_chunks', 'chunk_terms', 'memberships', 'document_policies', 'document_search_cache']
        result['table_statistics'] = [dict(row) for row in await database.pool.fetch('''
            SELECT relname,n_live_tup,n_mod_since_analyze,last_analyze::text,last_autoanalyze::text
            FROM pg_stat_user_tables WHERE schemaname='public' AND relname=ANY($1::text[])''', relation_names)]
        result['buffer_statistics'] = [dict(row) for row in await database.pool.fetch('''
            SELECT relname,heap_blks_read,heap_blks_hit,idx_blks_read,idx_blks_hit
            FROM pg_statio_user_tables WHERE schemaname='public' AND relname=ANY($1::text[])''', relation_names)]
        result['index_definitions'] = [dict(row) for row in await database.pool.fetch('''
            SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename=ANY($1::text[])''', relation_names)]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'profile_saved': args.output.as_posix(), 'source_mutations': 0, 'supplier_http_requests': 0}), flush=True)
    finally:
        documents.lexical_pool.shutdown(wait=True)
        await database.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--tokens-json', type=Path, default=Path('/receipts/tokens.json'))
    parser.add_argument('--output', type=Path, default=Path('/receipts/retrieval-profile.json'))
    asyncio.run(main(parser.parse_args()))
