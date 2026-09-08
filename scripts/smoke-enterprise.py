"""Cross-platform enterprise Compose smoke check with no model or search calls."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request

COMPOSE = ["docker", "compose", "-f", "compose.yaml", "-f", "compose.enterprise.yaml"]


def run(*arguments: str, capture: bool = False) -> str:
    process = subprocess.run([*COMPOSE, *arguments], check=False, text=True, capture_output=capture)
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or process.stdout.strip() or "Docker Compose command failed")
    return process.stdout


def read_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for(label: str, action, attempts: int = 30) -> None:
    error = None
    for _ in range(attempts):
        try:
            action()
            return
        except Exception as exc:
            error = exc
            time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {label}: {error}")


def backend_get(url: str) -> None:
    run("exec", "-T", "backend", "python", "-c", (
        "import urllib.request; "
        f"assert urllib.request.urlopen('{url}', timeout=5).status == 200"
    ), capture=True)


def targets_ready() -> None:
    payload = read_json("http://127.0.0.1:3000/api/health")
    if payload.get("database") != "ok":
        raise RuntimeError("Grafana database is not ready")
    command = (
        "import json,urllib.request; "
        "data=json.load(urllib.request.urlopen('http://prometheus:9090/api/v1/targets',timeout=5)); "
        "targets=data['data']['activeTargets']; "
        "assert {x['labels'].get('job') for x in targets if x['health']=='up'} "
        ">= {'deepresearch-api','deepresearch-worker'}"
    )
    run("exec", "-T", "backend", "python", "-c", command, capture=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", action="store_true", help="Build and start the enterprise Compose profile first")
    args = parser.parse_args()
    if args.start:
        run("up", "-d", "--build", "--wait", "--wait-timeout", "300")
    wait_for("web", lambda: read_json("http://127.0.0.1:8080/healthz"))
    wait_for("Keycloak OIDC", lambda: read_json(
        "http://127.0.0.1:8180/realms/deepresearch/.well-known/openid-configuration"))
    wait_for("Grafana and Prometheus targets", targets_ready)
    version = run("exec", "-T", "postgres", "psql", "-U", "deepresearch", "-d", "deepresearch", "-tAc",
                  "SELECT version_num FROM alembic_version", capture=True).strip()
    if version != "0001_enterprise_workspace":
        raise RuntimeError(f"Unexpected Alembic version: {version}")
    output = run("exec", "-T", "redis", "redis-cli", "ping", capture=True).strip()
    if output != "PONG":
        raise RuntimeError("Redis did not return PONG")
    wait_for("backend readiness", lambda: backend_get("http://127.0.0.1:8000/readyz"))
    wait_for("worker metrics", lambda: backend_get("http://worker:8001/metrics"))
    run("exec", "-T", "backend", "python", "-c", "import socket; socket.create_connection(('otel-collector',4317),timeout=5).close()")
    print("Enterprise smoke passed: web, OIDC, migration, Redis, backend, worker, Prometheus, Grafana, and OTel.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Enterprise smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
