"""Actual legacy-publication metadata, exact catalog identities and migration counterexamples."""
from copy import deepcopy
import gzip
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from scripts import prepare_release_asset_catalog as catalog

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/release-migration-20261113'


@pytest.fixture(scope='module')
def contracts():
    return catalog.legacy_contracts(ROOT)


@pytest.fixture
def metadata():
    return (json.loads(gzip.decompress((FIXTURES / 'legacy-releases.json.gz').read_bytes())),
            json.loads(gzip.decompress((FIXTURES / 'resolved-tags.json.gz').read_bytes())))


def test_complete_real_legacy_asset_catalog_keeps_order_bytes_and_compatibility(metadata, contracts):
    releases, tags = metadata
    result = catalog.prepare_catalog(releases, tags, contracts)
    assert result['legacy_release_count'] == 5 and result['legacy_asset_count'] == 53
    assert result['legacy_archive_bytes'] == 3503526912
    for row in result['legacy_releases']:
        assert row['retention']['original_release_deletion_allowed'] is False
        assert row['retention']['original_tag_deletion_allowed'] is False
        assert row['future_product_mirror']['destination_release'] is None
        assert row['future_product_mirror']['legacy_release_still_required_after_mirroring']
        assert sum(p['bytes'] for p in row['ordered_assets']) == row['archive_bytes']
        assert [p['index'] for p in row['ordered_assets']] == list(range(len(row['ordered_assets'])))
    assert result['metadata_only'] and not result['payload_downloaded_or_rebuilt']


def test_generated_public_catalog_matches_every_actual_legacy_fixture_identity(metadata, contracts):
    releases, tags = metadata
    observed = catalog.prepare_catalog(releases, tags, contracts)
    public = catalog.read(ROOT / 'deployment/security/legacy-source-assets-catalog-20261113.json')
    assert public['legacy_releases'] == observed['legacy_releases']
    assert public['legacy_asset_count'] == 53 and public['metadata_only']
    assert public['remote_mutations'] == 0 and not public['binary_redistribution_approved']


@pytest.mark.parametrize('change', ['release-id', 'release-missing', 'tag-missing', 'moved-tag', 'draft',
                                    'asset-missing', 'asset-id', 'asset-bytes', 'asset-digest', 'asset-api', 'asset-browser'])
def test_changed_legacy_release_ref_or_asset_cannot_be_a_migration_success(metadata, contracts, change):
    releases, tags = metadata
    release = releases[0]
    asset = release['assets'][0]
    if change == 'release-id':
        release['id'] += 1
    elif change == 'release-missing':
        releases.pop(0)
    elif change == 'tag-missing':
        tags['tags'] = [r for r in tags['tags'] if r['ref'] != 'refs/tags/' + release['tag_name']]
    elif change == 'moved-tag':
        next(r for r in tags['tags'] if r['ref'] == 'refs/tags/' + release['tag_name'])['peeled_commit'] = '0' * 40
    elif change == 'draft':
        release['draft'] = True
    elif change == 'asset-missing':
        release['assets'].pop()
    else:
        key = {'asset-id': 'id', 'asset-bytes': 'size', 'asset-digest': 'digest',
               'asset-api': 'url', 'asset-browser': 'browser_download_url'}[change]
        asset[key] = asset[key] + 1 if key in {'id', 'size'} else 'https://example.invalid/unreviewed'
    with pytest.raises(ValueError):
        catalog.prepare_catalog(releases, tags, contracts)


class Response(BytesIO):
    def getcode(self):
        return 200


@pytest.mark.parametrize('tag', list(catalog.LEGACY_COMMITS))
@pytest.mark.parametrize('change', ['unchanged-real-metadata', 'retag-same-release-and-assets',
                                    'replace-release-object', 'delete-release'])
def test_original_frozen_loader_publication_predicate_requires_old_live_release(monkeypatch, metadata, contracts, tag, change):
    # Only the HTTP response boundary is replayed. The actual frozen validation
    # implementation and original request/transport files remain unchanged.
    releases, _ = metadata
    contract = next(r for r in contracts if r['tag'] == tag)
    release = deepcopy(next(r for r in releases if r['tag_name'] == tag))
    if change == 'retag-same-release-and-assets':
        release['tag_name'] = 'v99.0.0'
    elif change == 'replace-release-object':
        release['id'] += 1
    url = f"https://api.github.com/repos/{catalog.REPOSITORY}/releases/{contract['release_id']}"

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == url and timeout == 60
            if change == 'delete-release':
                raise HTTPError(url, 404, 'Not Found', {}, BytesIO(b''))
            return Response(json.dumps(release).encode())

    module = catalog.approved if 'artifact' in contract else catalog.candidate
    monkeypatch.setattr(module, 'build_opener', lambda *_: Opener())

    def evaluate():
        if 'artifact' in contract:
            return catalog.approved.verify_release_publication(contract['transport'], contract['artifact'])
        return catalog.candidate.verify_publication(contract['transport'], contract['binding'], profile=contract['profile'])

    if change == 'unchanged-real-metadata':
        assert evaluate()['published_verified_live'] is True
    else:
        with pytest.raises((ValueError, HTTPError)):
            evaluate()
