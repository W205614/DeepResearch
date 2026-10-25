import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from backend.core.reliability import ServiceError
from scripts.bounded_evaluation import Budget


def policy(**overrides):
    return dict(model_http=2, llm_http=2, search_http=1, embedding_http=1, reranker_http=1,
                total_http=2, input_tokens=100000, output_tokens=1000) | overrides


def test_budget_persists_and_counts_simultaneous_attempts(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    Budget(path, policy())
    def attempt(_):
        try:
            return Budget(path, policy()).reserve("llm", {"max_tokens": 10})
        except ServiceError as error:
            assert error.code == "evaluation_budget"
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(value is not None for value in pool.map(attempt, range(8))) == 2
    assert Budget(path, policy()).report()["http_requests"] == 2
    with pytest.raises(ValueError, match="cannot be reset"):
        Budget(path, policy(total_http=20))


def test_unknown_usage_keeps_reservation_and_blocks_output_overshoot(tmp_path):
    budget = Budget(tmp_path / "ledger", policy(output_tokens=10))
    index = budget.reserve("llm", {"max_tokens": 10})
    budget.settle(index, 200, {})
    assert budget.report()["usage_missing_requests"] == 1
    with pytest.raises(ServiceError, match="budget"):
        budget.reserve("llm", {"max_tokens": 1})
    budget.settle(index, 200, {"prompt_tokens": 3, "completion_tokens": 2})
    budget.reserve("llm", {"max_tokens": 8})
    assert budget.report()["actual_completion_tokens"] == 2


@pytest.mark.parametrize("status", [401, 402, 403, 429, 503])
def test_supplier_failure_halts_next_request(tmp_path, status):
    budget = Budget(tmp_path / "ledger", policy())
    budget.settle(budget.reserve("llm", {}), status, {})
    with pytest.raises(ServiceError) as error:
        budget.reserve("llm", {})
    assert error.value.code == "evaluation_stopped"


async def test_transport_counts_retries_without_storing_authorization(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "__init__", httpx.AsyncClient.__init__)
    budget = Budget(tmp_path / "ledger", policy())
    budget.install()
    async def response(request):
        return httpx.Response(200, json={"usage": {"prompt_tokens": 5, "completion_tokens": 2}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        await client.get("https://supplier.test/page")
        await asyncio.gather(*(client.post("https://supplier.test/chat/completions",
            headers={"Authorization": "Bearer secret-must-not-persist"}, json={"model": "test", "max_tokens": 8})
            for _ in range(2)))
        with pytest.raises(ServiceError):
            await client.post("https://supplier.test/chat/completions", headers={"Authorization": "Bearer secret"}, json={})
    report = budget.report()
    assert report["http_requests"] == 2 and report["actual_prompt_tokens"] == 10
    assert "secret-must-not-persist" not in str(report)


def test_explicit_stage_transfer_preserves_history_and_total(tmp_path):
    path = tmp_path / "ledger"
    original = policy(stages={"saved": 2, "holdout": 0})
    budget = Budget(path, original, "saved")
    budget.reserve("llm", {})
    authorization = tmp_path / "authorization.txt"
    authorization.write_text("Coordinator explicitly approved one stage transfer; total unchanged")
    with pytest.raises(ValueError, match="Aggregate"):
        Budget.amend_stages(path, original | {"total_http": 10}, authorization)
    transferred = original | {"stages": {"saved": 1, "holdout": 1}}
    Budget.amend_stages(path, transferred, authorization)
    continued = Budget(path, transferred, "holdout")
    continued.reserve("llm", {})
    report = continued.report()
    assert report["http_requests"] == 2
    assert report["policy_changes"][0]["http_at_transfer"] == 1
    with pytest.raises(ValueError, match="below recorded"):
        Budget.amend_stages(path, original, authorization)
