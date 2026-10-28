import hashlib
from pathlib import Path

import pytest

from scripts import publish_grafana_candidate as publication


def test_exact_parts_preserve_complete_archive_and_every_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(publication, 'PART_BYTES', 8)
    archive = tmp_path / 'candidate.oci.tar'
    archive.write_bytes(b'exact authorized source bytes')
    parts = publication.make_parts(archive, tmp_path / 'parts')
    assert b''.join(part['path'].read_bytes() for part in parts) == archive.read_bytes()
    assert all(hashlib.sha256(part['path'].read_bytes()).hexdigest() == part['sha256'] for part in parts)
    assert [part['index'] for part in parts] == list(range(len(parts)))


@pytest.mark.parametrize('field,value', [('size', 1), ('digest', 'sha256:' + 'f' * 64),
    ('state', 'open'), ('name', 'another.bin'), ('id', False), ('url', 'https://untrusted.invalid/file')])
def test_remote_asset_metadata_cannot_replace_exact_uploaded_bytes(field, value):
    expected = {'asset_name': 'exact.part-000', 'bytes': 42, 'sha256': 'a' * 64}
    asset = {'id': 123, 'name': expected['asset_name'], 'size': expected['bytes'],
        'digest': 'sha256:' + expected['sha256'], 'state': 'uploaded',
        'url': 'https://api.github.com/repos/W205614/DeepResearch/releases/assets/123'}
    publication.verify_asset(asset, expected)
    asset[field] = value
    with pytest.raises(ValueError):
        publication.verify_asset(asset, expected)


def test_human_authorization_failure_prevents_network_or_filesystem_writes(monkeypatch):
    monkeypatch.setattr(publication.gate, 'load_request', lambda *_: {})
    monkeypatch.setattr(publication.gate, 'read_json', lambda *_: {'accepted': False})
    monkeypatch.setattr(publication.gate, 'validate_authorization',
                        lambda *_: (_ for _ in ()).throw(ValueError('Not authorized')))
    monkeypatch.setattr(publication.ancestor, 'source_identity', lambda: pytest.fail('Source operation before auth'))
    monkeypatch.setattr(publication.ancestor, 'download', lambda *_: pytest.fail('HTTP before auth'))
    monkeypatch.setattr(Path, 'mkdir', lambda *_a, **_k: pytest.fail('Write before auth'))
    with pytest.raises(ValueError, match='Not authorized'):
        publication.main()


def test_existing_draft_is_addressed_by_fixed_id_before_any_creation_attempt(tmp_path, monkeypatch):
    request = {'binding': {}}
    monkeypatch.setattr(publication.gate, 'load_request', lambda *_: request)
    monkeypatch.setattr(publication.gate, 'read_json', lambda *_: {'accepted': True})
    monkeypatch.setattr(publication.gate, 'validate_authorization', lambda *_: None)
    monkeypatch.setattr(publication, 'verify_oci_archive', lambda *_a, **_k: {})
    monkeypatch.setattr(publication, 'make_parts', lambda *_: [])
    observed = []

    class Client:
        def call(self, path, **kwargs):
            observed.append((path, kwargs))
            raise RuntimeError('Stop after the first request')

    with pytest.raises(RuntimeError, match='first request'):
        publication.publish(Client(), request, tmp_path / 'fixture.tar', tmp_path)
    assert observed == [(f'/releases/{publication.RELEASE_ID}', {})]
