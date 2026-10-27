"""Evaluation entrypoints retain both persistent application and transport budgets."""
from argparse import Namespace
import json

import httpx
import pytest

from backend.core.config import Settings
from backend.core.db import Database
from backend.core.reliability import ServiceError
from backend.core.usage_governance import paid_identity
from backend.infrastructure.providers import Providers
from scripts.bounded_evaluation import Budget
from scripts.evaluation_scope import scoped_evaluation


async def test_cli_scope_preserves_shared_transport_budget_and_application_reservations(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, '__init__', httpx.AsyncClient.__init__)
    policy = dict(model_http=1, llm_http=1, search_http=1, embedding_http=1, reranker_http=1,
                  total_http=1, input_tokens=100000, output_tokens=1000)
    budget = Budget(tmp_path / 'transport-ledger', policy)
    budget.install()
    db = Database(tmp_path / 'application-ledger.sqlite3')
    await db.init()
    settings = Settings(_env_file=None, llm_api_key='fixture-only', demo_mode=False)
    sent = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
        sent.append(request) or httpx.Response(200, json={'usage': {'prompt_tokens': 4, 'completion_tokens': 1}})))
    provider = Providers(settings, db, client)
    @scoped_evaluation
    async def evaluate():
        assert paid_identity() == ('evaluator', 'evaluator')
        await provider.post('https://fixture.test/chat/completions', 'fixture-only', {'max_tokens': 8}, 'fixture', retries=0)
        with pytest.raises(ServiceError) as error:
            await provider.post('https://fixture.test/chat/completions', 'fixture-only', {'max_tokens': 8}, 'fixture', retries=0)
        assert error.value.code == 'evaluation_budget'
    assert paid_identity() is None
    await evaluate()
    assert paid_identity() is None
    assert budget.report()['http_requests'] == 1 and len(sent) == 1
    attempts = await db.rows('SELECT * FROM usage_attempts')
    assert len(attempts) == 2 and all(row['purpose'] == 'evaluation' for row in attempts)
    await provider.close()


def test_each_live_evaluation_entrypoint_explicitly_declares_scoped_accounting():
    from scripts import evaluate_answers, evaluate_local_qa, evaluate_retrieval, evaluate_research
    for entrypoint in (evaluate_answers.main_async, evaluate_local_qa.main_async,
                       evaluate_retrieval.main_async, evaluate_research.evaluate):
        assert hasattr(entrypoint, '__wrapped__')


async def test_retrieval_demo_creates_evaluator_membership_and_never_needs_real_provider(tmp_path, monkeypatch):
    from scripts.evaluate_retrieval import main_async
    # The demo has no transport calls, even when developer .env contains live keys.
    monkeypatch.setattr(httpx.AsyncClient, 'post', lambda *_args, **_kwargs: pytest.fail('unexpected provider HTTP'))
    (tmp_path / 'fixture.txt').write_text('ALPHA lexical retrieval evidence.', encoding='utf-8')
    (tmp_path / 'corpus.json').write_text(json.dumps([{'path': 'fixture.txt', 'name': 'fixture.txt'}]))
    (tmp_path / 'cases.json').write_text(json.dumps([{'id': 'fixture', 'query': 'ALPHA', 'relevant_documents': ['fixture.txt']}]))
    output = tmp_path / 'output' / 'retrieval.json'
    await main_async(Namespace(with_reranker=False, cutoffs=[1], corpus=str(tmp_path / 'corpus.json'),
                              cases=str(tmp_path / 'cases.json'), output=str(output), demo=True, max_k=1,
                              require_reranker_gate=False))
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['dataset']['mode'] == 'demo'
    db = Database(output.parent / 'retrieval-eval-data' / 'research.sqlite3')
    assert (await db.membership('evaluator', 'evaluator'))['role'] == 'admin'
