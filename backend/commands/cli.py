"""Command-line client for the running local DeepResearch service."""
import argparse
import json
import time
import urllib.error
import urllib.request
from uuid import uuid4


TERMINAL = {"completed", "insufficient", "failed", "cancelled", "interrupted"}


def request(base_url: str, path: str, *, method="GET", body=None, user="local-user", token=""):
    headers = {"X-User-ID": user}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(base_url.rstrip("/") + path, data=payload,
                                                           headers=headers, method=method), timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"服务请求失败（{exc.code}）：{detail}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"无法连接本地服务：{exc.reason}") from None


def research(args):
    run = request(args.base_url, "/api/research/runs", method="POST", user=args.user, token=args.token,
                  body={"topic": args.topic, "mode": args.mode, "client_request_id": uuid4().hex})
    while run["status"] not in TERMINAL:
        time.sleep(.8)
        run = request(args.base_url, f"/api/research/runs/{run['id']}", user=args.user, token=args.token)
    if args.json:
        print(json.dumps(run, ensure_ascii=False, indent=2))
    else:
        print(run.get("report") or run.get("error") or "研究未生成报告")
    return 0 if run["status"] in {"completed", "insufficient"} else 1


def main():
    parser = argparse.ArgumentParser(description="DeepResearch 本地 CLI（需先启动 Docker 服务）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080", help="本地 Web 服务地址")
    parser.add_argument("--user", default="local-user", help="本地演示用户标识")
    parser.add_argument("--token", default="", help="工作台访问令牌；未配置时留空")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("research", help="发起研究并在完成后输出 Markdown 报告")
    command.add_argument("topic", help="研究主题")
    command.add_argument("--mode", choices=["auto", "quick", "deep"], default="auto")
    command.add_argument("--json", action="store_true", help="输出完整运行记录 JSON")
    args = parser.parse_args()
    return research(args)


if __name__ == "__main__":
    raise SystemExit(main())
