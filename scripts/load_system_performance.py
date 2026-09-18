"""Isolated end-to-end pressure and fault-injection probe. Never target production."""
import argparse
import asyncio
import json
import math
import os
import time
from collections import Counter
from pathlib import Path

import httpx


BACKEND = os.getenv("PERFORMANCE_BACKEND_URL", "http://backend:8080")
KEYCLOAK = os.getenv("PERFORMANCE_KEYCLOAK_URL", "http://keycloak:8080")
METRICS = os.getenv("PERFORMANCE_METRICS_URL", BACKEND)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]


async def wait_for_keycloak(client: httpx.AsyncClient) -> str:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            response = await client.post(f"{KEYCLOAK}/realms/master/protocol/openid-connect/token", data={
                "client_id": "admin-cli", "grant_type": "password", "username": "perf-admin",
                "password": "performance-only"})
            if response.status_code == 200:
                return response.json()["access_token"]
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1)
    raise RuntimeError("Keycloak did not become ready")


async def provision_tokens(client: httpx.AsyncClient, count: int) -> list[str]:
    admin_token = await wait_for_keycloak(client)
    headers = {"Authorization": "Bearer " + admin_token}
    admin = f"{KEYCLOAK}/admin/realms/deepresearch-perf"
    response = await client.post(admin + "/clients", headers=headers, json={
        "clientId": "performance-load", "publicClient": True, "directAccessGrantsEnabled": True,
        "standardFlowEnabled": False, "protocolMappers": [{
            "name": "audience", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
            "config": {"included.client.audience": "deepresearch-api", "access.token.claim": "true"}}]})
    if response.status_code not in {201, 409}:
        response.raise_for_status()
    tokens = []
    for index in range(count):
        username = f"perf-user-{index:03d}"
        password = f"performance-only-{index:03d}"
        user = {"username": username, "enabled": True, "emailVerified": True,
                "email": username + "@example.invalid", "firstName": "Performance", "lastName": "Fixture",
                "credentials": [{"type": "password", "value": password, "temporary": False}]}
        response = await client.post(admin + "/users", headers=headers, json=user)
        if response.status_code not in {201, 409}:
            response.raise_for_status()
        if response.status_code == 409:
            existing = await client.get(admin + "/users", headers=headers,
                                        params={"username": username, "exact": "true"})
            existing.raise_for_status()
            user_id = existing.json()[0]["id"]
            updated = await client.put(admin + "/users/" + user_id, headers=headers,
                                       json={key: value for key, value in user.items() if key != "credentials"})
            updated.raise_for_status()
            reset = await client.put(admin + f"/users/{user_id}/reset-password", headers=headers,
                                     json={"type": "password", "value": password, "temporary": False})
            reset.raise_for_status()
        token = await client.post(f"{KEYCLOAK}/realms/deepresearch-perf/protocol/openid-connect/token", data={
            "client_id": "performance-load", "grant_type": "password", "username": username, "password": password})
        token.raise_for_status()
        tokens.append(token.json()["access_token"])
    return tokens


async def fixed_rate(client: httpx.AsyncClient, path: str, tokens: list[str], qps: int, seconds: float):
    latencies: list[float] = []
    statuses: Counter[str] = Counter()
    started = time.monotonic()
    tasks = []

    async def one(index: int):
        scheduled = started + index / qps
        await asyncio.sleep(max(0, scheduled - time.monotonic()))
        before = time.monotonic()
        try:
            response = await client.get(BACKEND + path, headers={"Authorization": "Bearer " + tokens[index % len(tokens)]})
            statuses[str(response.status_code)] += 1
        except httpx.HTTPError as error:
            statuses[type(error).__name__] += 1
        latencies.append((time.monotonic() - before) * 1000)

    total = int(qps * seconds)
    for index in range(total):
        tasks.append(asyncio.create_task(one(index)))
    await asyncio.gather(*tasks)
    elapsed = time.monotonic() - started
    return {
        "target_qps": qps, "offered": total, "elapsed_seconds": round(elapsed, 3),
        "achieved_qps": round(total / elapsed, 2), "status": dict(statuses),
        "p50_ms": round(percentile(latencies, .5), 2), "p95_ms": round(percentile(latencies, .95), 2),
        "p99_ms": round(percentile(latencies, .99), 2), "max_ms": round(max(latencies, default=0), 2),
    }


async def slow_upstream_scenario(client: httpx.AsyncClient, slow_token: str, control_token: str):
    async def slow_call():
        before = time.monotonic()
        response = await client.get(BACKEND + "/api/status?perf_delay_ms=2000&perf_status=200",
                                    headers={"Authorization": "Bearer " + slow_token})
        return response.status_code, round((time.monotonic() - before) * 1000, 2), response.text

    slow_tasks = [asyncio.create_task(slow_call()) for _ in range(24)]
    await asyncio.sleep(.05)
    control = await fixed_rate(client, "/api/threads", [control_token], 10, 2)
    slow = await asyncio.gather(*slow_tasks)
    return {
        "slow_status": dict(Counter(str(item[0]) for item in slow)),
        "slow_p95_ms": round(percentile([item[1] for item in slow], .95), 2),
        "fast_rejections": sum(1 for status, duration, _ in slow if status == 503 and duration < 500),
        "control": control,
    }


async def circuit_scenario(client: httpx.AsyncClient, token: str):
    bodies = []
    statuses = []
    for _ in range(6):
        response = await client.get(BACKEND + "/api/status?perf_status=503",
                                    headers={"Authorization": "Bearer " + token})
        statuses.append(response.status_code)
        bodies.append(response.text)
    opened = any("熔断" in body for body in bodies)
    await asyncio.sleep(3.2)
    recovered = await client.get(BACKEND + "/api/status?perf_status=200",
                                 headers={"Authorization": "Bearer " + token})
    return {"statuses": statuses, "circuit_open_response": opened, "recovery_status": recovered.status_code}


async def research_scenario(client: httpx.AsyncClient, token: str):
    headers = {"Authorization": "Bearer " + token}
    created = []
    started = time.monotonic()
    for index in range(2):
        response = await client.post(BACKEND + "/api/research/runs", headers=headers, json={
            "topic": f"performance fixture business scenario {index}", "data_policy": "public",
            "client_request_id": f"perf-{time.time_ns()}-{index}"})
        response.raise_for_status()
        created.append(response.json()["id"])
    terminal = {}
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and len(terminal) < len(created):
        for run_id in created:
            if run_id in terminal:
                continue
            response = await client.get(BACKEND + f"/api/research/runs/{run_id}", headers=headers)
            response.raise_for_status()
            body = response.json()
            if body["status"] in {"completed", "failed", "interrupted", "cancelled"}:
                terminal[run_id] = body["status"]
        await asyncio.sleep(.2)
    return {"created": len(created), "terminal": dict(Counter(terminal.values())),
            "duration_ms": round((time.monotonic() - started) * 1000, 2)}


def first_pressure_signal(stages):
    for stage in stages:
        total = max(1, stage["offered"])
        non_200 = total - stage["status"].get("200", 0)
        if non_200 / total > .01:
            return {"target_qps": stage["target_qps"], "reason": "http_error", "status": stage["status"]}
        if stage["p95_ms"] > 2000:
            return {"target_qps": stage["target_qps"], "reason": "p95_over_2s", "p95_ms": stage["p95_ms"]}
    return {"target_qps": stages[-1]["target_qps"], "reason": "no_collapse_within_test_ceiling"}


async def main(args):
    limits = httpx.Limits(max_connections=512, max_keepalive_connections=128)
    async with httpx.AsyncClient(timeout=15, limits=limits, trust_env=False) as client:
        tokens = await provision_tokens(client, args.principals)
        if args.tokens_file:
            target = Path(args.tokens_file)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(tokens), encoding="utf-8")
            target.chmod(0o600)
            print(json.dumps({"prepared_tokens": len(tokens), "path": str(target)}), flush=True)
            return 0
        # Create each personal workspace before timing read pressure.
        await asyncio.gather(*(client.get(BACKEND + "/api/threads", headers={"Authorization": "Bearer " + token})
                               for token in tokens))
        baseline = await fixed_rate(client, "/api/threads", tokens, 50, 2)
        tenant_overload = await fixed_rate(client, "/api/threads", [tokens[0]], 100, 2)
        await asyncio.sleep(2)
        slow = await slow_upstream_scenario(client, tokens[1], tokens[2])
        circuit = await circuit_scenario(client, tokens[3])
        research = await research_scenario(client, tokens[4])
        stages = []
        for qps in args.ramp:
            stages.append(await fixed_rate(client, "/api/threads", tokens, qps, args.stage_seconds))
            await asyncio.sleep(.5)
        metrics = await client.get(METRICS + "/metrics")
        selected_metrics = [line for line in metrics.text.splitlines() if line.startswith((
            "deepresearch_business_agent_", "deepresearch_business_rate_limit_", "hikaricp_connections_",
            "tomcat_threads_", "http_server_requests_seconds_"))]
    result = {
        "scope": "isolated Docker stack; deterministic Agent/model/search fixtures",
        "baseline": baseline,
        "tenant_overload": tenant_overload,
        "slow_upstream": slow,
        "circuit": circuit,
        "research_chain": research,
        "system_ramp": stages,
        "first_pressure_signal": first_pressure_signal(stages),
        "selected_metrics": selected_metrics[:120],
    }
    result["passed"] = (
        baseline["status"].get("200", 0) == baseline["offered"]
        and tenant_overload["status"].get("429", 0) > 0
        and slow["fast_rejections"] >= 8
        and slow["control"]["status"].get("200", 0) == slow["control"]["offered"]
        and slow["control"]["p95_ms"] < 1000
        and circuit["circuit_open_response"] and circuit["recovery_status"] == 200
        and research["terminal"].get("completed", 0) == research["created"])
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--principals", type=int, default=64)
    parser.add_argument("--stage-seconds", type=float, default=3)
    parser.add_argument("--ramp", type=int, nargs="+", default=[100, 250, 500, 800, 1200])
    parser.add_argument("--tokens-file")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
