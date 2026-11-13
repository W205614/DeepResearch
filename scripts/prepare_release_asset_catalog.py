"""Prepare a read-only source-asset catalog; never move releases, tags or assets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import fetch_approved_image as approved  # noqa: E402
from scripts import fetch_grafana_candidate as candidate  # noqa: E402
from scripts import fetch_grafana_fixed_20261107 as fixed  # noqa: E402
from scripts import grafana_image_disposition, image_disposition  # noqa: E402

REPOSITORY = 'W205614/DeepResearch'
LEGACY_COMMITS = {
    'artifact-minio-fixed-20261026': '6bec3f00d883f0de0aac89b29d3a30cd7325fdf3',
    'artifact-grafana-reviewed-20261026': '6bec3f00d883f0de0aac89b29d3a30cd7325fdf3',
    'artifact-grafana-exact28-20261028': 'ee370ae7841f7ae7cb85f7705d07dfb7ddd20d5e',
    'artifact-grafana-maintenance-20261105': '0a18012496cfe591109d54ad7522c4a2330e97ba',
    'artifact-grafana-fixed-20261107': 'c6acf98ca5d753b6981153f49108f43c62dbef0d',
}


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return candidate.exact.strict_json(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def legacy_contracts(root=ROOT):
    rows = []
    for artifact, policy in (('minio', image_disposition), ('grafana', grafana_image_disposition)):
        binding = policy.load_binding(root)
        path = policy.POLICY
        transport = read(root / path)['transport']
        spec = approved.artifact_spec(artifact)
        if artifact == 'grafana':
            parts = approved.validate_parts(binding, transport)
        else:
            parts = [{'index': 0, 'asset_id': spec['asset_id'], 'asset_name': spec['asset_name'],
                      'bytes': binding['archive_bytes'], 'sha256': binding['archive_sha256'],
                      'api_url': f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{spec['asset_id']}"}]
        rows.append({'tag': spec['release_tag'], 'release_id': spec['release_id'],
            'binding': binding, 'transport': transport, 'parts': parts, 'artifact': artifact,
            'loader': 'scripts/fetch_approved_image.py', 'publication_function': 'verify_release_publication',
            'input_sha256': {path.as_posix(): digest(root / path)}})
    for profile in (candidate.selected_profile(None), candidate.maintenance_profile(), fixed.loader_profile()):
        consumer = profile.consumer
        request_path = consumer.REQUEST_PATH
        require(digest(root / request_path) == consumer.REQUEST_SHA256, 'Legacy request bytes changed')
        require(digest(root / profile.transport_path) == profile.transport_sha256, 'Legacy transport bytes changed')
        binding = read(root / request_path)['binding']
        transport = read(root / profile.transport_path)
        parts = candidate.validate_transport(transport, binding, profile=profile)
        rows.append({'tag': profile.release_tag, 'release_id': transport['release_id'],
            'binding': binding, 'transport': transport, 'parts': parts, 'profile': profile,
            'loader': 'scripts/fetch_grafana_candidate.py', 'publication_function': 'verify_publication',
            'input_sha256': {request_path.as_posix(): consumer.REQUEST_SHA256,
                             profile.transport_path.as_posix(): profile.transport_sha256}})
    require({row['tag'] for row in rows} == set(LEGACY_COMMITS), 'Exactly five legacy contracts are required')
    return rows


def prepare_catalog(releases, resolved_tags, contracts):
    require(isinstance(releases, list) and releases, 'Complete release inventory is missing')
    require(len({r['id'] for r in releases}) == len(releases)
            and len({r['tag_name'] for r in releases}) == len(releases), 'Duplicate release identity')
    refs = resolved_tags['tags']
    require(len({r['ref'] for r in refs}) == len(refs), 'Duplicate tag identity')
    tag_map = {r['ref'].removeprefix('refs/tags/'): r for r in refs}
    release_map = {r['tag_name']: r for r in releases}
    assets = [a for r in releases for a in r['assets']]
    require(len({a['id'] for a in assets}) == len(assets), 'Repeated asset ID across releases')
    result = []
    for contract in contracts:
        tag = contract['tag']
        require(tag in release_map and tag in tag_map, 'A required legacy Release or Tag is missing')
        release, ref = release_map[tag], tag_map[tag]
        require(release['id'] == contract['release_id'] and release['draft'] is False
                and release['published_at'] and ref['peeled_commit'] == LEGACY_COMMITS[tag],
                'A required legacy publication or source commit changed')
        remote = {a['id']: a for a in release['assets']}
        require(len(remote) == len(release['assets']) == len(contract['parts'])
                and len({a['name'] for a in release['assets']}) == len(remote),
                'Full legacy release asset inventory changed')
        parts = []
        for index, part in enumerate(contract['parts']):
            require(part['index'] == index and part['asset_id'] in remote, 'A legacy ordered asset is missing')
            asset = remote[part['asset_id']]
            api = f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{part['asset_id']}"
            browser = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{quote(part['asset_name'], safe='')}"
            require(asset['name'] == part['asset_name'] and asset['size'] == part['bytes']
                    and asset['digest'] == 'sha256:' + part['sha256'] and asset['state'] == 'uploaded'
                    and asset['url'] == part['api_url'] == api and asset['browser_download_url'] == browser,
                    'Legacy asset name, size, server SHA, state or URL differs from its pinned transport')
            parts.append({'index': index, 'asset_id': asset['id'], 'name': asset['name'],
                'bytes': asset['size'], 'sha256': part['sha256'], 'api_url': api, 'browser_download_url': browser})
        binding = contract['binding']
        require(sum(p['bytes'] for p in parts) == binding['archive_bytes'], 'Whole archive length differs')
        result.append({'tag': tag, 'release_id': release['id'], 'peeled_source_commit': ref['peeled_commit'],
            'release_api_url': f"https://api.github.com/repos/{REPOSITORY}/releases/{release['id']}",
            'release_url': release['html_url'], 'published_at': release['published_at'],
            'archive_bytes': binding['archive_bytes'], 'archive_sha256': binding['archive_sha256'],
            'ordered_assets': parts, 'frozen_input_sha256': contract['input_sha256'],
            'legacy_publication_guard': {'script': contract['loader'], 'function': contract['publication_function'],
                'requires_original_live_release_id_and_tag': True,
                'checks_live_asset_membership': contract['publication_function'] == 'verify_publication',
                'also_requires_original_git_tag': tag in ('artifact-minio-fixed-20261026', 'artifact-grafana-reviewed-20261026')},
            'retention': {'status': 'required_legacy_compatibility', 'original_release_deletion_allowed': False,
                'original_tag_deletion_allowed': False,
                'why_asset_ID_alone_is_insufficient': 'Every loader also verifies its original live Release ID/Tag. The exact28, maintenance and fixed20261107 loaders additionally verify asset membership in that Release.'},
            'future_product_mirror': {'destination_release': None, 'new_asset_ids': None,
                'required_content_identity': 'Every ordered part and the whole archive must retain the original bytes/SHA; API asset IDs may differ.',
                'legacy_release_still_required_after_mirroring': True,
                'anonymous_full_byte_verification_performed': False}})
    return {'schema': 'deepresearch-legacy-source-assets-catalog-v1', 'repository': REPOSITORY,
        'purpose': 'Read-only inventory and future product-version source index; no publication or deletion authority.',
        'snapshot_counts': {'tags': len(refs), 'releases': len(releases), 'assets': len(assets),
            'drafts': sum(r['draft'] for r in releases)},
        'legacy_release_count': len(result), 'legacy_asset_count': sum(len(r['ordered_assets']) for r in result),
        'legacy_archive_bytes': sum(r['archive_bytes'] for r in result), 'legacy_releases': result,
        'metadata_only': True, 'payload_downloaded_or_rebuilt': False,
        'remote_mutations': 0, 'binary_redistribution_approved': False,
        'unmodified_default_network_paths_require_all_five_original_publications': True,
        'explicit_local_archive_mode': 'Existing loaders support exact local archives separately; that does not prove the original default workflows work after a remote migration.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--releases', type=Path, required=True)
    parser.add_argument('--resolved-tags', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    require(not output.exists() and not args.output.is_symlink() and output.is_relative_to(ROOT / '.cache'),
            'Use a new local catalog output under this checkout .cache')
    catalog = prepare_catalog(read(args.releases), read(args.resolved_tags), legacy_contracts())
    catalog['input_snapshot_sha256'] = {'releases': digest(args.releases), 'resolved_tags': digest(args.resolved_tags)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes((json.dumps(catalog, indent=2) + '\n').encode())
    print(json.dumps({k: catalog[k] for k in ('snapshot_counts', 'legacy_release_count', 'legacy_asset_count', 'legacy_archive_bytes')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
