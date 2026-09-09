from prometheus_client import generate_latest

from backend.core.db import uid
from backend.core.metrics import RUN_TERMINAL_STATUSES, initialize_run_terminal_metrics


async def test_worker_metrics_expose_anonymous_run_node_and_token_series(api_client):
    app, client = api_client
    run = (await client.post("/api/research/runs", json={"topic": "指标验证", "client_request_id": uid()})).json()
    await app.state.runtime.tasks[run["id"]]
    metrics = generate_latest().decode("utf-8")
    assert "deepresearch_runs_total" in metrics
    assert "deepresearch_node_duration_seconds" in metrics
    assert "deepresearch_llm_calls_total" in metrics
    assert 'deepresearch_tokens_total{kind="prompt"}' in metrics
    assert "user_id" not in metrics and "topic" not in metrics
def test_terminal_run_metrics_have_zero_baselines_before_work():
    initialize_run_terminal_metrics()
    metrics = generate_latest().decode("utf-8")
    for status in RUN_TERMINAL_STATUSES:
        assert f'deepresearch_runs_total{{status="{status}"}}' in metrics
