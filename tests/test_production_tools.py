import json
from unittest.mock import AsyncMock, Mock

import jwt
import pytest

import httpx

from scripts.production_load import RequestFailure, document_content, existing_corpus, read_tokens, response_code
from backend.services.document_parsing import split_document


def test_capacity_dataset_uses_production_parser_and_unique_documents():
    first, last = document_content(0, 100), document_content(499, 100)
    assert first != last
    assert len(split_document('load.md', first)) == len(split_document('load.md', last)) == 100


def test_capacity_tool_accepts_keycloak_provisioner_token_list_without_printing_tokens(tmp_path):
    path = tmp_path / 'tokens.json'
    token = jwt.encode({'sub': 'known-oidc-subject'}, 'fixture-secret-with-enough-characters')
    path.write_text(json.dumps([token]), encoding='utf-8')
    assert read_tokens(path) == {'known-oidc-subject': token}
    path.write_text(json.dumps({'known-subject': token}), encoding='utf-8')
    assert read_tokens(path) == {'known-subject': token}


@pytest.mark.parametrize('value', [None, 'token', {'subject': ''}, {'': 'token'}, {'subject': 1}])
def test_capacity_tool_rejects_invalid_token_file(tmp_path, value):
    path = tmp_path / 'tokens.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError):
        read_tokens(path)


def test_capacity_error_context_keeps_routes_and_stable_code_without_response_detail():
    error = RequestFailure('POST', '/api/documents/' + 'a' * 32 + '/reindex?private=value', 503, 'queue_unavailable')
    assert error.context == {'method': 'POST', 'route': '/api/documents/{id}/reindex',
                             'http_status': 503, 'code': 'queue_unavailable'}
    assert RequestFailure('POST', '/api/documents/search', 503, 'queue_unavailable').context['route'] == '/api/documents/search'
    response = httpx.Response(503, json={'error': {'code': 'queue_unavailable'}, 'detail': 'private fixture text'})
    assert response_code(response) == 'queue_unavailable'
    assert 'private' not in str(error)


def test_capacity_error_context_does_not_use_arbitrary_provider_text_as_metric_code():
    response = httpx.Response(503, json={'code': 'private prose that must remain private'})
    assert response_code(response) == 'http_503'


async def test_existing_capacity_dataset_refuses_wrong_owner_before_any_object_access(settings):
    database = Mock(fetchval=AsyncMock(return_value='someone-else'), fetch=AsyncMock())
    with pytest.raises(ValueError, match='owned'):
        await existing_corpus(database, settings, 'space', ['alice'], 1, 1)
    database.fetch.assert_not_called()


async def test_existing_capacity_dataset_refuses_viewer_or_missing_fixture_members(settings):
    database = Mock(fetchval=AsyncMock(return_value='alice'),
                    fetch=AsyncMock(return_value=[{'subject': 'alice', 'role': 'viewer'}]))
    with pytest.raises(ValueError, match='writer access'):
        await existing_corpus(database, settings, 'space', ['alice'], 1, 1)
