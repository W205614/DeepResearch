"""Independent authenticated alert forwarding; no Agent, SQL or queue dependency."""
import hmac
import json
import os
from pathlib import Path
import asyncio
import sqlite3
import time

import httpx
from fastapi import FastAPI, HTTPException, Request

from backend.core.alerts import AlertRelay, deliver_with_retry, format_alert_message, post_alert


class DurableAlertRelay(AlertRelay):
    """Persist only safe fingerprints/status, not webhook URLs or alert payloads."""
    def __init__(self, path):
        super().__init__()
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS deliveries(fingerprint TEXT PRIMARY KEY,status TEXT NOT NULL,updated_at REAL NOT NULL)")
                self._last_status = dict(conn.execute("SELECT fingerprint,status FROM deliveries"))

    def mark_delivered(self, events):
        if self.path:
            with sqlite3.connect(self.path) as conn:
                conn.executemany("INSERT INTO deliveries VALUES(?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at",
                                 [(event['fingerprint'], event['status'], time.time()) for event in events])
                conn.execute("DELETE FROM deliveries WHERE updated_at<?", (time.time() - 7 * 86400,))
                conn.execute("DELETE FROM deliveries WHERE fingerprint NOT IN (SELECT fingerprint FROM deliveries ORDER BY updated_at DESC LIMIT 10000)")
                self._last_status = dict(conn.execute("SELECT fingerprint,status FROM deliveries"))
        else:
            super().mark_delivered(events)


def create_app():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    token_path = os.environ.get('ALERT_RELAY_TOKEN_FILE', '')
    token = Path(token_path).read_text().strip() if token_path else os.environ.get('ALERT_RELAY_TOKEN', '')
    webhook = os.environ.get('FEISHU_WEBHOOK_URL', '')
    app.state.sender = post_alert
    relay = DurableAlertRelay(os.environ.get('ALERT_RELAY_STATE_FILE', ''))
    delivery_gate = asyncio.Lock()

    @app.get('/livez')
    async def live():
        return {'status': 'alive', 'notifications_enabled': bool(token and webhook)}

    @app.post('/internal/alerts')
    async def forward(request: Request):
        supplied = request.headers.get('authorization', '').removeprefix('Bearer ')
        if not token:
            raise HTTPException(503, '告警认证未配置')
        if not hmac.compare_digest(supplied, token):
            raise HTTPException(401, '告警来源未授权')
        if not webhook:
            raise HTTPException(503, '告警通知未启用')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 131072:
                raise HTTPException(413, '告警载荷过大')
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict) or not isinstance(payload.get('alerts'), list):
                raise ValueError()
            if len(payload['alerts']) > 100 or any(not isinstance(a, dict) for a in payload['alerts']):
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(422, '告警格式无效') from None
        async with delivery_gate:
            events = relay.pending(payload)
            if not events:
                return {'delivered': False, 'reason': 'deduplicated'}
            try:
                await deliver_with_retry(app.state.sender, webhook, format_alert_message(events))
            except httpx.HTTPError:
                raise HTTPException(502, '告警转发失败') from None
            relay.mark_delivered(events)
            return {'delivered': True, 'statuses': [e['status'] for e in events]}

    return app
