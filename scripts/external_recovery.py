"""Snapshot and verify recovery through supported external PG, S3, Milvus and OIDC.

Credentials stay in operator-owned JSON files and subprocess environments. No
database, bucket, collection, source file or restored resource is deleted. IdP
native recovery must finish before verify; OIDC has no identity restore API.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg
import httpx

from backend.core.config import Settings
from backend.infrastructure.object_store import ObjectStore
from backend.infrastructure.vectors import VectorIndex
from backend.core.postgres import PostgresDatabase
from scripts.production_load import read_tokens


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def config(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    allowed = {'settings', 'pg_admin_url', 'pg_restore_role', 'app_base_url', 'tokens_file', 'api_ca_certs'}
    if not isinstance(value, dict) or set(value) - allowed or not isinstance(value.get('settings'), dict):
        raise ValueError('Recovery config must use the documented fields')
    unknown = set(value['settings']) - set(Settings.model_fields)
    if unknown:
        raise ValueError('Unknown Settings field in recovery config')
    settings = Settings(_env_file=None, **value['settings'])
    if settings.demo_mode or settings.object_store_backend != 's3' or not settings.database_url.startswith('postgresql://'):
        raise ValueError('External recovery requires PostgreSQL and S3 settings')
    if not value.get('pg_admin_url'):
        raise ValueError('A private pg_admin_url is required for pg_dump/restore')
    if pg_identity(value['pg_admin_url']) != pg_identity(settings.database_url):
        raise ValueError('Administrative and runtime URLs must address the same PostgreSQL host/port/database')
    settings.object_store_auto_create_bucket = False
    return value, settings


def pg_identity(url):
    value = urlsplit(url)
    return value.hostname, value.port or 5432, unquote(value.path.lstrip('/'))


def pg_environment(url):
    value = urlsplit(url)
    if value.scheme not in {'postgres', 'postgresql'} or not value.hostname or not value.path.strip('/'):
        raise ValueError('Invalid PostgreSQL administrative endpoint')
    environment = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    environment.update(PGHOST=value.hostname, PGPORT=str(value.port or 5432),
        PGUSER=unquote(value.username or ''), PGPASSWORD=unquote(value.password or ''),
        PGDATABASE=unquote(value.path.lstrip('/')))
    parameters = parse_qs(value.query)
    for key, name in {'sslmode': 'PGSSLMODE', 'sslrootcert': 'PGSSLROOTCERT',
                      'sslcert': 'PGSSLCERT', 'sslkey': 'PGSSLKEY'}.items():
        if key in parameters:
            environment[name] = parameters[key][-1]
    return environment


def pg_tool(executable, url, arguments):
    output = subprocess.run([executable, *arguments], env=pg_environment(url),
                            capture_output=True, check=False)
    if output.returncode:
        raise RuntimeError(executable + ' failed; credential and driver output withheld')


async def table_hashes(connection):
    await connection.execute("SET TIME ZONE 'UTC'")
    tables = await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
    result = {}
    for row in tables:
        table = row['tablename']
        quoted = '"' + table.replace('"', '""') + '"'
        hasher, count = hashlib.sha256(), 0
        async with connection.transaction(readonly=True):
            async for record in connection.cursor(f'SELECT to_jsonb(t)::text AS value FROM public.{quoted} t ORDER BY to_jsonb(t)::text'):
                hasher.update(record['value'].encode() + b'\n')
                count += 1
        result[table] = {'rows': count, 'sha256': hasher.hexdigest()}
    return result


async def quiescent(connection):
    active = await connection.fetchval("SELECT (SELECT COUNT(*) FROM runs WHERE status IN ('queued','running','interrupted')) + (SELECT COUNT(*) FROM documents WHERE status IN ('scanning','indexing','rebuilding'))")
    barrier = await connection.fetchval("SELECT value FROM metadata WHERE key='embedding_migration_barrier'")
    if active or barrier:
        raise RuntimeError('Source has active research/indexing/migration; finish quiescing first')


async def open_index(settings, *, readonly=False):
    database = PostgresDatabase(settings.database_url)
    await database.init()
    index = VectorIndex(settings, database)
    try:
        if readonly:
            expected = hashlib.sha256(json.dumps([settings.embedding_model, settings.embedding_base_url,
                settings.embedding_dimension, settings.demo_mode]).encode()).hexdigest()
            stored = await database.one("SELECT value FROM metadata WHERE key='embedding_fingerprint'")
            if not stored or stored['value'] != expected:
                raise ValueError('Snapshot requires an existing matching embedding fingerprint')
            active = await database.one("SELECT value FROM metadata WHERE key='embedding_active_config'")
            if active:
                active = json.loads(active['value'])
                if active['fingerprint'] != expected:
                    raise ValueError('Snapshot active index differs from its embedding fingerprint')
                index._active_prefix = active['prefix']
        else:
            await index.fingerprint()
    except Exception:
        await database.close()
        raise
    return database, index


def canonical_record(record):
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      default=lambda item: item.tolist() if hasattr(item, 'tolist') else float(item))


async def snapshot(args, private, settings):
    args.stage = 'source_quiescence'
    if not args.quiesced:
        raise ValueError('Snapshot requires explicit --quiesced after stopping business writes')
    args.bundle.mkdir(parents=True, exist_ok=False)
    os.chmod(args.bundle, 0o700)
    connection = await asyncpg.connect(private['pg_admin_url'])
    store, database, index = ObjectStore(settings), None, None
    try:
        await quiescent(connection)
        before = await table_hashes(connection)
        args.stage = 'postgres_snapshot'
        await asyncio.to_thread(pg_tool, 'pg_dump', private['pg_admin_url'],
            ['--format=custom', '--no-owner', '--no-privileges', '--file', str(args.bundle / 'postgres.dump')])
        await store.start()
        args.stage = 's3_snapshot'
        objects = []
        (args.bundle / 'objects').mkdir()
        def copy_objects():
            for page in store._client.get_paginator('list_objects_v2').paginate(Bucket=settings.object_store_bucket):
                for item in page.get('Contents', []):
                    key = item['Key']
                    relative = 'objects/' + hashlib.sha256(key.encode()).hexdigest()
                    response = store._client.get_object(Bucket=settings.object_store_bucket, Key=key)
                    try:
                        with (args.bundle / relative).open('wb') as target:
                            while body := response['Body'].read(1024 * 1024):
                                target.write(body)
                    finally:
                        response['Body'].close()
                    objects.append({'key': key, 'path': relative, 'sha256': digest(args.bundle / relative),
                                    'bytes': (args.bundle / relative).stat().st_size})
        await asyncio.to_thread(copy_objects)
        references = await connection.fetch("""SELECT 'uploads/' || CASE WHEN object_key='' THEN id ELSE object_key END AS key
            FROM documents WHERE status='ready' UNION SELECT 'uploads/' || id AS key FROM attachments WHERE status='ready'""")
        if not {row['key'] for row in references}.issubset({item['key'] for item in objects}):
            raise RuntimeError('Live SQL evidence references absent S3 objects; snapshot is incomplete')
        database, index = await open_index(settings, readonly=True)
        args.stage = 'milvus_snapshot'
        collections = {}
        for kind in ('documents', 'memories'):
            relative = 'vectors-' + kind + '.jsonl'
            def copy_vectors():
                identifiers = set()
                client, name = index.client(), index._active_prefix + '_' + kind
                with (args.bundle / relative).open('w', encoding='utf-8') as output:
                    if client.has_collection(name):
                        fields = client.describe_collection(name)['fields']
                        dimension = next(int(field['params']['dim']) for field in fields if field['name'] == 'vector')
                        if dimension != settings.embedding_dimension:
                            raise RuntimeError('Source collection dimension differs from its configured contract')
                        iterator = client.query_iterator(collection_name=name, batch_size=100, filter='', output_fields=['*', 'vector'])
                        try:
                            while rows := iterator.next():
                                for record in rows:
                                    if len(record['vector']) != settings.embedding_dimension:
                                        raise RuntimeError('Source vector dimension differs from its configured contract')
                                    output.write(canonical_record(record) + '\n')
                                    identifiers.add(record['id'])
                        finally:
                            iterator.close()
                return identifiers
            identifiers = await asyncio.to_thread(copy_vectors)
            sql_ids = await connection.fetch('SELECT id FROM chunks' if kind == 'documents' else
                                              "SELECT id FROM memories WHERE kind='semantic'")
            if not {row['id'] for row in sql_ids}.issubset(identifiers):
                raise RuntimeError('Source vectors do not cover retained SQL evidence; snapshot is incomplete')
            collections[kind] = {'path': relative, 'sha256': digest(args.bundle / relative), 'entities': len(identifiers)}
        await quiescent(connection)
        args.stage = 'snapshot_consistency'
        if before != await table_hashes(connection):
            raise RuntimeError('Source changed during snapshot; bundle remains incomplete and cannot be restored')
        manifest = {'format': 1, 'created_at': time.time(), 'tables': before, 'objects': objects,
            'collections': collections, 'postgres_sha256': digest(args.bundle / 'postgres.dump'),
            'source': {'database': list(pg_identity(private['pg_admin_url'])),
                       'bucket': settings.object_store_bucket, 's3_endpoint': settings.object_store_endpoint,
                       'milvus_uri': settings.milvus_uri, 'prefix': index._active_prefix},
            'embedding': {'model': settings.embedding_model, 'base_url': settings.embedding_base_url,
                          'dimension': settings.embedding_dimension, 'demo_mode': settings.demo_mode},
            'identity': {'issuer': settings.oidc_issuer, 'audience': settings.oidc_audience},
            'identity_native_restore_required': True}
        (args.bundle / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'snapshot_complete': True, 'tables': len(before), 'objects': len(objects),
                'vector_entities': {kind: value['entities'] for kind, value in collections.items()}}
    finally:
        await connection.close()
        if database:
            await database.close()
        if index and index._client:
            await asyncio.to_thread(index._client.close)


def manifest(args):
    value = json.loads((args.bundle / 'manifest.json').read_text(encoding='utf-8'))
    if value.get('format') != 1:
        raise ValueError('Unsupported external recovery bundle')
    if digest(args.bundle / 'postgres.dump') != value['postgres_sha256']:
        raise ValueError('PostgreSQL archive integrity check failed')
    for item in [*value['objects'], *value['collections'].values()]:
        path = (args.bundle / item['path']).resolve()
        if not path.is_relative_to(args.bundle.resolve()) or not path.is_file() or digest(path) != item['sha256']:
            raise ValueError('Recovery bundle file is absent, unsafe, or changed')
    return value


async def restore(args, private, settings, backup):
    args.stage = 'restore_preflight'
    if not args.isolated:
        raise ValueError('Restore requires --isolated and fresh external targets')
    if list(pg_identity(private['pg_admin_url'])) == backup['source']['database']:
        raise ValueError('Source database cannot be a restore target')
    if settings.object_store_bucket == backup['source']['bucket'] and settings.object_store_endpoint == backup['source']['s3_endpoint']:
        raise ValueError('Source bucket cannot be a restore target')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', settings.vector_collection_prefix):
        raise ValueError('Restore collection prefix must be a Milvus identifier')
    if settings.milvus_uri == backup['source']['milvus_uri'] and settings.vector_collection_prefix == backup['source']['prefix']:
        raise ValueError('Source collections cannot be restore targets')
    embedding = {'model': settings.embedding_model, 'base_url': settings.embedding_base_url,
                 'dimension': settings.embedding_dimension, 'demo_mode': settings.demo_mode}
    if embedding != backup['embedding']:
        raise ValueError('Restore embedding fingerprint differs; do not mix vector spaces')
    connection = await asyncpg.connect(private['pg_admin_url'])
    store, database, index = ObjectStore(settings), None, None
    try:
        if await connection.fetchval("SELECT COUNT(*) FROM pg_tables WHERE schemaname='public'"):
            raise ValueError('Restore database must have no application tables')
        await store.start()
        if store._client.list_objects_v2(Bucket=settings.object_store_bucket, MaxKeys=1).get('KeyCount', 0):
            raise ValueError('Restore bucket must be empty')
        # Reuse the production authenticated/TLS client configuration.
        index = VectorIndex(settings, None)
        for kind in backup['collections']:
            if await asyncio.to_thread(index.client().has_collection, settings.vector_collection_prefix + '_' + kind):
                raise ValueError('Restore collections must not already exist')
        role = private.get('pg_restore_role', 'dr_migrator')
        if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', role):
            raise ValueError('Invalid restore owner role')
        args.stage = 'postgres_restore'
        await asyncio.to_thread(pg_tool, 'pg_restore', private['pg_admin_url'],
            ['--exit-on-error', '--single-transaction', '--no-owner', '--no-privileges', '--role', role,
             '--dbname', pg_environment(private['pg_admin_url'])['PGDATABASE'], str(args.bundle / 'postgres.dump')])
        if await table_hashes(connection) != backup['tables']:
            raise RuntimeError('Restored SQL rows differ from the snapshot')
        from scripts.migrate_services import grant_runtime
        await grant_runtime(connection)
        args.stage = 's3_restore'
        def restore_objects():
            for item in backup['objects']:
                with (args.bundle / item['path']).open('rb') as body:
                    store._client.put_object(Bucket=settings.object_store_bucket, Key=item['key'], Body=body,
                                             **store._encryption())
        await asyncio.to_thread(restore_objects)
        # Only the collection namespace changes; the model/endpoint/dimension
        # fingerprint and every backed-up vector remain unchanged.
        active = await connection.fetchval("SELECT value FROM metadata WHERE key='embedding_active_config'")
        if active:
            active = json.loads(active) | {'prefix': settings.vector_collection_prefix}
            await connection.execute("UPDATE metadata SET value=$1 WHERE key='embedding_active_config'", json.dumps(active))
        await asyncio.to_thread(index._client.close)
        args.stage = 'milvus_restore'
        database, index = await open_index(settings)
        for kind, description in backup['collections'].items():
            await asyncio.to_thread(index.ensure, kind)
            with (args.bundle / description['path']).open(encoding='utf-8') as source:
                rows = []
                for line in source:
                    rows.append(json.loads(line))
                    if len(rows) == 100:
                        await index.upsert(kind, rows)
                        rows = []
                if rows:
                    await index.upsert(kind, rows)
        return {'restore_complete': True, 'sql_tables_verified': len(backup['tables']),
                'objects_restored': len(backup['objects']), 'identity_native_restore_required': True,
                'application_verification_required': True, 'resources_retained': True}
    finally:
        await connection.close()
        if database:
            await database.close()
        if index and index._client:
            await asyncio.to_thread(index._client.close)


async def verify(args, private, settings, backup):
    args.stage = 'verify_preflight'
    if not args.isolated or not private.get('tokens_file') or not private.get('app_base_url'):
        raise ValueError('Verify requires --isolated, restored identity tokens_file and app_base_url')
    tokens = read_tokens(Path(private['tokens_file']))
    verify_tls = ssl.create_default_context(cafile=private['api_ca_certs']) if private.get('api_ca_certs') else True
    connection = await asyncpg.connect(private['pg_admin_url'])
    store, database, index = ObjectStore(settings), None, None
    try:
        # Authentication updates last-seen identity metadata. Verify persisted
        # business content before making those intentional authenticated reads.
        actual = await table_hashes(connection)
        if {key: value for key, value in actual.items() if key != 'metadata'} != {
                key: value for key, value in backup['tables'].items() if key != 'metadata'}:
            raise RuntimeError('Restored business SQL differs before application verification')
        await store.start()
        args.stage = 'verify_s3'
        def verify_objects():
            for item in backup['objects']:
                response = store._client.get_object(Bucket=settings.object_store_bucket, Key=item['key'])
                hasher = hashlib.sha256()
                try:
                    while block := response['Body'].read(1024 * 1024):
                        hasher.update(block)
                finally:
                    response['Body'].close()
                if hasher.hexdigest() != item['sha256']:
                    raise RuntimeError('Restored S3 object differs')
        await asyncio.to_thread(verify_objects)
        database, index = await open_index(settings, readonly=True)
        args.stage = 'verify_milvus'
        for kind, description in backup['collections'].items():
            def verify_vectors():
                client, name = index.client(), index._active_prefix + '_' + kind
                if not client.has_collection(name):
                    raise RuntimeError('Restored vector collection is absent')
                iterator = client.query_iterator(collection_name=name, batch_size=100, filter='', output_fields=['*', 'vector'])
                expected = {}
                with (args.bundle / description['path']).open(encoding='utf-8') as source:
                    for line in source:
                        record = json.loads(line)
                        expected[record['id']] = hashlib.sha256(canonical_record(record).encode()).hexdigest()
                count = 0
                try:
                    while rows := iterator.next():
                        for record in rows:
                            if expected.get(record['id']) != hashlib.sha256(canonical_record(record).encode()).hexdigest():
                                raise RuntimeError('Restored vector/entity differs')
                            count += 1
                finally:
                    iterator.close()
                if count != description['entities']:
                    raise RuntimeError('Restored collection coverage differs')
            await asyncio.to_thread(verify_vectors)
        with (args.bundle / backup['collections']['documents']['path']).open(encoding='utf-8') as source:
            first = source.readline()
            if not first:
                raise RuntimeError('No restored document vector; retrieval recovery is unverified')
            sample = json.loads(first)
        hits = await index.search('documents', sample['user_id'], sample['vector'], limit=1)
        if not hits or not await connection.fetchval('SELECT 1 FROM chunks WHERE id=$1 AND user_id=$2',
                                                      hits[0]['id'], sample['user_id']):
            raise RuntimeError('Restored vector retrieval does not map to SQL evidence')
        checked = {'identity_tokens': 0, 'permission_boundaries': 0, 'published_reports': 0, 'downloads': 0, 'citations': 0}
        args.stage = 'verify_oidc_acl_reports'
        async with httpx.AsyncClient(base_url=private['app_base_url'], timeout=30, verify=verify_tls, trust_env=False) as client:
            for subject, token in tokens.items():
                headers = {'Authorization': 'Bearer ' + token}
                account = await connection.fetchrow('SELECT status,revoked_before FROM platform_users WHERE subject=$1', subject)
                if not account:
                    raise RuntimeError('Verification token subject was absent from the restored identity registry')
                import jwt
                claims = jwt.decode(token, options={'verify_signature': False})
                denied = account and (account['status'] != 'active' or claims.get('iat', 0) <= account['revoked_before'])
                response = await client.get('/api/workspaces', headers=headers)
                if denied:
                    if response.status_code not in {401, 403}:
                        raise RuntimeError('Restored disabled/revoked identity was allowed')
                    checked['permission_boundaries'] += 1
                    continue
                if response.status_code != 200:
                    raise RuntimeError('Restored OIDC identity failed application verification')
                checked['identity_tokens'] += 1
                memberships = await connection.fetch('SELECT workspace_id,role FROM memberships WHERE subject=$1', subject)
                expected_spaces = {row['workspace_id'] for row in memberships}
                if {row['id'] for row in response.json()} != expected_spaces:
                    raise RuntimeError('Restored OIDC subject mapping differs from memberships')
                for membership in memberships:
                    scoped = headers | {'X-Workspace-ID': membership['workspace_id']}
                    if membership['role'] == 'viewer':
                        denied_write = await client.post('/api/threads', headers=scoped, json={'title': 'must reject viewer'})
                        if denied_write.status_code != 403:
                            raise RuntimeError('Restored viewer gained write permission')
                        checked['permission_boundaries'] += 1
                    reports = await connection.fetch("SELECT * FROM report_publications WHERE workspace_id=$1 AND status='published'", membership['workspace_id'])
                    for report in reports:
                        response = await client.get('/api/reports/' + report['id'], headers=scoped)
                        if response.status_code in {403, 404}:
                            checked['permission_boundaries'] += 1
                            continue
                        if response.status_code != 200 or response.json()['content_sha256'] != report['content_sha256']:
                            raise RuntimeError('Restored formal report hash differs')
                        body = response.json()
                        if body['report_markdown'] != report['report_markdown']:
                            raise RuntimeError('Restored formal report text differs')
                        snapshot_text = report['report_markdown'] + '\n' + report['sources_json'] + '\n' + report['validation_json']
                        hashes = {hashlib.sha256((snapshot_text + '\n' + report['artifacts_json']).encode()).hexdigest()}
                        if not json.loads(report['artifacts_json']):
                            hashes.add(hashlib.sha256(snapshot_text.encode()).hexdigest())
                        if report['content_sha256'] not in hashes:
                            raise RuntimeError('Formal report content hash is internally inconsistent')
                        sources = json.loads(report['sources_json'])
                        if sources and not any('[' + source['id'] + ']' in body['report_markdown'] for source in sources):
                            raise RuntimeError('Restored formal report lost citations')
                        checked['citations'] += bool(sources)
                        checked['published_reports'] += 1
                        downloaded = await client.get('/api/reports/' + report['id'] + '/download', headers=scoped)
                        if downloaded.status_code != 200 or downloaded.content != report['report_markdown'].encode():
                            raise RuntimeError('Restored formal report cannot be downloaded')
                        checked['downloads'] += 1
            if min(checked['identity_tokens'], checked['permission_boundaries'], checked['published_reports'], checked['downloads'], checked['citations']) < 1:
                raise RuntimeError('Insufficient restored identities/permissions/published report/citation fixtures; recovery is unverified')
        return {'passed': True, **checked, 'sql_tables_verified': len(actual),
                'objects_sha256_verified': len(backup['objects']), 'vectors_verified': True,
                'sql_vector_retrieval_verified': True,
                'scope': 'isolated external logical recovery; no provider/region RTO or RPO claim', 'resources_retained': True}
    finally:
        await connection.close()
        if database:
            await database.close()
        if index and index._client:
            await asyncio.to_thread(index._client.close)


async def main(args):
    args.stage = 'configuration'
    private, settings = config(args.config)
    if args.action == 'snapshot':
        return await snapshot(args, private, settings)
    args.stage = 'bundle_integrity'
    backup = manifest(args)
    return await (restore(args, private, settings, backup) if args.action == 'restore' else verify(args, private, settings, backup))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('snapshot', 'restore', 'verify'))
    parser.add_argument('--config', type=Path, required=True, help='Private JSON; never a command-line DSN/key/token')
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--quiesced', action='store_true')
    parser.add_argument('--isolated', action='store_true')
    parser.add_argument('--report', type=Path, default=Path('.cache/external-recovery.json'))
    args = parser.parse_args()
    try:
        result = asyncio.run(main(args))
    except Exception as error:
        result = {'passed': False, 'action': args.action, 'stage': args.stage,
                  'failure_type': type(error).__name__, 'resources_retained': True}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result), flush=True)
        raise SystemExit('External recovery failed; private driver output withheld') from None
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
