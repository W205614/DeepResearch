"""Cross-platform enterprise Compose smoke check with no model or search calls."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request

COMPOSE = ["docker", "compose"]


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


def telemetry_backends_ready() -> None:
    backend_get("http://tempo:3200/ready")
    backend_get("http://loki:3100/ready")


def emit_telemetry_probe() -> None:
    """Emit one sanitized record so the smoke test exercises the live log pipeline."""
    command = (
        "from backend.core.config import Settings; "
        "from backend.core.observability import configure_task_logger,configure_telemetry; "
        "from opentelemetry import _logs; "
        "settings=Settings(); "
        "configure_telemetry(None,settings.otel_exporter_otlp_endpoint); "
        "configure_task_logger(settings.task_log_level).info('observability smoke probe'); "
        "assert _logs.get_logger_provider().force_flush(5000)"
    )
    run("exec", "-T", "backend", "python", "-c", command, capture=True)

def telemetry_records_ready() -> None:
    command = (
        "import json,urllib.parse,urllib.request; "
        "logs=json.load(urllib.request.urlopen('http://loki:3100/loki/api/v1/query_range?'+urllib.parse.urlencode({'query':'{service_name=\"deepresearch-api\"}','limit':'1'}),timeout=5)); "
        "assert logs['data']['result']; "
        "traces=json.load(urllib.request.urlopen('http://tempo:3200/api/search?'+urllib.parse.urlencode({'tags':'service.name=deepresearch-api','limit':'1'}),timeout=5)); "
        "assert traces.get('traces')"
    )
    run("exec", "-T", "backend", "python", "-c", command, capture=True)

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
    parser.add_argument("--start", action="store_true", help="Build and start the default enterprise Compose environment first")
    args = parser.parse_args()
    if args.start:
        run("up", "-d", "--build", "--force-recreate", "--wait", "--wait-timeout", "300")
    wait_for("web", lambda: read_json("http://127.0.0.1:8080/healthz"))
    wait_for("Keycloak OIDC", lambda: read_json(
        "http://127.0.0.1:8180/realms/deepresearch/.well-known/openid-configuration"))
    wait_for("Grafana and Prometheus targets", targets_ready)
    wait_for("Tempo and Loki", telemetry_backends_ready)
    read_json("http://127.0.0.1:8080/api/auth/config")
    emit_telemetry_probe()
    wait_for("stored application telemetry", telemetry_records_ready)
    version = run("exec", "-T", "postgres", "psql", "-U", "deepresearch", "-d", "deepresearch", "-tAc",
                  "SELECT version_num FROM alembic_version", capture=True).strip()
    if version != "0003_personal_memory_ownership":
        raise RuntimeError(f"Unexpected Alembic version: {version}")
    output = run("exec", "-T", "redis", "redis-cli", "ping", capture=True).strip()
    if output != "PONG":
        raise RuntimeError("Redis did not return PONG")
    wait_for("backend readiness", lambda: backend_get("http://127.0.0.1:8000/readyz"))
    wait_for("worker metrics", lambda: backend_get("http://worker:8001/metrics"))
    run("exec", "-T", "backend", "python", "-c", "import socket; socket.create_connection(('otel-collector',4317),timeout=5).close()")
    print("Enterprise smoke passed: web, OIDC, migration, Redis, backend, worker, Prometheus, Grafana, Tempo, Loki, and OTel.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Enterprise smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
