"""Assert that the test Compose stack relays firing and resolved alerts to its local recorder."""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen


def request_json(url, method="GET", payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=8) as response:
        return json.loads(response.read() or b"{}")


def alert(ends_at):
    return {"labels": {"alertname": "DeepResearchRelayDrill", "severity": "warning"},
            "annotations": {"summary": "local recorder drill"},
            "startsAt": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(), "endsAt": ends_at.isoformat()}


def main(args):
    firing = alert(datetime.now(timezone.utc) + timedelta(minutes=5))
    request_json(args.alertmanager + "/api/v2/alerts", "POST", [firing, firing])
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        deliveries = request_json(args.recorder + "/deliveries").get("deliveries", [])
        texts = [item.get("content", {}).get("text", "") for item in deliveries]
        if any("[FIRING]" in text for text in texts):
            break
        time.sleep(1)
    else:
        raise RuntimeError("local recorder did not receive firing alert")
    request_json(args.alertmanager + "/api/v2/alerts", "POST", [alert(datetime.now(timezone.utc) - timedelta(seconds=1))])
    while time.time() < deadline:
        deliveries = request_json(args.recorder + "/deliveries").get("deliveries", [])
        if any("[RESOLVED]" in item.get("content", {}).get("text", "") for item in deliveries):
            print("Alert relay drill passed: firing and recovery reached the local recorder.")
            return
        time.sleep(1)
    raise RuntimeError("local recorder did not receive resolved alert")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--alertmanager", default="http://127.0.0.1:9093")
    parser.add_argument("--recorder", default="http://127.0.0.1:18090")
    parser.add_argument("--timeout", type=int, default=30)
    main(parser.parse_args())
