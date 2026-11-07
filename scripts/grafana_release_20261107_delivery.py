"""Delivery for a separately approved exact subject; no active selector writes."""
from __future__ import annotations

import hashlib
import json
import urllib.error

from scripts.oci_artifact import verify_oci_archive
from scripts.publish_grafana_candidate import GitHub, verify_asset

PART_BYTES = 64 * 1024**2
REPOSITORY = 'W205614/DeepResearch'


def require(value, message):
    if not value:
        raise ValueError(message)


def write_json(path, value):
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode('utf-8'))


def part_plan(archive, binding):
    result, whole = [], hashlib.sha256()
    with archive.open('rb') as stream:
        while block := stream.read(PART_BYTES):
            require(len(result) < 32, 'Exact archive part limit exceeded')
            whole.update(block)
            result.append({'index': len(result), 'bytes': len(block),
                'sha256': hashlib.sha256(block).hexdigest(),
                'asset_name': 'grafana-' + binding['archive_sha256'][:8] + f'.oci.tar.part-{len(result):03d}'})
    require(result and sum(part['bytes'] for part in result) == binding['archive_bytes']
            and whole.hexdigest() == binding['archive_sha256'], 'Actual complete archive differs from the reviewed subject')
    return result


def prepare(api, archive):
    request = api.load_request(api.ROOT)
    binding = request['binding']
    proof = verify_oci_archive(archive, binding, max_archive_bytes=api.MAX_ARCHIVE_BYTES)
    return {'technical_ready': True, 'passed': False, 'approval_pending': True,
            'request_sha256': api.REQUEST_SHA256, 'subject': api.subject_for(binding),
            'actual_archive_proof': proof, 'parts': part_plan(archive, binding),
            'release_tag': api.RELEASE_TAG, 'target_commit': api.RELEASE_SOURCE_COMMIT,
            'network_or_Docker_operations': False, 'authorization_granted': False}


def tag_commit(client, release_tag):
    ref = client.call('/git/ref/tags/' + release_tag)['object']
    if ref['type'] == 'tag':
        ref = client.call('/git/tags/' + ref['sha'])['object']
    require(ref['type'] == 'commit', 'Release tag must resolve to the exact build commit')
    return ref['sha']


def publish(api, client, archive, output, *, release_id):
    # Authority precedes any HTTP, splitting or output creation. The old accepted
    # exact28/maintenance record cannot validate this new request and subject.
    request = api.load_request(api.ROOT)
    api.validate_authorization(api.read_json(api.ROOT / api.AUTH_PATH), request)
    require(type(release_id) is int and release_id > 0, 'A real exact draft release ID is required')
    require(output.resolve().is_relative_to((api.ROOT / '.cache/eval').resolve())
            and not output.exists(), 'Use a new publication output inside workspace .cache/eval')
    binding = request['binding']
    proof = verify_oci_archive(archive, binding, max_archive_bytes=api.MAX_ARCHIVE_BYTES)
    parts = part_plan(archive, binding)
    release = client.call(f'/releases/{release_id}')
    require(type(release.get('id')) is int and release['id'] == release_id
            and release.get('tag_name') == api.RELEASE_TAG and release.get('prerelease') is False
            and api.REQUEST_SHA256 in release.get('body', '')
            and binding['archive_sha256'] in release.get('body', ''),
            'The actual draft release does not bind this new request and subject')
    try:
        actual_commit = tag_commit(client, api.RELEASE_TAG)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        require(release['draft'] is True and release.get('target_commitish') == api.RELEASE_SOURCE_COMMIT,
                'Missing-tag draft must fix the actual build source')
    else:
        require(actual_commit == api.RELEASE_SOURCE_COMMIT, 'Another source tag cannot be published')
    assets = client.call(f'/releases/{release_id}/assets?per_page=100')
    names = [asset['name'] for asset in assets]
    expected_names = {part['asset_name'] for part in parts}
    require(len(names) == len(set(names)) and set(names) <= expected_names,
            'Duplicate or unrelated release assets are preserved and block publication')
    uploaded = {asset['name']: asset for asset in assets}
    output.mkdir(parents=True, exist_ok=False)
    records = []
    with archive.open('rb') as stream:
        for part in parts:
            block = stream.read(part['bytes'])
            require(hashlib.sha256(block).hexdigest() == part['sha256'], 'Archive changed after full verification')
            asset = uploaded.get(part['asset_name'])
            if asset is None:
                require(release['draft'] is True, 'Never append bytes to an already published release')
                file = output / part['asset_name']
                file.write_bytes(block)
                asset = client.call('', file=(release_id, part['asset_name'], file))
            verify_asset(asset, part)
            records.append({**part, 'asset_id': asset['id'], 'api_url': asset['url'],
                            'remote_digest': asset['digest'], 'remote_state': asset['state']})
        require(not stream.read(1), 'Archive grew during publication')
    current = client.call(f'/releases/{release_id}/assets?per_page=100')
    require(len(current) == len(parts) and {asset['name'] for asset in current} == expected_names,
            'Actual complete uploaded asset inventory differs')
    for asset in current:
        verify_asset(asset, next(part for part in parts if part['asset_name'] == asset['name']))
    # Recheck mutable authority after uploads, before making the exact draft public.
    require(api.load_request(api.ROOT) == request, 'Request inputs changed during publication')
    api.validate_authorization(api.read_json(api.ROOT / api.AUTH_PATH), request)
    if release['draft'] is True:
        client.call(f'/releases/{release_id}', method='PATCH', value={'draft': False, 'make_latest': 'false'})
    release = client.call(f'/releases/{release_id}')
    require(release['draft'] is False and type(release.get('published_at')) is str
            and release['tag_name'] == api.RELEASE_TAG
            and tag_commit(client, api.RELEASE_TAG) == api.RELEASE_SOURCE_COMMIT,
            'Actual published release/source identity differs')
    transport = {'schema': 'deepresearch-grafana-candidate-transport-v1', 'artifact': 'grafana',
        'repository': REPOSITORY, 'request_sha256': api.REQUEST_SHA256,
        'transport_mode': 'github_asset_parts', 'release_id': release_id, 'release_tag': api.RELEASE_TAG,
        'asset_name': 'grafana-' + binding['archive_sha256'][:8] + '.oci.tar',
        'asset_sha256': binding['archive_sha256'], 'archive_bytes': binding['archive_bytes'],
        'part_bytes': PART_BYTES, 'parts': records}
    write_json(output / 'transport.json', transport)
    publication = {'published': True, 'release_id': release_id, 'release_tag': api.RELEASE_TAG,
        'request_sha256': api.REQUEST_SHA256, 'subject': api.subject_for(binding), 'proof': proof,
        'actual_tag_commit': api.RELEASE_SOURCE_COMMIT, 'published_at': release['published_at'],
        'transport_sha256': hashlib.sha256((output / 'transport.json').read_bytes()).hexdigest()}
    write_json(output / 'publication.json', publication)
    return transport, publication


def authorized_client(token):
    return GitHub(token)
