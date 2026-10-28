"""Publish only the explicitly authorized, already verified Grafana OCI bytes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_candidate_exact28 as gate  # noqa: E402
from scripts import reuse_grafana_candidate as ancestor  # noqa: E402
from scripts.oci_artifact import verify_oci_archive  # noqa: E402

REPOSITORY = 'W205614/DeepResearch'
RELEASE_TAG = 'artifact-grafana-exact28-20261028'
RELEASE_ID = 426729499
ASSET_NAME = 'grafana-849d3d86.oci.tar'
PART_BYTES = 64 * 1024**2
OUTPUT = ROOT / '.cache/eval/grafana-publication'


class GitHub:
    def __init__(self, token):
        gate.require(bool(token), 'An Actions publication token is required')
        self.token = token
        self.opener = urllib.request.build_opener(ancestor.NoRedirect)

    def call(self, path, *, method='GET', value=None, file=None):
        url = f'https://api.github.com/repos/{REPOSITORY}{path}'
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                   'User-Agent': 'DeepResearch-authorized-exact28-publication',
                   'X-GitHub-Api-Version': '2022-11-28'}
        payload = json.dumps(value).encode() if value is not None else None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        if file is not None:
            release_id, name, path = file
            url = (f'https://uploads.github.com/repos/{REPOSITORY}/releases/{release_id}/assets?'
                   + urllib.parse.urlencode({'name': name}))
            headers.update({'Content-Type': 'application/octet-stream', 'Content-Length': str(path.stat().st_size)})
            with path.open('rb') as stream:
                with self.opener.open(urllib.request.Request(url, data=stream, headers=headers, method='POST'),
                                      timeout=300) as response:
                    return json.load(response)
        with self.opener.open(urllib.request.Request(url, data=payload, headers=headers, method=method),
                              timeout=60) as response:
            return json.load(response)


def verify_asset(asset, expected):
    gate.require(type(asset.get('id')) is int and asset['id'] > 0
                 and asset.get('name') == expected['asset_name']
                 and asset.get('size') == expected['bytes']
                 and asset.get('digest') == 'sha256:' + expected['sha256']
                 and asset.get('state') == 'uploaded'
                 and asset.get('url') == f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{asset['id']}",
                 'Published part identity, bytes or remote digest differs')


def make_parts(archive, directory):
    directory.mkdir()
    parts = []
    with archive.open('rb') as stream:
        while data := stream.read(PART_BYTES):
            index = len(parts)
            gate.require(index < 32, 'Authorized archive part bound exceeded')
            name = ASSET_NAME + f'.part-{index:03d}'
            path = directory / name
            path.write_bytes(data)
            parts.append({'index': index, 'asset_name': name, 'bytes': len(data),
                          'sha256': hashlib.sha256(data).hexdigest(), 'path': path})
    gate.require(bool(parts), 'The exact authorized archive is empty')
    return parts


def publish(client, request, archive, output):
    checked = gate.load_request(ROOT)
    gate.validate_authorization(gate.read_json(ROOT / gate.AUTH_PATH), checked)
    gate.require(request == checked, 'Publication request differs from the fully checked authorization')
    binding = request['binding']
    proof = verify_oci_archive(archive, binding, max_archive_bytes=gate.MAX_ARCHIVE_BYTES)
    parts = make_parts(archive, output / 'parts')
    # The authorized account created this draft and fixed its actual source tag.
    # GET-by-tag is a published-release lookup and must not be used to discover
    # or recreate this existing draft with the narrower Actions credential.
    release = client.call(f'/releases/{RELEASE_ID}')
    gate.require(release.get('tag_name') == RELEASE_TAG and release.get('id') == RELEASE_ID
                 and gate.REQUEST_SHA256 in release.get('body', '')
                 and binding['archive_sha256'] in release.get('body', '')
                 and release.get('prerelease') is False, 'Existing release is not this approved factual subject')
    release_id = release['id']
    # A draft may not expose its tag until publication. The create request fixes
    # target_commitish; the actual Git ref is checked before reporting success.
    assets = client.call(f'/releases/{release_id}/assets?per_page=100')
    names = [asset['name'] for asset in assets]
    expected_names = {part['asset_name'] for part in parts}
    gate.require(len(names) == len(set(names)) and set(names) <= expected_names,
                 'Release contains duplicate or unrelated assets; nothing was removed')
    uploaded = {asset['name']: asset for asset in assets}
    records = []
    for part in parts:
        asset = uploaded.get(part['asset_name'])
        if asset is None:
            gate.require(release['draft'] is True, 'Cannot append bytes to an already published exact release')
            asset = client.call('', file=(release_id, part['asset_name'], part['path']))
        verify_asset(asset, part)
        records.append({key: value for key, value in part.items() if key != 'path'} |
                       {'asset_id': asset['id'], 'api_url': asset['url'],
                        'remote_digest': asset['digest'], 'remote_state': asset['state']})
    current = client.call(f'/releases/{release_id}/assets?per_page=100')
    gate.require(len(current) == len(parts) and {asset['name'] for asset in current} == expected_names,
                 'Final release asset inventory differs')
    for asset in current:
        verify_asset(asset, next(part for part in parts if part['asset_name'] == asset['name']))
    try:
        existing_ref = client.call('/git/ref/tags/' + RELEASE_TAG)['object']
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        gate.require(release['draft'] is True and release.get('target_commitish') == ancestor.SOURCE,
                     'Draft release does not fix the real build source')
    else:
        if existing_ref['type'] == 'tag':
            existing_ref = client.call('/git/tags/' + existing_ref['sha'])['object']
        gate.require(existing_ref['type'] == 'commit' and existing_ref['sha'] == ancestor.SOURCE,
                     'Existing release tag is not the actual build source; nothing was published')
    if release['draft'] is True:
        release = client.call(f'/releases/{release_id}', method='PATCH', value={'draft': False, 'make_latest': 'false'})
    release = client.call(f'/releases/{release_id}')
    gate.require(release['draft'] is False and release['published_at'] and release['tag_name'] == RELEASE_TAG,
                 'Exact artifact is not a published release')
    reference = client.call('/git/ref/tags/' + RELEASE_TAG)['object']
    if reference['type'] == 'tag':
        reference = client.call('/git/tags/' + reference['sha'])['object']
    gate.require(reference['type'] == 'commit' and reference['sha'] == ancestor.SOURCE,
                 'Published release tag does not resolve to the actual build source commit')
    transport = {'schema': 'deepresearch-grafana-candidate-transport-v1', 'artifact': 'grafana',
                 'repository': REPOSITORY, 'request_sha256': gate.REQUEST_SHA256,
                 'transport_mode': 'github_asset_parts', 'release_id': release_id, 'release_tag': RELEASE_TAG,
                 'asset_name': ASSET_NAME, 'asset_sha256': binding['archive_sha256'],
                 'archive_bytes': binding['archive_bytes'], 'part_bytes': PART_BYTES, 'parts': records}
    (output / 'transport.json').write_text(json.dumps(transport, indent=2) + '\n', encoding='utf-8')
    (output / 'publication.json').write_text(json.dumps({'published': True, 'release_id': release_id,
        'release_tag': RELEASE_TAG, 'request_sha256': gate.REQUEST_SHA256, 'proof': proof,
        'actual_tag_commit': reference['sha'], 'published_at': release['published_at'],
        'source_identity': ancestor.source_identity()}, indent=2) + '\n', encoding='utf-8')
    return transport


def main():
    # Authorization and all reviewed input hashes are checked before HTTP/write.
    request = gate.load_request(ROOT)
    gate.validate_authorization(gate.read_json(ROOT / gate.AUTH_PATH), request)
    ancestor.source_identity()
    OUTPUT.mkdir(parents=True, exist_ok=False)
    material = OUTPUT / 'ancestor'
    material.mkdir()
    archive, _ = ancestor.download(material)
    transport = publish(GitHub(os.environ.get('GITHUB_TOKEN')), request, archive, OUTPUT)
    print(json.dumps({'published': True, 'release_id': transport['release_id'],
                      'archive_sha256': transport['asset_sha256'], 'parts': len(transport['parts'])}))


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read(4096)).get('message', 'Publication HTTP request failed')
        except (ValueError, AttributeError):
            message = 'Publication HTTP request failed'
        token = os.environ.get('GITHUB_TOKEN')
        if token:
            message = message.replace(token, '[redacted]')
        failure = {'published': False, 'http_status': error.code, 'message': message[:300],
                   'release_id': RELEASE_ID, 'request_sha256': gate.REQUEST_SHA256}
        if OUTPUT.is_dir():
            (OUTPUT / 'publication-error.json').write_text(json.dumps(failure, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(failure))
        raise SystemExit(1)
