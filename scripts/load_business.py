"""Bounded business load against isolated real components, with fixture models."""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from backend.core.config import Settings
from backend.core.auth import issue_development_token
from backend.core.db import uid


def percentile(values, fraction=.95):
    return sorted(values)[min(len(values)-1, int((len(values)-1)*fraction))] if values else 0


async def main(args):
    deadline = time.monotonic() + args.seconds
    metrics = {'requests': 0, 'unexpected_failures': 0, 'accepted': [], 'completed': [],
               'control_ms': [], 'research_ms': [], 'index_ms': [], 'queue_ms': []}
    async def client_loop():
        settings = Settings()
        headers = {'Authorization': 'Bearer ' + issue_development_token(settings, uid())}
        async with httpx.AsyncClient(base_url='http://api:8000', headers=headers, timeout=150) as client:
            async def call(method, url, **kwargs):
                start = time.monotonic()
                response = await client.request(method, url, **kwargs)
                metrics['requests'] += 1
                if not url.endswith('/events'):
                    metrics['control_ms'].append((time.monotonic()-start)*1000)
                if response.status_code not in {200, 201, 202, 204}:
                    metrics['unexpected_failures'] += 1
                response.raise_for_status()
                return response
            while time.monotonic() < deadline:
                try:
                    stamp = time.monotonic()
                    document = (await call('POST', '/api/documents', files={'file': ('load.txt',
                        ('Source validation workflow. Unique fixture ' + uid()).encode(), 'text/plain')})).json()
                    while time.monotonic()-stamp < 120:
                        docs = (await call('GET', '/api/documents')).json()
                        state = next(d['status'] for d in docs if d['id'] == document['id'])
                        if state == 'ready':
                            break
                        if state not in {'indexing', 'scanning'}:
                            raise RuntimeError('index failed')
                        await asyncio.sleep(.2)
                    else:
                        raise RuntimeError('index deadline')
                    metrics['index_ms'].append((time.monotonic()-stamp)*1000)
                    stamp = time.monotonic()
                    run = (await call('POST', '/api/research/runs', json={'data_policy':'public','topic': 'research workflow', 'client_request_id': uid()})).json()
                    metrics['accepted'].append(run['id'])
                    events = await call('GET', f"/api/research/runs/{run['id']}/events")
                    if events.text.count('"type": "done"') != 1:
                        raise RuntimeError('missing or duplicate terminal event')
                    from datetime import datetime
                    running = [json.loads(line[6:]) for line in events.text.splitlines()
                               if line.startswith('data: {') and '"type": "running"' in line]
                    if running:
                        metrics['queue_ms'].append((datetime.fromisoformat(running[0]['created_at'])-
                                                   datetime.fromisoformat(run['created_at'])).total_seconds()*1000)
                    result = (await call('GET', f"/api/research/runs/{run['id']}")).json()
                    if result['status'] != 'completed':
                        raise RuntimeError('research failed')
                    metrics['completed'].append(run['id'])
                    metrics['research_ms'].append((time.monotonic()-stamp)*1000)
                    await call('GET', f"/api/research/runs/{run['id']}/report")
                    await call('DELETE', f"/api/documents/{document['id']}")
                except Exception:
                    metrics['unexpected_failures'] += 1
                    await asyncio.sleep(1)
    await asyncio.gather(*(client_loop() for _ in range(args.clients)))
    result = {'clients': args.clients, 'duration_seconds': args.seconds, 'provider': 'deterministic fixture',
              'requests': metrics['requests'], 'unexpected_failures': metrics['unexpected_failures'],
              'accepted': len(metrics['accepted']), 'completed': len(metrics['completed']),
              'control_p95_ms': round(percentile(metrics['control_ms']), 2),
              'research_p95_ms': round(percentile(metrics['research_ms']), 2),
              'index_p95_ms': round(percentile(metrics['index_ms']), 2),
              'queue_p95_ms': round(percentile(metrics['queue_ms']), 2)}
    result['passed'] = (bool(metrics['accepted']) and metrics['accepted'] == metrics['completed']
                        and len(set(metrics['completed'])) == len(metrics['completed'])
                        and result['unexpected_failures']/max(result['requests'], 1) < .01
                        and result['control_p95_ms'] < 2000)
    # Completion order can differ between clients; compare sets too.
    result['passed'] = (result['passed'] or (
        bool(metrics['accepted']) and sorted(metrics['accepted']) == sorted(metrics['completed'])
        and len(set(metrics['completed'])) == len(metrics['completed'])
        and result['unexpected_failures']/max(result['requests'], 1) < .01 and result['control_p95_ms'] < 2000))
    print(json.dumps(result), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=300)
    parser.add_argument('--clients', type=int, default=5)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
