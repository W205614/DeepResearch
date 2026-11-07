"""Bounded acceptance load for explicitly isolated real components and fixture models.

Run inside the verification application network. Tokens are read from a private
JSON file, never printed. This intentionally retains its dataset and report.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg
import httpx
import jwt

from backend.core.auth import issue_development_token
from backend.core.config import Settings
from backend.core.db import uid
from backend.services.document_parsing import split_document


def percentile(values):
    return round(sorted(values)[int((len(values) - 1) * .95)] * 1000, 2) if values else None


class RequestFailure(RuntimeError):
    def __init__(self, method, path, status, code):
        import re
        route = re.sub(r'/(runs|documents|threads|reports|workspaces)/(?:[0-9a-f]{32}|[0-9a-f-]{36}|thread[0-9]+)(?=/|$)',
                       r'/\1/{id}', path.split('?')[0])
        self.context = {'method': method, 'route': route, 'http_status': status, 'code': code}
        super().__init__(json.dumps(self.context))


def response_code(response):
    import re
    try:
        body = response.json()
    except ValueError:
        return 'http_' + str(response.status_code)
    if isinstance(body, dict):
        error = body.get('error')
        value = error.get('code') if isinstance(error, dict) else body.get('code')
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_]{1,80}', value):
            return value
    return 'http_' + str(response.status_code)


def document_content(index, chunks):
    # Markdown sections produce independent parser blocks; validate the exact
    # expected chunk count using the production parser before any upload.
    content = '\n\n'.join(f'# Source validation workflow {index}-{part}\n'
        f'Fixture document {index}, paragraph {part}. Research assistants collect sources, '
        'verify evidence, preserve citations and recover interrupted research tasks.'
        for part in range(chunks)).encode()
    if len(split_document('capacity.md', content)) != chunks:
        raise RuntimeError('Fixture no longer matches the production parser chunk contract')
    return content


def read_tokens(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(value, list):
        # The server verifies these tokens. Decode only the subject label here
        # to accept the existing Keycloak fixture provisioner's output format.
        value = {jwt.decode(token, options={'verify_signature': False})['sub']: token for token in value}
    if not isinstance(value, dict) or any(not isinstance(k, str) or not k or
            not isinstance(v, str) or not v for k, v in value.items()):
        raise ValueError('Token file must contain a token list or subject-to-token object')
    return value


async def existing_corpus(database, settings, workspace, subjects, documents, chunks_per_document):
    owner = await database.fetchval('SELECT owner_subject FROM workspaces WHERE id=$1', workspace)
    if owner != subjects[0]:
        raise ValueError('Existing corpus must be owned by the first provided test account')
    members = await database.fetch('SELECT subject,role FROM memberships WHERE workspace_id=$1', workspace)
    if {row['subject'] for row in members} != set(subjects) or any(row['role'] not in {'admin', 'researcher'} for row in members):
        raise ValueError('Existing corpus must contain exactly the provided test accounts with writer access')
    rows = await database.fetch('SELECT id,name,hash,status,object_key FROM documents WHERE user_id=$1', workspace)
    expected = {f'capacity-{index}.md': hashlib.sha256(document_content(index, chunks_per_document)).hexdigest()
                for index in range(documents)}
    if len(rows) != documents or {row['name'] for row in rows} != set(expected) or any(
            row['status'] != 'ready' or row['hash'] != expected[row['name']] for row in rows):
        raise ValueError('Existing corpus document names/status/content hashes differ from the requested fixture')
    counts = await database.fetch('SELECT document_id,COUNT(*) AS total FROM chunks WHERE user_id=$1 GROUP BY document_id', workspace)
    if {row['document_id'] for row in counts} != {row['id'] for row in rows} or any(
            row['total'] != chunks_per_document for row in counts):
        raise ValueError('Existing corpus must have exactly the requested chunk count in every document')
    from backend.infrastructure.object_store import ObjectStore
    store = ObjectStore(settings)
    await store.start()
    async def verify_file(row):
        body = await store.get('uploads', row['object_key'] or row['id'])
        if hashlib.sha256(body).hexdigest() != row['hash']:
            raise ValueError('Existing S3 original content differs from its indexed fixture hash')
    try:
        for offset in range(0, len(rows), 4):
            await asyncio.gather(*(verify_file(row) for row in rows[offset:offset + 4]))
    finally:
        if store._client:
            await asyncio.to_thread(store._client.close)
    return [row['id'] for row in rows]


async def main(args):
    settings = Settings()
    if not args.isolated or os.environ.get('INTEGRATION') != '1':
        raise RuntimeError('Requires --isolated and INTEGRATION=1; never target daily data')
    if settings.demo_mode or settings.queue_backend != 'redis' or settings.object_store_backend != 's3' or not settings.database_url.startswith('postgresql://'):
        raise RuntimeError('Requires real PostgreSQL/Redis/Milvus and fixture providers, with DEMO_MODE=false')
    if settings.embedding_dimension != 3072:
        raise RuntimeError('This acceptance contract requires a 3072-dimensional vector collection')
    prefix = 'capacity-' + uid()[:10]
    if args.tokens_json:
        tokens = read_tokens(args.tokens_json)
        if len(tokens) < args.accounts:
            raise ValueError('Token JSON must map at least --accounts known subjects to nonempty tokens')
        tokens = dict(list(tokens.items())[:args.accounts])
    elif settings.auth_mode == 'development':
        tokens = {f'{prefix}-{index:02}': issue_development_token(settings, f'{prefix}-{index:02}', expires_in=7200)
                  for index in range(args.accounts)}
    else:
        raise ValueError('OIDC acceptance requires --tokens-json; identity credentials are never guessed')
    subjects = list(tokens)
    metrics = {'requests': 0, 'unexpected_errors': 0, 'control': [], 'search': [],
               'accepted': set(), 'completed': set(), 'terminal': {}, 'max_running': 0,
               'max_workspace_running': 0, 'duplicate_requests': 0, 'max_waiting': 0, 'request_errors': {}}
    failures = []
    db = await asyncpg.connect(settings.database_url)
    limits = httpx.Limits(max_connections=40, max_keepalive_connections=20)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=120, limits=limits) as client:
        def headers(subject, workspace=None):
            return {'Authorization': 'Bearer ' + tokens[subject]} | ({'X-Workspace-ID': workspace} if workspace else {})

        async def call(method, path, subject, workspace=None, category='control', **kwargs):
            stamp = time.monotonic()
            metrics['requests'] += 1
            try:
                response = await client.request(method, path, headers=headers(subject, workspace), **kwargs)
                if response.status_code not in {200, 201, 202, 204}:
                    raise RequestFailure(method, path, response.status_code, response_code(response))
                return response
            except Exception as exc:
                metrics['unexpected_errors'] += 1
                error = exc if isinstance(exc, RequestFailure) else RequestFailure(method, path, 0, type(exc).__name__)
                key = json.dumps(error.context, sort_keys=True)
                metrics['request_errors'][key] = metrics['request_errors'].get(key, 0) + 1
                raise error from None
            finally:
                if category in {'control', 'search'}:
                    metrics[category].append(time.monotonic() - stamp)

        try:
            personal = {}
            for subject in subjects:
                personal[subject] = (await call('GET', '/api/workspaces', subject, category='provision')).json()[0]['id']
            owner = subjects[0]
            corpus = args.existing_workspace
            if not corpus:
                corpus = (await call('POST', '/api/workspaces', owner, category='provision',
                                     json={'name': prefix + ' 500-document corpus'})).json()['id']
                for subject in subjects[1:]:
                    await call('PUT', f'/api/workspaces/{corpus}/members', owner, corpus, category='provision',
                               json={'subject': subject, 'role': 'researcher'})
            print(json.dumps({'phase': 'verify_existing_dataset' if args.existing_workspace else 'seed',
                              'workspace': corpus, 'accounts': len(subjects),
                              'documents': args.documents, 'chunks': args.documents * args.chunks_per_document}), flush=True)

            async def upload(index):
                content = document_content(index, args.chunks_per_document)
                response = await call('POST', '/api/documents', owner, corpus, category='provision',
                    files={'file': (f'capacity-{index}.md', content, 'text/markdown')})
                return response.json()['id']

            documents = (await existing_corpus(db, settings, corpus, subjects, args.documents, args.chunks_per_document)
                         if args.existing_workspace else [])
            seed_start = time.monotonic()
            for offset in range(0, 0 if args.existing_workspace else args.documents, args.upload_batch_size):
                batch_size = min(args.upload_batch_size, args.documents - offset)
                while time.monotonic() - seed_start < args.seed_timeout:
                    pending = await db.fetchval("SELECT COUNT(*) FROM documents WHERE status IN ('scanning','indexing','rebuilding')")
                    if pending + batch_size <= 8:
                        break
                    await asyncio.sleep(1)
                else:
                    raise TimeoutError('Dataset staging backlog did not drain within the seed deadline')
                documents.extend(await asyncio.gather(*(upload(index) for index in range(
                    offset, min(args.documents, offset + args.upload_batch_size)))))
                print(json.dumps({'phase': 'uploaded', 'count': len(documents)}), flush=True)
            while time.monotonic() - seed_start < args.seed_timeout:
                rows = await db.fetch('SELECT status,COUNT(*) AS total FROM documents WHERE user_id=$1 GROUP BY status', corpus)
                states = {row['status']: row['total'] for row in rows}
                if any(state not in {'ready', 'scanning', 'indexing'} for state in states):
                    raise RuntimeError('Document indexing failed: ' + json.dumps(states))
                if states.get('ready') == args.documents:
                    break
                await asyncio.sleep(2)
            else:
                raise TimeoutError('Dataset indexing deadline exceeded')
            chunk_count = await db.fetchval('SELECT COUNT(*) FROM chunks WHERE user_id=$1', corpus)
            if chunk_count != args.documents * args.chunks_per_document:
                raise RuntimeError(f'Unexpected indexed chunk coverage: {chunk_count}')
            # Search through the application, including ACL, SQL and Milvus.
            results = (await call('POST', '/api/documents/search', owner, corpus, category='search',
                                 json={'query': 'Source validation workflow', 'limit': 6})).json()
            if not results:
                raise RuntimeError('Indexed corpus is not retrievable')
            from backend.infrastructure.vectors import VectorIndex
            from backend.core.postgres import PostgresDatabase
            database = PostgresDatabase(settings.database_url)
            await database.init()
            try:
                index = VectorIndex(settings, database)
                if args.existing_workspace:
                    from backend.services.embedding_migration import embedding_fingerprint
                    recorded = await database.one("SELECT value FROM metadata WHERE key='embedding_fingerprint'")
                    if not recorded or recorded['value'] != embedding_fingerprint(settings):
                        raise ValueError('Existing corpus requires its original matching embedding fingerprint')
                await index.fingerprint()
                def check_vectors():
                    if args.existing_workspace and not index.client().has_collection(index._active_prefix + '_documents'):
                        raise ValueError('Existing corpus vector collection is absent')
                    vector_client, collection = index.ensure('documents')
                    vector_client.flush(collection)
                    iterator = vector_client.query_iterator(collection, filter='user_id == ' + json.dumps(corpus),
                        output_fields=['id'], batch_size=1000, consistency_level='Strong')
                    identifiers = set()
                    try:
                        while batch := iterator.next():
                            identifiers.update(row['id'] for row in batch)
                    finally:
                        iterator.close()
                    return identifiers
                vector_ids = await asyncio.to_thread(check_vectors)
                sql_ids = {row['id'] for row in await db.fetch('SELECT id FROM chunks WHERE user_id=$1', corpus)}
                vector_count = len(vector_ids)
                if vector_ids != sql_ids:
                    raise RuntimeError(f'Milvus/SQL ID coverage mismatch: {vector_count}/{chunk_count}')
            finally:
                await database.close()

            print(json.dumps({'phase': 'load', 'seconds': args.seconds, 'clients': args.clients,
                              'research_concurrency': args.research_concurrency}), flush=True)
            metrics['control'].clear()
            metrics['search'].clear()
            metrics['requests'] = metrics['unexpected_errors'] = 0
            load_start = time.monotonic()
            deadline = load_start + args.seconds
            sampling = True

            async def sample():
                while sampling:
                    states = await db.fetch("SELECT status,COUNT(*) AS total FROM runs WHERE status IN ('running','queued') GROUP BY status")
                    counts = {row['status']: row['total'] for row in states}
                    metrics['max_running'] = max(metrics['max_running'], counts.get('running', 0))
                    metrics['max_waiting'] = max(metrics['max_waiting'], counts.get('queued', 0))
                    workspace_active = await db.fetchval("SELECT COALESCE(MAX(total),0) FROM (SELECT COUNT(*) AS total FROM runs WHERE status='running' GROUP BY user_id) r")
                    metrics['max_workspace_running'] = max(metrics['max_workspace_running'], workspace_active)
                    await asyncio.sleep(.2)

            async def online(index):
                subject = subjects[index % len(subjects)]
                count = 0
                while time.monotonic() < deadline:
                    try:
                        if count % 10 == 9:
                            await call('POST', '/api/documents/search', subject, corpus, category='search',
                                       json={'query': 'Source validation workflow', 'limit': 6})
                        else:
                            await call('GET', ['/api/threads', '/api/documents', '/api/memories', '/api/workspaces'][count % 4],
                                       subject, corpus)
                        count += 1
                    except Exception as exc:
                        failures.append(type(exc).__name__)
                    await asyncio.sleep(random.uniform(.8, 1.2))

            async def research(index):
                subject = subjects[index % len(subjects)]
                # One lane exercises the full corpus; other personal spaces keep
                # the default two-per-workspace constraint while reaching five.
                workspace = corpus if index == 0 else personal[subject]
                count = 0
                while time.monotonic() < deadline:
                    try:
                        body = {'topic': 'Source validation research workflow', 'mode': 'quick',
                                'data_policy': 'internal', 'client_request_id': prefix + '-' + uid()}
                        run = (await call('POST', '/api/research/runs', subject, workspace, json=body)).json()
                        run_id = run['id']
                        metrics['accepted'].add(run_id)
                        if count % 10 == 0:
                            duplicate = (await call('POST', '/api/research/runs', subject, workspace, json=body)).json()
                            if duplicate['id'] != run_id:
                                raise RuntimeError('Duplicate request created a second business run')
                            metrics['duplicate_requests'] += 1
                        end = time.monotonic() + args.run_timeout
                        while time.monotonic() < end:
                            state = (await call('GET', f'/api/research/runs/{run_id}', subject, workspace)).json()
                            if state['status'] not in {'queued', 'running'}:
                                break
                            await asyncio.sleep(1)
                        else:
                            raise TimeoutError('Accepted research did not converge')
                        metrics['terminal'][run_id] = state['status']
                        if state['status'] not in {'completed', 'insufficient'}:
                            raise RuntimeError('Research ended in ' + state['status'])
                        report = await call('GET', f'/api/research/runs/{run_id}/report', subject, workspace, category='report')
                        if not report.content:
                            raise RuntimeError('Empty report download')
                        metrics['completed'].add(run_id)
                        count += 1
                    except Exception as exc:
                        failures.append(type(exc).__name__)
                        await asyncio.sleep(1)

            sampler = asyncio.create_task(sample())
            try:
                await asyncio.gather(*(online(index) for index in range(args.clients)),
                                     *(research(index) for index in range(args.research_concurrency)))
            finally:
                sampling = False
                await sampler
            duration = time.monotonic() - load_start
            done_counts = await db.fetch("SELECT r.id,COUNT(e.id) AS total FROM runs r LEFT JOIN events e ON e.run_id=r.id AND e.type='done' WHERE r.client_request_id LIKE $1 GROUP BY r.id", prefix + '-%')
            duplicate_or_missing_done = sum(row['total'] != 1 for row in done_counts)
            error_rate = metrics['unexpected_errors'] / max(1, metrics['requests'])
            p95 = percentile(metrics['control'])
            functional = (bool(metrics['accepted']) and metrics['accepted'] == metrics['completed']
                and not duplicate_or_missing_done and error_rate < .01
                and p95 is not None and p95 < 2000 and metrics['max_running'] <= 5
                and metrics['max_workspace_running'] <= 2 and metrics['max_waiting'] <= 20)
            contract = (args.seconds >= 1800 and args.accounts >= 50 and args.clients >= 10
                and args.research_concurrency == 5 and metrics['max_running'] == 5
                and args.documents == 500 and chunk_count == 50000)
            result = {'provider': 'deterministic fixture; no real model speed/cost/quality claim',
                'functional_passed': functional, 'production_capacity_passed': functional and contract,
                'workspace': corpus, 'accounts': len(subjects), 'online_clients': args.clients,
                'requested_seconds': args.seconds, 'observed_seconds': round(duration, 2),
                'documents': len(documents), 'sql_chunks': chunk_count, 'milvus_vectors': vector_count,
                'dataset_reused': bool(args.existing_workspace), 'vector_sql_ids_equal': True,
                'embedding_dimension': settings.embedding_dimension, 'requests': metrics['requests'],
                'unexpected_errors': metrics['unexpected_errors'], 'error_rate': round(error_rate, 6),
                'control_p95_ms': p95, 'search_p95_ms': percentile(metrics['search']),
                'accepted': len(metrics['accepted']), 'completed': len(metrics['completed']),
                'max_global_running': metrics['max_running'], 'max_workspace_running': metrics['max_workspace_running'],
                'max_global_waiting': metrics['max_waiting'], 'duplicate_requests': metrics['duplicate_requests'],
                'duplicate_or_missing_done_events': duplicate_or_missing_done,
                'failures_by_type': {kind: failures.count(kind) for kind in sorted(set(failures))},
                'request_error_contexts': [json.loads(key) | {'count': count} for key, count in sorted(metrics['request_errors'].items())],
                'dataset_content_sha256': hashlib.sha256(document_content(0, args.chunks_per_document)).hexdigest()}
        finally:
            await db.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result['functional_passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated', action='store_true')
    parser.add_argument('--base-url', default='http://api:8000')
    parser.add_argument('--tokens-json', type=Path)
    parser.add_argument('--existing-workspace', help='Reuse only after owner, members, original hashes, per-document chunks and vector IDs verify')
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--accounts', type=int, default=50)
    parser.add_argument('--clients', type=int, default=10)
    parser.add_argument('--research-concurrency', type=int, default=5)
    parser.add_argument('--documents', type=int, default=500)
    parser.add_argument('--chunks-per-document', type=int, default=100)
    parser.add_argument('--upload-batch-size', type=int, default=2)
    parser.add_argument('--seed-timeout', type=int, default=3600)
    parser.add_argument('--run-timeout', type=int, default=300)
    parser.add_argument('--report', type=Path, default=Path('.cache/eval/production-load.json'))
    arguments = parser.parse_args()
    if min(arguments.seconds, arguments.clients, arguments.accounts, arguments.research_concurrency,
           arguments.documents, arguments.chunks_per_document, arguments.upload_batch_size) < 1:
        parser.error('Counts and duration must be positive')
    if arguments.research_concurrency > min(arguments.accounts, 5):
        parser.error('Research lanes must be <= accounts and <= 5')
    if arguments.upload_batch_size > 4:
        parser.error('Upload staging concurrency must stay between 1 and 4; backlog is bounded at eight')
    try:
        status = asyncio.run(main(arguments))
    except (Exception, KeyboardInterrupt) as error:
        failure = {'functional_passed': False, 'production_capacity_passed': False,
                   'completed': False, 'failure_type': type(error).__name__}
        if isinstance(error, RequestFailure):
            failure['request_error_context'] = error.context
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(json.dumps(failure, indent=2), encoding='utf-8')
        print(json.dumps(failure), flush=True)
        raise
    raise SystemExit(status)
