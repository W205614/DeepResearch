"""Independent authenticated alert forwarding; no Agent, SQL or queue dependency."""
import hmac
import json
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request

from backend.core.alerts import AlertRelay, deliver_with_retry, format_alert_message, post_alert


def create_app():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    token_path = os.environ.get('ALERT_RELAY_TOKEN_FILE', '')
    token = Path(token_path).read_text().strip() if token_path else os.environ.get('ALERT_RELAY_TOKEN', '')
    webhook = os.environ.get('FEISHU_WEBHOOK_URL', '')
    app.state.sender = post_alert
    relay = AlertRelay()

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
        events = relay.pending(payload)
        if not events:
            return {'delivered': False, 'reason': 'deduplicated'}
        try:
            await deliver_with_retry(app.state.sender, webhook, format_alert_message(events))
        except httpx.HTTPError:
            raise HTTPException(502, '告警转发失败') from None
        if len(relay._last_status) > 10000:
            relay._last_status.clear()
        relay.mark_delivered(events)
        return {'delivered': True, 'statuses': [e['status'] for e in events]}

    return app
