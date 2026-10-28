"""Fetch and load the newly authorized exact28 Grafana OCI, without rebuilding."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.request import Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_candidate_exact28 as exact  # noqa: E402
from scripts.fetch_approved_image import NoRedirect, open_asset_response  # noqa: E402
from scripts.oci_artifact import verify_oci_archive  # noqa: E402

TRANSPORT_PATH = Path('deployment/security/grafana-candidate-transport.json')
# Filled with the real, published release inventory hash after upload verification.
# An empty pin fails before HTTP or Docker, including explicit local restoration.
TRANSPORT_SHA256 = 'e4376eb6e5e44059f0b029b1d8e2872a6b3dd6acdda405a012e16ab3017383c8'
REPOSITORY = 'W205614/DeepResearch'
RELEASE_TAG = 'artifact-grafana-exact28-20261028'
APPROVED_TAG = 'deepresearch-approved-grafana:20261028'
SCAN_TAG = 'deepresearch-scan-grafana:ci'
PART_BYTES = 64 * 1024**2
MAX_PARTS = 32
STORAGE_HOSTS = frozenset({'release-assets.githubusercontent.com', 'objects.githubusercontent.com'})


class FetchError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise FetchError(message)


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def authorized_request(root):
    # This checks all pinned compilation inputs and evidence, rather than requiring
    # unrelated Compose/workflow changes to leave the entire Git checkout clean.
    request = exact.load_request(root)
    auth = exact.read_json(exact.repository_file(root, exact.AUTH_PATH.as_posix()))
    exact.validate_authorization(auth, request)
    return request


def validate_transport(transport, binding):
    require(type(transport) is dict, 'Missing candidate transport object')
    total = binding['archive_bytes']
    name = 'grafana-' + binding['archive_sha256'][:8] + '.oci.tar'
    require(transport.get('schema') == 'deepresearch-grafana-candidate-transport-v1'
            and transport.get('artifact') == 'grafana'
            and transport.get('repository') == REPOSITORY
            and transport.get('request_sha256') == exact.REQUEST_SHA256
            and transport.get('transport_mode') == 'github_asset_parts'
            and type(transport.get('release_id')) is int and transport['release_id'] > 0
            and transport.get('release_tag') == RELEASE_TAG
            and transport.get('asset_name') == name
            and transport.get('asset_sha256') == binding['archive_sha256']
            and type(transport.get('archive_bytes')) is int and transport['archive_bytes'] == total
            and type(transport.get('part_bytes')) is int and transport['part_bytes'] == PART_BYTES
            and type(total) is int and 0 < total <= exact.MAX_ARCHIVE_BYTES,
            'Candidate transport subject, request, repository or release differs')
    parts = transport.get('parts')
    require(type(parts) is list and 0 < len(parts) <= MAX_PARTS
            and len(parts) == (total + PART_BYTES - 1) // PART_BYTES,
            'Candidate parts inventory is incomplete')
    seen = set()
    for index, part in enumerate(parts):
        require(type(part) is dict, 'Malformed candidate part')
        asset_id, digest = part.get('asset_id'), part.get('sha256')
        require(type(part.get('index')) is int and part['index'] == index
                and type(asset_id) is int and asset_id > 0 and asset_id not in seen
                and part.get('asset_name') == name + f'.part-{index:03d}'
                and part.get('api_url') == f'https://api.github.com/repos/{REPOSITORY}/releases/assets/{asset_id}'
                and type(part.get('bytes')) is int
                and part['bytes'] == min(PART_BYTES, total - index * PART_BYTES)
                and type(digest) is str and exact.HEX.fullmatch(digest)
                and part.get('remote_digest') == 'sha256:' + digest
                and part.get('remote_state') == 'uploaded',
                'Candidate part ID, order, name, size, digest or uploaded state differs')
        seen.add(asset_id)
    return parts


def load_transport(root, binding):
    require(type(TRANSPORT_SHA256) is str and exact.HEX.fullmatch(TRANSPORT_SHA256),
            'Final published candidate transport SHA256 pin is not set')
    path = exact.repository_file(root, TRANSPORT_PATH.as_posix())
    require(file_hash(path) == TRANSPORT_SHA256, 'Published candidate transport bytes changed')
    transport = exact.read_json(path)
    validate_transport(transport, binding)
    return transport


def verify_publication(transport, binding):
    parts = validate_transport(transport, binding)
    url = f"https://api.github.com/repos/{REPOSITORY}/releases/{transport['release_id']}"
    headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
               'User-Agent': 'DeepResearch-exact28-artifact-loader'}
    if token := os.environ.get('GITHUB_TOKEN', ''):
        headers['Authorization'] = 'Bearer ' + token
    with build_opener(NoRedirect).open(Request(url, headers=headers), timeout=60) as response:
        require(response.getcode() == 200, 'Release metadata response is not successful')
        raw = response.read(4 * 1024**2 + 1)
    require(len(raw) <= 4 * 1024**2, 'Release metadata exceeds its bound')
    release = exact.strict_json(raw)
    require(type(release) is dict and type(release.get('id')) is int
            and release['id'] == transport['release_id'] and release.get('tag_name') == RELEASE_TAG
            and release.get('draft') is False and type(release.get('published_at')) is str,
            'Candidate release is unpublished or its live identity changed')
    try:
        timestamp = datetime.fromisoformat(release['published_at'].replace('Z', '+00:00'))
        require(timestamp.utcoffset() is not None, 'Release publication timestamp lacks a timezone')
    except ValueError:
        raise FetchError('Release publication timestamp is invalid') from None
    assets = release.get('assets')
    require(type(assets) is list and all(type(asset) is dict for asset in assets),
            'Live candidate release assets are missing')
    for part in parts:
        matches = [asset for asset in assets if asset.get('id') == part['asset_id']]
        require(len(matches) == 1, 'Candidate asset is absent or duplicated in the fixed release')
        asset = matches[0]
        require(type(asset.get('id')) is int and asset.get('name') == part['asset_name']
                and type(asset.get('size')) is int and asset['size'] == part['bytes']
                and asset.get('digest') == part['remote_digest'] and asset.get('state') == 'uploaded'
                and asset.get('url') == part['api_url'],
                'Live candidate asset metadata differs from the published inventory')
    return {'release_id': release['id'], 'release_tag': release['tag_name'],
            'published_at': release['published_at'], 'published_verified_live': True,
            'verified_asset_count': len(parts)}


def checked_cache(path, size, digest):
    require(not path.is_symlink() and path.is_file() and path.stat().st_size == size
            and file_hash(path) == digest, 'Cached candidate bytes differ; preserve them for investigation')


def download_parts(destination, binding, transport):
    parts = validate_transport(transport, binding)
    # Publication and live per-asset identities are checked even when every byte is cached.
    publication = verify_publication(transport, binding)
    require(not destination.is_symlink(), 'Candidate destination is a symbolic link')
    if destination.exists():
        checked_cache(destination, binding['archive_bytes'], binding['archive_sha256'])
        return destination, publication
    destination.parent.mkdir(parents=True, exist_ok=True)
    cache = destination.parent / ('grafana-exact28-parts-' + binding['archive_sha256'][:16])
    require(not cache.is_symlink(), 'Candidate parts cache is a symbolic link')
    cache.mkdir(exist_ok=True)
    verified = []
    for part in parts:
        target = cache / (f"{part['index']:03d}-" + part['sha256'] + '.part')
        if target.exists() or target.is_symlink():
            checked_cache(target, part['bytes'], part['sha256'])
        else:
            with open_asset_response(part['asset_id'], STORAGE_HOSTS) as response:
                require(response.getcode() == 200, 'Candidate asset download is not successful')
                declared = response.headers.get('Content-Length')
                require(declared is None or declared == str(part['bytes']),
                        'Candidate part declared size differs')
                with tempfile.NamedTemporaryFile(prefix='grafana-exact28-', suffix='.partial',
                                                 dir=cache, delete=False) as output:
                    temporary, count, hashed = Path(output.name), 0, hashlib.sha256()
                    while block := response.read(1024**2):
                        count += len(block)
                        require(count <= part['bytes'], 'Candidate part exceeds its bound')
                        hashed.update(block)
                        output.write(block)
            require(count == part['bytes'] and hashed.hexdigest() == part['sha256'],
                    'Candidate part is incomplete or changed; partial bytes retained')
            temporary.rename(target)
        verified.append(target)
    with tempfile.NamedTemporaryFile(prefix='grafana-exact28-assembled-', suffix='.partial',
                                     dir=destination.parent, delete=False) as output:
        assembled, count, hashed = Path(output.name), 0, hashlib.sha256()
        for path in verified:
            with path.open('rb') as stream:
                while block := stream.read(1024**2):
                    count += len(block)
                    require(count <= binding['archive_bytes'], 'Candidate assembled archive exceeds its bound')
                    hashed.update(block)
                    output.write(block)
    require(count == binding['archive_bytes'] and hashed.hexdigest() == binding['archive_sha256'],
            'Candidate assembled archive whole SHA/size differs; partial bytes retained')
    assembled.rename(destination)
    return destination, publication


def inspect(reference):
    result = subprocess.run(['docker', 'image', 'inspect', reference], capture_output=True, check=False)
    if result.returncode:
        return None
    records = exact.strict_json(result.stdout)
    require(type(records) is list and len(records) == 1 and type(records[0]) is dict,
            'Docker inspection is malformed')
    return records[0]


def exact_image(inspection, binding):
    return bool(inspection and inspection.get('Id') in {
        binding['index_digest'], binding['platform_manifest_digest'], binding['config_digest']}
        and inspection.get('RootFS', {}).get('Layers') == binding['diff_ids']
        and inspection.get('Os') == 'linux' and inspection.get('Architecture') == 'amd64')


def load_image(archive, binding, tag):
    require(tag in {APPROVED_TAG, SCAN_TAG}, 'Candidate output tag belongs to another namespace')
    references = [binding['config_digest'], binding['platform_manifest_digest'],
                  binding['index_digest'], binding['source_tag']]
    selected = next((ref for ref in references if exact_image(inspect(ref), binding)), None)
    if selected is None:
        loaded = subprocess.run(['docker', 'load', '--input', str(archive)], capture_output=True, check=False)
        require(loaded.returncode == 0, 'Docker could not load the verified exact28 OCI')
        selected = next((ref for ref in references if exact_image(inspect(ref), binding)), None)
    require(selected is not None, 'Loaded candidate config, filesystem or platform differs')
    tagged = subprocess.run(['docker', 'tag', selected, tag], capture_output=True, check=False)
    require(tagged.returncode == 0, 'Docker could not label the verified candidate')
    actual = inspect(tag)
    require(exact_image(actual, binding), 'Resulting tag is not the authorized exact28 candidate')
    return actual


def output_paths(root, output, archive):
    cache = (root / '.cache/eval').resolve()
    inspection = output.with_name(output.stem + '-inspect.json')
    for path in (output, inspection):
        require(cache.is_relative_to(root.resolve()) and path.resolve().is_relative_to(cache)
                and path.suffix == '.json' and not path.is_symlink()
                and (archive is None or path.resolve() != archive.resolve()),
                'Result paths must be distinct JSON files below workspace .cache/eval')
    return inspection


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Explicit local recovery OCI; never a default CI cache bypass')
    parser.add_argument('--tag', default=APPROVED_TAG, choices=[APPROVED_TAG, SCAN_TAG])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    output = args.output or ROOT / '.cache/eval/approved-grafana-load.json'
    try:
        inspection_path = output_paths(ROOT, output, args.archive)
        request = authorized_request(ROOT)
        binding = request['binding']
        transport = load_transport(ROOT, binding)
        if args.archive is None:
            archive, publication = download_parts(ROOT / '.cache/eval/approved-grafana.oci.tar', binding, transport)
            source = 'fixed_published_candidate_release_parts'
        else:
            archive = args.archive
            publication = {'mode': 'explicit_local_archive_no_remote_publication_claim'}
            source = 'explicit_local_recovery'
        require(not archive.is_symlink() and archive.is_file(), 'Candidate archive is absent or linked')
        verified = verify_oci_archive(archive, binding, max_archive_bytes=exact.MAX_ARCHIVE_BYTES)
        # Recheck inputs/auth/transport after downloads and full OCI verification, before Docker.
        require(authorized_request(ROOT) == request and load_transport(ROOT, binding) == transport,
                'Candidate authority changed during verification')
        if args.archive is None:
            publication = verify_publication(transport, binding)
        inspection = load_image(archive, binding, args.tag)
        inspection_path.parent.mkdir(parents=True, exist_ok=True)
        inspection_path.write_text(json.dumps(inspection, indent=2) + '\n', encoding='utf-8')
        verified.pop('authorization_evaluated', None)
        result = {**verified, 'passed': True, 'mode': 'new_exact28_authorized_load_only_scan_gate_still_required',
                  'artifact': 'grafana', 'archive': str(archive), 'tag': args.tag,
                  'request_sha256': exact.REQUEST_SHA256, 'transport_sha256': TRANSPORT_SHA256,
                  'policy_authorization_verified': True, 'old_authorization_inherited': False,
                  'archive_source': source, 'publication': publication, 'inspection': str(inspection_path)}
    except Exception as error:
        # Never echo credentials, signed URLs, HTTP bodies or Docker output.
        result = {'passed': False, 'mode': 'failed_closed', 'error_type': type(error).__name__}
        if isinstance(error, FetchError):
            result['error'] = str(error)
    try:
        output_paths(ROOT, output, args.archive)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    except (OSError, ValueError):
        print(json.dumps({'passed': False, 'mode': 'failed_closed', 'error': 'Unsafe result path'}))
        return 1
    print(json.dumps({key: result[key] for key in ('passed', 'mode', 'error_type', 'error') if key in result}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
