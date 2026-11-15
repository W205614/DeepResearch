import hashlib
from http.client import IncompleteRead
import io
import json

import pytest

from scripts import prefetch_minio_verified_20261115 as transport


def source(body):
    return {'name': 'locked.source', 'url': 'https://official.example/locked.source',
            'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}


class Response(io.BytesIO):
    status = 200
    headers = {}

    def geturl(self):
        return 'https://official.example/locked.source'


def test_complete_get_checks_original_size_sha_and_keeps_receipt(tmp_path, monkeypatch):
    row = source(b'actual-source')
    directory, evidence = tmp_path / 'inputs', tmp_path / 'receipts'
    directory.mkdir()
    evidence.mkdir()

    def open_response(request, timeout):
        assert not request.has_header('Range') and timeout == 90
        return Response(b'actual-source')

    monkeypatch.setattr(transport, 'urlopen', open_response)
    result = transport.fetch_one(row, directory, evidence)
    assert (directory / row['name']).read_bytes() == b'actual-source'
    assert result['actual_sha256'] == row['sha256']


@pytest.mark.parametrize('wrong_body', [b'evil', b'shorter-than-the-pin'])
def test_wrong_complete_sha_or_size_fails_once_and_preserves_body(tmp_path, monkeypatch, wrong_body):
    row = source(b'good')
    calls = []
    monkeypatch.setattr(transport, 'urlopen', lambda *a, **k: calls.append(1) or Response(wrong_body))
    with pytest.raises(ValueError, match='locked.source: expected 4 bytes'):
        transport.fetch_one(row, tmp_path, tmp_path)
    assert len(calls) == 1 and not (tmp_path / row['name']).exists()
    assert (tmp_path / 'locked.source.attempt-1.failed.body').read_bytes() == wrong_body


def test_only_three_incomplete_attempts_and_each_partial_is_retained(tmp_path, monkeypatch):
    class Truncated(Response):
        def read(self, size):
            raise IncompleteRead(b'part', 5)

    calls = []
    monkeypatch.setattr(transport, 'urlopen', lambda *a, **k: calls.append(1) or Truncated())
    with pytest.raises(ValueError, match='three EOF/timeout attempts'):
        transport.fetch_one(source(b'wholebody'), tmp_path, tmp_path)
    assert len(calls) == 3
    for attempt in range(1, 4):
        assert (tmp_path / f'locked.source.attempt-{attempt}.failed.body').read_bytes() == b'part'
        receipt = json.loads((tmp_path / f'locked.source.attempt-{attempt}.json').read_bytes())
        assert receipt['error_type'] == 'IncompleteRead'


@pytest.mark.parametrize('kind', ['http', 'foreign', 'partial'])
def test_unapproved_transport_is_not_retried(tmp_path, monkeypatch, kind):
    response = Response(b'good')
    if kind == 'partial':
        response.status = 206
    else:
        response.geturl = lambda: ('http://official.example/locked.source' if kind == 'http'
                                   else 'https://unapproved.example/locked.source')
    calls = []
    monkeypatch.setattr(transport, 'urlopen', lambda *a, **k: calls.append(1) or response)
    with pytest.raises(ValueError):
        transport.fetch_one(source(b'good'), tmp_path, tmp_path)
    assert len(calls) == 1
