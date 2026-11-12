"""The local transport never substitutes an unauthenticated partial database."""
import hashlib

import pytest

from scripts.go_native_database_transport_20261112 import copy_verified_archive
from scripts.check_native_jvm import NativeGateError


def example(tmp_path):
    data = b'synthetic archive transport, not a real advisory database'
    source = tmp_path / 'source.archive'
    source.write_bytes(data)
    checksum = hashlib.sha256(data).hexdigest()
    spec = {'url': 'https://grype.anchore.io/databases/v6/unit-only.tar.zst?checksum=sha256:' + checksum,
            'archive_sha256': checksum}
    return data, source, spec, tmp_path / 'copied.archive'


def test_complete_archive_is_checked_and_copied_without_reusing_a_hydrated_database(tmp_path):
    data, source, spec, destination = example(tmp_path)
    evidence = {}
    copy_verified_archive(spec, source, destination, evidence)
    assert destination.read_bytes() == source.read_bytes() == data
    assert evidence['archive']['verified']
    assert evidence['archive']['existing_hydrated_database_reused'] is False


def test_partial_archive_is_preserved_and_rejected_before_import(tmp_path):
    data, source, spec, destination = example(tmp_path)
    source.write_bytes(data[:10])
    with pytest.raises(NativeGateError, match='differs from the current publisher snapshot'):
        copy_verified_archive(spec, source, destination, {})
    assert source.read_bytes() == data[:10] and not destination.exists()


@pytest.mark.parametrize('mutation', ['http', 'wrong_checksum', 'duplicate_checksum'])
def test_archive_cannot_bypass_the_original_url_and_checksum_boundary(tmp_path, mutation):
    _, source, spec, destination = example(tmp_path)
    if mutation == 'http':
        spec['url'] = spec['url'].replace('https:', 'http:')
    elif mutation == 'wrong_checksum':
        spec['url'] = spec['url'].replace(spec['archive_sha256'], 'a' * 64)
    else:
        spec['url'] += '&checksum=sha256:' + spec['archive_sha256']
    with pytest.raises(NativeGateError, match='URL/checksum'):
        copy_verified_archive(spec, source, destination, {})
    assert source.exists() and not destination.exists()
