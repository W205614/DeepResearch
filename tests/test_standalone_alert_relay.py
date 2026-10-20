import httpx
import pytest

from scripts.alert_relay_service import create_app


@pytest.mark.asyncio
async def test_independent_relay_rejects_disabled_notifications_and_auth(monkeypatch):
    monkeypatch.setenv('ALERT_RELAY_TOKEN', 'relay-test')
    monkeypatch.delenv('ALERT_RELAY_TOKEN_FILE', raising=False)
    monkeypatch.delenv('FEISHU_WEBHOOK_URL', raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url='http://relay') as client:
        assert (await client.get('/livez')).json()['notifications_enabled'] is False
        assert (await client.post('/internal/alerts', json={})).status_code == 401
        assert (await client.post('/internal/alerts', headers={'Authorization': 'Bearer relay-test'}, json={})).status_code == 503


@pytest.mark.asyncio
async def test_independent_relay_delivers_and_retries_without_agent_runtime(monkeypatch):
    monkeypatch.setenv('ALERT_RELAY_TOKEN', 'relay-test')
    monkeypatch.delenv('ALERT_RELAY_TOKEN_FILE', raising=False)
    monkeypatch.setenv('FEISHU_WEBHOOK_URL', 'http://recorder/webhook')
    app = create_app()
    delivered = []

    async def sender(url, body):
        delivered.append(body)

    app.state.sender = sender
    payload = {'alerts': [{'fingerprint': 'one', 'status': 'firing', 'labels': {'alertname': 'AgentDown'}}]}
    headers = {'Authorization': 'Bearer relay-test'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://relay') as client:
        assert (await client.post('/internal/alerts', headers=headers, json=payload)).json()['delivered']
        assert (await client.post('/internal/alerts', headers=headers, json=payload)).json()['reason'] == 'deduplicated'
        payload['alerts'][0]['status'] = 'resolved'
        assert (await client.post('/internal/alerts', headers=headers, json=payload)).json()['statuses'] == ['resolved']
        assert (await client.post('/internal/alerts', headers=headers, json=[])).status_code == 422
    assert len(delivered) == 2
