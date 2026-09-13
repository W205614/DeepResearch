"""Run on a separate machine. Exit nonzero on an unavailable core API.

No credentials, model calls or outbound notifications. Wire the exit code into
the organization's existing scheduler/monitor, independently of the app host.
"""
import argparse
import json
import urllib.request
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query:
        parser.error("Use an HTTP(S) base URL without credentials or query parameters")
    try:
        for path, expected in (("/livez", "ok"), ("/readyz", "ready")):
            with urllib.request.urlopen(args.base_url.rstrip("/") + path, timeout=5) as response:
                if response.status != 200 or json.load(response).get("status") != expected:
                    raise RuntimeError("unhealthy")
        print(json.dumps({"core_available": True}))
    except Exception:
        print(json.dumps({"core_available": False}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
