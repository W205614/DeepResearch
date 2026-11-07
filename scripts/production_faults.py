"""Fault acceptance for the literal, isolated dr-production-test Compose project.

Host: python scripts/production_faults.py --only redis replicas worker
Container probes never print bearer tokens, SQL credentials or response bodies.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

COMPOSE = ['docker', 'compose', '--project-directory', '.', '--env-file', '.env.production-test',
    '-p', 'dr-production-test', '-f', 'compose.yaml', '-f', 'deployment/compose/compose.core.yaml',
    '-f', 'deployment/compose/compose.replicas.yaml', '-f', 'deployment/compose/compose.production-test.yaml']


async def probe(args):
    import asyncpg
    import httpx
    from backend.core.config import Settings
    from scripts.production_load import read_tokens
    if os.environ.get('INTEGRATION') != '1':
        raise RuntimeError('Probes require isolated fixture services with INTEGRATION=1')
    settings = Settings()
    tokens = read_tokens(args.tokens_json)
    subjects = list(tokens)
    if len(subjects) < 5:
        raise ValueError('Worker competition drill requires five distinct real test accounts')
    async with httpx.AsyncClient(base_url=args.base_url, timeout=20, trust_env=False) as client:
        async def request(method, path, subject=None, **kwargs):
            return await client.request(method, path, headers={'Authorization': 'Bearer ' + tokens[subject or subjects[0]]}, **kwargs)

        if args.probe in {'bootstrap', 'read', 'readonly'}:
            reads = []
            for index in range(3):
                subject = subjects[index]
                for path in ['/api/threads', '/api/documents', '/api/capabilities']:
                    response = await request('GET', path, subject)
                    reads.append(response.status_code)
            result = {'read_statuses': reads, 'history_available': all(status == 200 for status in reads)}
            if args.probe == 'readonly':
                writes = [await request('POST', '/api/threads', json={'title': 'must be rejected during outage'}),
                    await request('POST', '/api/research/runs', json={'topic': 'must be rejected during outage',
                        'data_policy': 'public', 'client_request_id': 'fault-' + str(time.time_ns())}),
                    await request('POST', '/api/documents', files={'file': ('denied.txt', b'fixture', 'text/plain')})]
                result['write_statuses'] = [response.status_code for response in writes]
                result['writes_denied'] = all(response.status_code == 503 for response in writes)
            return result
        if args.probe == 'create-slow':
            ids = []
            for subject in subjects[:5]:
                response = await request('POST', '/api/research/runs', subject, json={
                    'topic': 'slow-recovery isolated fault ' + str(time.time_ns()), 'mode': 'quick',
                    'data_policy': 'public', 'client_request_id': 'fault-' + str(time.time_ns())})
                if response.status_code != 202:
                    raise RuntimeError('Slow fixture request was not accepted: HTTP ' + str(response.status_code))
                ids.append(response.json()['id'])
            return {'runs': ids}
        if args.probe == 'state':
            ids = args.runs.split(',')
            db = await asyncpg.connect(settings.database_url)
            try:
                rows = await db.fetch('SELECT id,status,attempt_count FROM runs WHERE id=ANY($1::text[])', ids)
                paused = await db.fetchval("SELECT COUNT(*) FROM metadata WHERE key=ANY($1::text[])", ['slow:' + run for run in ids])
                done = await db.fetch("SELECT run_id,COUNT(*) AS total FROM events WHERE run_id=ANY($1::text[]) AND type='done' GROUP BY run_id", ids)
                return {'runs': [dict(row) for row in rows], 'paused': paused,
                        'done_events': {row['run_id']: row['total'] for row in done}}
            finally:
                await db.close()
    raise ValueError('Unknown probe')


def command(args, *, capture=False, timeout=60):
    return subprocess.run(args, check=True, capture_output=capture, text=True, encoding='utf-8', timeout=timeout)


def host_main(args):
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    # This command intentionally fixes project and env names so a copied run
    # cannot silently target the operator's daily Compose stack.
    if not (root / '.env.production-test').is_file():
        raise RuntimeError('Isolated .env.production-test is required')
    result = {'scope': 'dr-production-test; fixture models and real OIDC/PG/Redis/Milvus',
              'passed': False, 'checks': {}}
    stopped = set()

    def ids(service):
        values = command(COMPOSE + ['ps', '-q', service], capture=True).stdout.split()
        if not values:
            raise RuntimeError('Missing isolated service: ' + service)
        return values

    def run_probe(action, runs=None):
        call = COMPOSE + ['run', '--rm', '--no-deps', '-T', 'acceptance', 'python', 'scripts/production_faults.py',
                '--probe', action, '--base-url', args.base_url, '--tokens-json', args.tokens_json.as_posix()]
        if runs:
            call.extend(['--runs', ','.join(runs)])
        output = command(call, capture=True, timeout=240).stdout.strip().splitlines()
        return json.loads(output[-1])

    def stop_one(container):
        stopped.add(container)
        command(['docker', 'stop', '--time', '0', container], capture=True)

    def restore(container):
        command(['docker', 'start', container], capture=True)
        stopped.discard(container)

    def until(check, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = check()
            if value:
                return value
            time.sleep(2)
        raise TimeoutError('Isolated fault acceptance deadline exceeded')

    try:
        initial = run_probe('bootstrap')
        if not initial['history_available']:
            raise RuntimeError('Initial authenticated history unavailable')
        if 'redis' in args.only:
            redis = ids('redis')[0]
            stop_one(redis)
            try:
                readonly = run_probe('readonly')
                result['checks']['redis_readonly'] = readonly
                if not readonly['history_available'] or not readonly['writes_denied']:
                    raise RuntimeError('Redis outage violated read-only contract')
            finally:
                restore(redis)
            until(lambda: run_probe('read')['history_available'], 60)
        if 'replicas' in args.only:
            for service in ('backend', 'agent'):
                replicas = ids(service)
                if len(replicas) < 2:
                    raise RuntimeError('Replica drill requires two ' + service + ' replicas')
                stop_one(replicas[0])
                try:
                    value = run_probe('read')
                    result['checks'][service + '_replica_exit'] = value
                    if not value['history_available']:
                        raise RuntimeError('A single ' + service + ' exit interrupted history')
                finally:
                    restore(replicas[0])
                until(lambda: run_probe('read')['history_available'], 90)
        if 'worker' in args.only:
            workers = ids('worker')
            if len(workers) < 2:
                raise RuntimeError('Worker competition drill requires two workers')
            runs = run_probe('create-slow')['runs']

            def all_claimed():
                value = run_probe('state', runs)
                return value if value['paused'] == 5 and all(row['status'] == 'running' for row in value['runs']) else None

            until(all_claimed, 90)
            stamp = time.monotonic()
            stop_one(workers[0])
            restore(workers[0])

            def all_terminal():
                value = run_probe('state', runs)
                return value if len(value['runs']) == 5 and all(
                    row['status'] not in {'queued', 'running', 'interrupted'} for row in value['runs']) else None

            value = until(all_terminal, args.recovery_timeout)
            passed = (all(row['status'] in {'completed', 'insufficient'} for row in value['runs'])
                      and any(row['attempt_count'] >= 2 for row in value['runs'])
                      and all(value['done_events'].get(run) == 1 for run in runs))
            result['checks']['worker_recovery'] = value | {'passed': passed, 'seconds': round(time.monotonic() - stamp, 2)}
            if not passed:
                raise RuntimeError('Worker recovery did not converge with exactly one terminal result')
        result['passed'] = True
    except Exception as error:
        result['failure_type'] = type(error).__name__
        raise
    finally:
        for container in stopped:
            command(['docker', 'start', container], capture=True)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', choices=('bootstrap', 'read', 'readonly', 'create-slow', 'state'))
    parser.add_argument('--runs', default='')
    parser.add_argument('--base-url', default='http://edge:8080')
    parser.add_argument('--tokens-json', type=Path, default=Path('/receipts/tokens.json'))
    parser.add_argument('--only', nargs='+', choices=('redis', 'replicas', 'worker'), default=['redis', 'replicas', 'worker'])
    parser.add_argument('--recovery-timeout', type=int, default=210)
    parser.add_argument('--report', type=Path, default=Path('.cache/production-acceptance/faults.json'))
    arguments = parser.parse_args()
    if arguments.probe:
        print(json.dumps(asyncio.run(probe(arguments))), flush=True)
    else:
        raise SystemExit(host_main(arguments))
