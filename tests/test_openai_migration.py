import json

import httpx
import pytest

from backend.core.config import Settings
from backend.core.reliability import ServiceError
from tests.helpers import make_provider
from scripts.openai_smoke import run_smoke


def official(provider):
    provider.settings.llm_provider = 'openai'
    provider.settings.llm_model_id = 'gpt-6.1-sol'
    provider.settings.vision_model_id = 'gpt-6.1-sol'
    provider.settings.llm_base_url = 'https://api.openai.com/v1'
    provider.settings.web_search_provider = 'openai'
    return provider


def test_openai_defaults_preserve_specialized_embedding_and_bge_reranker():
    settings = Settings(_env_file=None)
    assert settings.llm_model_id == settings.vision_model_id == 'gpt-6.1-sol'
    assert settings.llm_base_url == settings.embedding_base_url == 'https://api.openai.com/v1'
    assert settings.embedding_model == 'text-embedding-3-large' and settings.embedding_dimension == 3072
    assert settings.llm_reasoning_effort == 'low' and settings.send_embedding_dimensions
    assert settings.web_search_provider == 'openai'
    assert settings.reranker_model == 'Pro/BAAI/bge-reranker-v2-m3'


def test_legacy_api_check_is_configuration_only_without_http_or_ledger(tmp_path, monkeypatch):
    from scripts.check_apis import inspect_configuration
    def forbidden(*args, **kwargs):
        raise AssertionError('Configuration checks must not construct a network client')
    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    report = inspect_configuration(Settings(_env_file=None, llm_api_key='fixture', embedding_api_key='fixture'))
    assert report['configuration_present'] and report['supplier_http_requests'] == 0
    assert report['live_connectivity'] == 'not_checked'
    assert 'openai_smoke.py' in report['next_step']
    assert not list(tmp_path.iterdir())


async def test_openai_chat_omits_unsupported_sampling_and_accounts_all_output_tokens(tmp_path):
    from backend.domain.models import Route
    calls = []
    def respond(request):
        calls.append(request)
        body = json.loads(request.content)
        assert request.url == 'https://api.openai.com/v1/chat/completions'
        assert body['max_completion_tokens'] == 8000 and body['reasoning_effort'] == 'low'
        assert body['store'] is False and body['response_format'] == {'type': 'json_object'}
        assert not {'max_tokens', 'thinking', 'temperature', 'top_p'} & body.keys()
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"mode":"quick","reason":"ok"}'}}],
                                        'usage': {'prompt_tokens': 9, 'completion_tokens': 17,
                                                  'completion_tokens_details': {'reasoning_tokens': 12}}})
    provider = official(await make_provider(tmp_path, respond))
    try:
        result = await provider.structured('router', 'test', {}, Route, 'run', temperature=0)
        assert result.mode == 'quick' and len(calls) == 1
        ledger = await provider.db.one('SELECT * FROM usage_attempts')
        assert ledger['actual_output'] == 17 and ledger['reserved_tokens'] >= 8000
        counter = await provider.db.one("SELECT * FROM counters WHERE run_id='run'")
        assert counter['completion_tokens'] == 17
    finally:
        await provider.close()


async def test_openai_vision_uses_same_supported_request_options(tmp_path):
    def respond(request):
        body = json.loads(request.content)
        assert body['model'] == 'gpt-6.1-sol'
        assert body['max_completion_tokens'] == 8000 and 'max_tokens' not in body
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"readable":true,"text":"合成图"}'}}]})
    provider = official(await make_provider(tmp_path, respond))
    try:
        assert await provider.describe_image(b'synthetic-png', 'image/png') == '合成图'
    finally:
        await provider.close()


async def test_openai_search_uses_native_sources_and_never_falls_back(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        body = json.loads(request.content)
        assert request.url.path == '/v1/responses'
        assert body['max_tool_calls'] == 1
        assert body['reasoning'] == {'effort': 'low'}
        assert body['include'] == ['web_search_call.action.sources'] and body['store'] is False
        return httpx.Response(200, json={'output': [{'type': 'web_search_call', 'action': {
            'type': 'search', 'sources': [{'url': 'https://source.test/report', 'title': 'Source'}]}}],
            'output_text': 'https://invented.test/ignored'})
    provider = official(await make_provider(tmp_path, respond))
    try:
        assert await provider.search('question', 'search') == [
            {'url': 'https://source.test/report', 'name': 'Source', 'summary': ''}]
        assert len(calls) == 1
    finally:
        await provider.close()


async def test_openai_search_failure_does_not_send_a_bocha_request(tmp_path):
    from pydantic import SecretStr
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(503, json={})
    provider = official(await make_provider(tmp_path, respond))
    provider.settings.bocha_api_key = SecretStr('other-provider-key')
    try:
        with pytest.raises(ServiceError):
            await provider.search('question', 'search')
        assert len(calls) == 1 and calls[0].url.host == 'api.openai.com'
    finally:
        await provider.close()


async def test_vendor_extra_parameters_are_rejected_before_openai_call(tmp_path):
    from backend.domain.models import Route
    calls = []
    provider = official(await make_provider(tmp_path, lambda request: calls.append(request)))
    provider.settings.llm_extra_body = '{"thinking":{"type":"disabled"}}'
    try:
        with pytest.raises(ServiceError) as result:
            await provider.structured('router', 'test', {}, Route, '')
        assert result.value.code == 'provider_configuration' and not calls
    finally:
        await provider.close()


async def test_openai_mode_rejects_third_party_credential_destination_before_call(tmp_path):
    from backend.domain.models import Route
    calls = []
    provider = official(await make_provider(tmp_path, lambda request: calls.append(request)))
    provider.settings.llm_base_url = 'https://third-party.test/v1'
    try:
        with pytest.raises(ServiceError) as error:
            await provider.structured('router', 'test', {}, Route, '')
        assert error.value.code == 'provider_configuration' and not calls
        with pytest.raises(ServiceError):
            await provider.openai_search('question', '')
        assert not calls
    finally:
        await provider.close()


async def test_smoke_is_official_only_four_calls_and_durably_cannot_repeat(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        assert request.url.host == 'api.openai.com'
        assert request.headers['authorization'] == 'Bearer fixture-secret'
        body = json.loads(request.content)
        if request.url.path.endswith('/embeddings'):
            assert body['dimensions'] == 3072
            return httpx.Response(200, json={'data': [{'embedding': [1.0] * 3072}], 'usage': {'total_tokens': 5}})
        if request.url.path.endswith('/responses'):
            assert body['max_tool_calls'] == 1
            return httpx.Response(200, json={'output': [{'type': 'web_search_call', 'action': {
                'type': 'search', 'sources': [{'url': 'https://developers.openai.com'}]}}]})
        content = body['messages'][0]['content']
        answer = '{"color":"red"}' if isinstance(content, list) else '{"ok":true}'
        return httpx.Response(200, json={'choices': [{'message': {'content': answer}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        path = tmp_path / 'ledger.sqlite3'
        first = await run_smoke('fixture-secret', path, client=client)
        second = await run_smoke('fixture-secret', path, client=client)
    assert first['passed'] and second['passed'] and len(calls) == 4
    assert 'fixture-secret' not in json.dumps(first)
    assert not first['historical_vectors_rebuilt'] and first['full_quality_evaluation'] == 'not_run'


@pytest.mark.parametrize('failure', ['http', 'network', 'malformed'])
async def test_smoke_stops_on_failure_and_never_retries_unknown_or_failed(tmp_path, failure):
    calls = []
    def respond(request):
        calls.append(request)
        if failure == 'network':
            raise httpx.ReadTimeout('private upstream data', request=request)
        return httpx.Response(401, json={'error': 'private upstream data'}) if failure == 'http' else httpx.Response(200, json=[])
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        first = await run_smoke('fixture-secret', tmp_path / 'ledger.sqlite3', client=client)
        second = await run_smoke('fixture-secret', tmp_path / 'ledger.sqlite3', client=client)
    assert not first['passed'] and not second['passed'] and len(calls) == 1
    assert 'private upstream data' not in json.dumps(first)
