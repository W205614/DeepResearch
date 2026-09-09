"""Privacy-safe Alertmanager to Feishu relay helpers."""
import asyncio
import hashlib
import json

import httpx


SAFE_LABELS = ("alertname", "severity", "service", "component", "run_id", "trace_id")


class AlertRelay:
    """Suppress repeated status updates while allowing a recovery transition through."""
    def __init__(self):
        self._last_status: dict[str, str] = {}

    def pending(self, payload: dict) -> list[dict]:
        events = []
        for alert in payload.get("alerts", []):
            labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
            fingerprint = str(alert.get("fingerprint") or hashlib.sha256(
                json.dumps({key: labels.get(key, "") for key in SAFE_LABELS}, sort_keys=True).encode()).hexdigest()[:16])
            status = str(alert.get("status", "firing")).lower()
            if status not in {"firing", "resolved"} or self._last_status.get(fingerprint) == status:
                continue
            events.append({"fingerprint": fingerprint, "status": status,
                           "labels": {key: str(labels[key])[:120] for key in SAFE_LABELS if labels.get(key)},
                           "summary": str((alert.get("annotations") or {}).get("summary", labels.get("alertname", "alert")))[:300]})
        return events

    def mark_delivered(self, events: list[dict]) -> None:
        for event in events:
            self._last_status[event["fingerprint"]] = event["status"]


def format_alert_message(events: list[dict]) -> dict:
    lines = ["DeepResearch Alert"]
    for event in events:
        labels = event["labels"]
        identity = labels.get("alertname", "alert")
        severity = labels.get("severity", "warning")
        context = " ".join(f"{key}={labels[key]}" for key in ("service", "component", "run_id", "trace_id") if key in labels)
        lines.append(f"[{event['status'].upper()}][{severity}] {identity}: {event['summary']}" + (f" ({context})" if context else ""))
    return {"msg_type": "text", "content": {"text": "\n".join(lines)[:1800]}}


async def post_alert(webhook: str, body: dict) -> None:
    async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
        response = await client.post(webhook, json=body)
        response.raise_for_status()


async def deliver_with_retry(sender, webhook: str, body: dict, retries: int = 2) -> None:
    for attempt in range(retries + 1):
        try:
            await sender(webhook, body)
            return
        except httpx.HTTPError:
            if attempt == retries:
                raise
            await asyncio.sleep(0.2 * (2 ** attempt))
