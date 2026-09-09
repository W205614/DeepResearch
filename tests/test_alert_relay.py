import httpx
import pytest
from pydantic import SecretStr

from backend.api.app import create_app
from backend.core.alerts import AlertRelay, deliver_with_retry, format_alert_message


def alert(status="firing"):
    return {"status": status, "fingerprint": "stable-alert", "labels": {"alertname": "QueueBacklog", "severity": "critical", "trace_id": "trace-1"}, "annotations": {"summary": "queue depth is high"}}


def test_relay_suppresses_repeated_status_but_keeps_recovery():
    relay = AlertRelay()
    firing = relay.pending({"alerts": [alert()]})
    relay.mark_delivered(firing)
    assert relay.pending({"alerts": [alert()]}) == []
    resolved = relay.pending({"alerts": [alert("resolved")]})
    assert len(resolved) == 1 and "[RESOLVED][critical]" in format_alert_message(resolved)["content"]["text"]


@pytest.mark.asyncio
async def test_delivery_retries_transient_webhook_failures():
    attempts = 0
    async def flaky_sender(url, body):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("temporary")
    await deliver_with_retry(flaky_sender, "http://webhook.test", {"ok": True})
    assert attempts == 3


@pytest.mark.asyncio
async def test_alert_endpoint_delivers_firing_once_and_recovery(settings):
    settings.feishu_webhook_url = SecretStr("http://webhook.test")
    app = create_app(settings)
    delivered = []
    async def sender(url, body):
        delivered.append((url, body))
    app.state.alert_sender = sender
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/internal/alerts", json={"alerts": [alert()]})
            duplicate = await client.post("/internal/alerts", json={"alerts": [alert()]})
            recovery = await client.post("/internal/alerts", json={"alerts": [alert("resolved")]})
    assert first.json()["delivered"] is True
    assert duplicate.json() == {"delivered": False, "reason": "deduplicated"}
    assert recovery.json()["statuses"] == ["resolved"]
    assert len(delivered) == 2
