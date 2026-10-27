"""Real loopback HTTP delivery, failure/retry and restart-dedup verification.

The only receiver is an ephemeral local test server. No real notification URL
or user payload is accepted by this executable.
"""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import uvicorn
from scripts.alert_relay_service import create_app


async def verify(state):
    attempts, accepted = [], []
    remaining_failures = [3]

    class Recorder(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            attempts.append(body)
            if remaining_failures[0]:
                remaining_failures[0] -= 1
                self.send_response(503)
            else:
                accepted.append(body)
                self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')

        def log_message(self, *_):
            pass

    receiver = ThreadingHTTPServer(('127.0.0.1', 0), Recorder)
    thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    os.environ.update(ALERT_RELAY_TOKEN='controlled-fixture-token', ALERT_RELAY_STATE_FILE=str(state),
                      FEISHU_WEBHOOK_URL=f'http://127.0.0.1:{receiver.server_port}/webhook')
    os.environ.pop('ALERT_RELAY_TOKEN_FILE', None)
    payload = {'alerts': [{'fingerprint': 'delivery-fixture', 'status': 'firing', 'labels': {'alertname': 'ControlledOnly'}}]}
    headers = {'Authorization': 'Bearer controlled-fixture-token'}
    stages = []
    try:
        for restart in (False, True):
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            address = f'http://127.0.0.1:{sock.getsockname()[1]}'
            server = uvicorn.Server(uvicorn.Config(create_app(), log_level='critical', access_log=False))
            task = asyncio.create_task(server.serve(sockets=[sock]))
            try:
                async with asyncio.timeout(5):
                    while not server.started:
                        await asyncio.sleep(0.02)
                async with httpx.AsyncClient(base_url=address, trust_env=False, timeout=10) as client:
                    if not restart:
                        assert (await client.post('/internal/alerts', json=payload)).status_code == 401
                        failed = await client.post('/internal/alerts', json=payload, headers=headers)
                        assert failed.status_code == 502 and not accepted and len(attempts) == 3
                        stages.append('upstream_failure_not_marked_delivered')
                        responses = await asyncio.gather(*(client.post('/internal/alerts', json=payload, headers=headers) for _ in range(3)))
                        assert sum(response.json()['delivered'] for response in responses) == 1
                        stages.append('concurrent_recovery_sent_once')
                    else:
                        assert (await client.post('/internal/alerts', json=payload, headers=headers)).json()['reason'] == 'deduplicated'
                        stages.append('restart_duplicate_suppressed')
                        payload['alerts'][0]['status'] = 'resolved'
                        assert (await client.post('/internal/alerts', json=payload, headers=headers)).json()['delivered']
                        stages.append('resolved_transition_delivered')
            finally:
                server.should_exit = True
                await task
                sock.close()
    finally:
        receiver.shutdown()
        receiver.server_close()
        thread.join(timeout=2)
    assert len(accepted) == 2 and len(attempts) == 5
    return {'passed': True, 'scope': 'real HTTP to a controlled loopback receiver; no real person notified',
            'transport_attempts': len(attempts), 'accepted_notifications': len(accepted), 'stages': stages,
            'model_calls': 0}


if __name__ == '__main__':
    root = Path('.cache/optimization-20261026/alert-delivery')
    root.mkdir(parents=True, exist_ok=True)
    # A unique file preserves every previous attempt instead of clearing evidence.
    import uuid
    state = root / (uuid.uuid4().hex + '.sqlite3')
    report = asyncio.run(verify(state))
    Path('docs/evidence/optimization-20261026/alert-delivery-isolated.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
