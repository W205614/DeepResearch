import hashlib
from http.client import IncompleteRead
import io
import json
from urllib.error import HTTPError

import pytest

from scripts import prefetch_minio_verified_20261115 as transport


def test_real_vendored_original_key_is_exact_and_never_downloaded(tmp_path, monkeypatch):
    row = dict(transport.VENDORED_KEY)
    monkeypatch.setattr(transport, 'urlopen', lambda *a, **k: pytest.fail('Vendored key must not use a mutable keyserver response'))
    receipt = transport.fetch_one(row, tmp_path, tmp_path)
    assert receipt['transport'] == 'vendored_exact_original_public_key'
    assert receipt['HTTP_download_claimed'] is False
    assert (tmp_path / row['name']).read_bytes() == transport.VENDORED_KEY_PATH.read_bytes()
    assert transport.frozen.digest(tmp_path / row['name']) == row['sha256']


def test_modified_vendored_key_is_rejected(tmp_path, monkeypatch):
    changed = tmp_path / 'changed-key.asc'
    body = transport.VENDORED_KEY_PATH.read_bytes()
    changed.write_bytes(body[:-1] + bytes([body[-1] ^ 1]))
    monkeypatch.setattr(transport, 'VENDORED_KEY_PATH', changed)
    with pytest.raises(ValueError, match='original public key bytes differ'):
        transport.fetch_one(dict(transport.VENDORED_KEY), tmp_path, tmp_path)


@pytest.mark.parametrize('field,value', [('url', 'https://official.example/other-key'),
                                        ('bytes', 6196), ('sha256', '0' * 64)])
def test_vendored_key_rejects_changed_lock_tuple(tmp_path, field, value):
    row = {**transport.VENDORED_KEY, field: value}
    with pytest.raises(ValueError, match='exact unchanged name/URL/size/SHA lock tuple'):
        transport.fetch_one(row, tmp_path, tmp_path)


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


def test_only_locked_alpine_apk_can_use_official_mirror_after_404(tmp_path, monkeypatch):
    name = 'libssl3-3.5.9-r0.apk'
    tail = transport.ALPINE_APK_PATH + name
    origin = 'https://dl-cdn.alpinelinux.org/alpine' + tail
    mirror_url = transport.ALPINE_ARCHIVE_MIRRORS[0] + tail
    payload = b'locked-apk'
    row = {**source(payload), 'name': name, 'url': origin}
    lock = tmp_path / 'lock.json'
    lock.write_text(json.dumps({'inputs': [row]}), encoding='utf-8')
    monkeypatch.setattr(transport.frozen, 'LOCK', lock)
    calls = []

    def open_response(request, timeout):
        calls.append(request.full_url)
        if request.full_url == origin:
            raise HTTPError(origin, 404, 'Not Found', {}, None)
        response = Response(payload)
        response.geturl = lambda: mirror_url
        return response

    monkeypatch.setattr(transport, 'urlopen', open_response)
    rejected = tmp_path / 'rejected'
    rejected.mkdir()
    with pytest.raises(HTTPError, match='404'):
        transport.fetch_one({**row, 'sha256': '0' * 64}, rejected, rejected)
    assert calls == [origin]

    result = transport.fetch_one(row, tmp_path, tmp_path)
    primary_receipt = tmp_path / f'{name}.attempt-1.json'
    assert json.loads(primary_receipt.read_bytes())['http_status'] == 404
    assert calls == [origin, origin, mirror_url]
    assert result['locked_url'] == origin and result['final_url'] == mirror_url
    assert (tmp_path / name).read_bytes() == payload
