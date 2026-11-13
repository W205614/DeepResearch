import asyncio
import json
from pathlib import Path

import httpx
import pytest
import yaml

from backend.core.config import Settings
from backend.research.graph import ResearchGraph
from scripts.local import initialize
from tests.helpers import make_provider

ROOT = Path(__file__).resolve().parents[1]


def test_new_install_template_and_compose_wait_for_native_search(tmp_path, monkeypatch):
    for name in ('WEB_SEARCH_TIMEOUT_SECONDS', 'REQUEST_TIMEOUT_SECONDS'):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / '.env.example').write_bytes((ROOT / '.env.example').read_bytes())
    path = tmp_path / '.env'
    assert initialize(tmp_path, path)
    # The host template's object-store secret is supplied through Compose's
    # service-credential mapping; this focused settings load has no container.
    settings = Settings(_env_file=path, object_store_access_key='', object_store_secret_key='')
    assert settings.web_search_timeout_seconds == settings.request_timeout_seconds == 120
    configuration = yaml.safe_load((ROOT / 'compose.yaml').read_bytes())
    environment = configuration['services']['agent']['environment']
    assert environment['WEB_SEARCH_TIMEOUT_SECONDS'] == '${WEB_SEARCH_TIMEOUT_SECONDS:-120}'
    assert environment['REQUEST_TIMEOUT_SECONDS'] == '${REQUEST_TIMEOUT_SECONDS:-120}'
    worker = configuration['services']['worker']['environment']
    assert all(worker[key] == environment[key] for key in ('WEB_SEARCH_TIMEOUT_SECONDS', 'REQUEST_TIMEOUT_SECONDS'))


def test_existing_explicit_short_timeout_is_preserved_and_code_fallback_is_unchanged(tmp_path, monkeypatch):
    for name in ('WEB_SEARCH_TIMEOUT_SECONDS', 'REQUEST_TIMEOUT_SECONDS'):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / '.env'
    raw = b'WEB_SEARCH_TIMEOUT_SECONDS=20\nREQUEST_TIMEOUT_SECONDS=25\n'
    path.write_bytes(raw)
    assert initialize(tmp_path, path) is False and path.read_bytes() == raw
    settings = Settings(_env_file=path)
    assert settings.web_search_timeout_seconds == 20 and settings.request_timeout_seconds == 25
    defaults = Settings(_env_file=None)
    assert defaults.web_search_timeout_seconds == 20 and defaults.request_timeout_seconds == 90


@pytest.mark.parametrize('outer,inner,advance,expected', [(120, 120, 21, 'ok'),
                                                       (20, 120, 21, 'outer_timeout'),
                                                       (120, 20, 21, 'request_timeout')])
async def test_actual_graph_and_provider_timeout_propagation_without_wall_clock_wait(
        tmp_path, monkeypatch, outer, inner, advance, expected):
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    async def respond(request):
        calls.append(request)
        assert request.url == 'https://api.openai.com/v1/responses'
        assert json.loads(request.content)['max_tool_calls'] == 1
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return httpx.Response(200, json={'output': [{'type': 'web_search_call', 'action': {
            'type': 'search', 'sources': [{'url': 'https://source.test/report', 'title': 'Normal source'}]}}]})
    provider = await make_provider(tmp_path, respond)
    provider.settings.llm_provider = 'openai'
    provider.settings.llm_base_url = 'https://api.openai.com/v1'
    provider.settings.web_search_provider = 'openai'
    provider.settings.web_search_timeout_seconds = outer
    provider.settings.request_timeout_seconds = inner
    graph = ResearchGraph(provider.settings, provider.db, provider, None, None)
    async def read_fixture(_url):
        return 'Ordinary published source text.'
    monkeypatch.setattr(graph, 'read_web', read_fixture)
    loop, offset = asyncio.get_running_loop(), 0
    real_time = loop.time
    task = asyncio.create_task(graph.web_scout({'run_id': 'search', 'queries': ['normal question']}))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with monkeypatch.context() as clock:
            clock.setattr(loop, 'time', lambda: real_time() + offset)
            offset = advance
            # Run the actual event-loop deadlines and asyncio.Timeout cancellation.
            # Only elapsed time is controlled; neither timeout implementation is replaced.
            for _ in range(5):
                await asyncio.sleep(0)
            if expected == 'ok':
                assert not cancelled.is_set() and not task.done()
                release.set()
            else:
                await asyncio.wait_for(cancelled.wait(), 1)
            result = await task
        assert len(calls) == 1
        if expected == 'ok':
            assert len(result['web_results']) == 1
            assert result['web_outcomes'] == [{'source': 'web', 'outcome': 'ok'}]
        else:
            assert result['web_results'] == []
            assert result['web_outcomes'] == [{'source': 'web', 'outcome': 'error'}]
            warnings = await provider.db.rows("SELECT data FROM events WHERE type='warning'")
            messages = [json.loads(row['data'])['message'] for row in warnings]
            assert any(('联网搜索超时' if expected == 'outer_timeout' else '连接超时') in text for text in messages)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await provider.close()
