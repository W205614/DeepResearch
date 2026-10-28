"""Fetch and load the newly authorized exact28 Grafana OCI, without rebuilding."""
from __future__ import annotations

import argparse
from copy import copy
from datetime import datetime
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_candidate_exact28 as exact  # noqa: E402
from scripts.fetch_approved_image import NoRedirect, open_asset_response  # noqa: E402
from scripts.oci_artifact import canonical_path, verify_oci_archive  # noqa: E402

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


class DockerLoadError(FetchError):
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


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


def load_diagnostic(result, representation):
    raw = getattr(result, 'stderr', b'') or b''
    if isinstance(raw, str):
        raw = raw.encode('utf-8', errors='replace')
    text = raw.decode('utf-8', errors='replace')
    for name in ('GITHUB_TOKEN', 'GH_TOKEN', 'DOCKER_AUTH_CONFIG'):
        if secret := os.environ.get(name):
            text = text.replace(secret, '[redacted]')
    text = re.sub(r'https?://\S+', '[redacted-url]', text)
    text = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [redacted]', text)
    text = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)', '[redacted]', text)
    return {'representation': representation, 'returncode': result.returncode,
            'stderr_bytes': len(raw), 'stderr_sha256': hashlib.sha256(raw).hexdigest(),
            'stderr_excerpt': text[:4096], 'stderr_excerpt_truncated': len(text) > 4096}


def classic_manifest_missing(result):
    """Only Moby's legacy-load lookup of the OCI blobs directory permits a retry."""
    raw = getattr(result, 'stderr', b'') or b''
    text = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
    return bool(result.returncode and re.search(
        r'open /[^\s:]*docker-import-[0-9]+/blobs/json: no such file or directory', text))


class HashingReader:
    def __init__(self, stream):
        self.stream, self.hashed, self.count = stream, hashlib.sha256(), 0

    def read(self, size=-1):
        block = self.stream.read(size)
        self.count += len(block)
        self.hashed.update(block)
        return block


def verify_docker_load_archive(path, inventory, manifest_bytes):
    """Integrity only: original member payloads plus exactly one Docker manifest."""
    require(not path.is_symlink() and path.is_file(), 'Derived Docker transport is absent or linked')
    with tarfile.open(path, 'r:') as archive:
        members = archive.getmembers()
        require(len(members) == len(inventory) + 1
                and {member.name for member in members} == {*inventory, 'manifest.json'},
                'Derived Docker transport member inventory changed')
        for member in members:
            require(member.name == canonical_path(member.name) and (member.isfile() or member.isdir()),
                    'Derived Docker transport contains an unsafe member')
            if member.name == 'manifest.json':
                require(member.isfile() and member.size == len(manifest_bytes)
                        and archive.extractfile(member).read() == manifest_bytes,
                        'Derived Docker manifest differs from the exact config/layer mapping')
                continue
            expected = inventory[member.name]
            require(member.size == expected['bytes']
                    and ('file' if member.isfile() else 'directory') == expected['type'],
                    'Derived original member type or length changed')
            if member.isfile():
                with archive.extractfile(member) as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                require(digest == expected['sha256'], 'Derived original member payload changed')
    return {'bytes': path.stat().st_size, 'sha256': file_hash(path)}


def prepare_docker_load_archive(source, binding, destination):
    """Add manifest.json to a separate tar; the approved OCI remains untouched.

    Moby v28 image/tarexport/load.go reads Config/Layers at their listed paths,
    decompresses each layer with archive.DecompressStream, and checks each DiffID.
    No image config, compressed layer, index, or manifest blob is rewritten.
    """
    checked_cache(source, binding['archive_bytes'], binding['archive_sha256'])
    inventory = {}
    with tarfile.open(source, 'r:') as original:
        members = original.getmembers()
        require(0 < len(members) <= 4096 and len({member.name for member in members}) == len(members)
                and all(member.name == canonical_path(member.name)
                        and (member.isfile() or member.isdir()) for member in members)
                and 'manifest.json' not in {member.name for member in members},
                'Compatibility bridge requires the exact pure OCI without a Docker manifest')
        by_name = {member.name: member for member in members}

        def read_json_member(name, expected_digest):
            member = by_name.get(name)
            require(member is not None and member.isfile() and 0 < member.size <= 4 * 1024**2,
                    'Compatibility bridge metadata is absent or oversized')
            raw = original.extractfile(member).read()
            require('sha256:' + hashlib.sha256(raw).hexdigest() == expected_digest,
                    'Compatibility bridge metadata digest differs')
            return exact.strict_json(raw)

        index = read_json_member('index.json', binding['index_digest'])
        require(binding.get('root_layout') == 'direct_manifest' and len(index.get('manifests', [])) == 1
                and index['manifests'][0].get('digest') == binding['platform_manifest_digest'],
                'Compatibility bridge requires the exact direct OCI manifest')
        manifest = read_json_member('blobs/sha256/' + binding['platform_manifest_digest'][7:],
                                    binding['platform_manifest_digest'])
        require(manifest['config']['digest'] == binding['config_digest'],
                'Compatibility bridge config differs')
        config_path = 'blobs/sha256/' + binding['config_digest'][7:]
        config = read_json_member(config_path, binding['config_digest'])
        layers = manifest['layers']
        require(config.get('rootfs', {}).get('diff_ids') == binding['diff_ids']
                and len(layers) == len(binding['diff_ids']) and config.get('os') == 'linux'
                and config.get('architecture') == 'amd64', 'Compatibility bridge runtime identity differs')
        layer_paths = []
        for layer in layers:
            require(type(layer.get('digest')) is str and exact.DIGEST.fullmatch(layer['digest'])
                    and layer.get('mediaType') in {'application/vnd.oci.image.layer.v1.tar+gzip',
                                                  'application/vnd.oci.image.layer.v1.tar'},
                    'Compatibility bridge layer identity or encoding differs')
            path = 'blobs/sha256/' + layer['digest'][7:]
            require(path in by_name and by_name[path].isfile() and by_name[path].size == layer['size'],
                    'Compatibility bridge layer blob is absent or changed')
            layer_paths.append(path)
        manifest_bytes = (json.dumps([{'Config': config_path, 'Layers': layer_paths,
                                      'RepoTags': [binding['source_tag']]}], separators=(',', ':')) + '\n').encode()
        require(destination.resolve() != source.resolve() and not destination.is_symlink(),
                'Derived Docker transport must be distinct from the approved OCI')
        with destination.open('xb') as output:
            with tarfile.open(fileobj=output, mode='w', format=tarfile.PAX_FORMAT) as derived:
                for member in members:
                    expected = {'type': 'file' if member.isfile() else 'directory', 'bytes': member.size}
                    if member.isfile():
                        with original.extractfile(member) as stream:
                            reader = HashingReader(stream)
                            derived.addfile(copy(member), reader)
                        require(reader.count == member.size, 'Source member length changed during bridging')
                        expected['sha256'] = reader.hashed.hexdigest()
                        if member.name.startswith('blobs/sha256/'):
                            require(member.name == 'blobs/sha256/' + expected['sha256'],
                                    'Source blob payload changed during bridging')
                    else:
                        derived.addfile(copy(member))
                    inventory[member.name] = expected
                info = tarfile.TarInfo('manifest.json')
                info.size, info.mode, info.mtime = len(manifest_bytes), 0o644, 0
                derived.addfile(info, BytesIO(manifest_bytes))
    checked_cache(source, binding['archive_bytes'], binding['archive_sha256'])
    require(destination.stat().st_size <= binding['archive_bytes'] + 1024**2,
            'Derived Docker transport exceeds its independent size bound')
    integrity = verify_docker_load_archive(destination, inventory, manifest_bytes)
    receipt = {'mode': 'docker_manifest_transport_integrity_only', 'authorization_evaluated': False,
               'original_approved_archive_sha256': binding['archive_sha256'],
               'original_approved_archive_bytes': binding['archive_bytes'],
               'derived_archive': str(destination), 'derived_archive_sha256': integrity['sha256'],
               'derived_archive_bytes': integrity['bytes'], 'original_members': inventory,
               'original_member_payloads_unchanged': True,
               'added_member': {'path': 'manifest.json', 'bytes': len(manifest_bytes),
                                'sha256': hashlib.sha256(manifest_bytes).hexdigest()},
               'config_digest': binding['config_digest'], 'diff_ids': binding['diff_ids'],
               'original_binary_sha256': binding['binary_sha256']}
    return destination, receipt


def load_image(archive, binding, tag, *, load_receipt=None):
    require(tag in {APPROVED_TAG, SCAN_TAG}, 'Candidate output tag belongs to another namespace')
    diagnostics = {'attempts': [], 'compatibility_fallback_used': False}
    references = [binding['config_digest'], binding['platform_manifest_digest'],
                  binding['index_digest'], binding['source_tag']]
    selected = next((ref for ref in references if exact_image(inspect(ref), binding)), None)
    if selected is None:
        loaded = subprocess.run(['docker', 'load', '--input', str(archive)], capture_output=True, check=False)
        diagnostics['attempts'].append(load_diagnostic(loaded, 'original_approved_oci'))
        if loaded.returncode:
            if not classic_manifest_missing(loaded):
                raise DockerLoadError('Docker could not load the verified exact28 OCI', diagnostics)
            receipt = None
            try:
                with tempfile.TemporaryDirectory(prefix='grafana-docker-load-', dir=archive.parent) as temporary:
                    derived, receipt = prepare_docker_load_archive(
                        archive, binding, Path(temporary) / 'compatible.docker.tar')
                    checked_cache(derived, receipt['derived_archive_bytes'], receipt['derived_archive_sha256'])
                    diagnostics.update(compatibility_fallback_used=True, derived_transport_integrity=receipt)
                    retry = subprocess.run(['docker', 'load', '--input', str(derived)],
                                           capture_output=True, check=False)
                    diagnostics['attempts'].append(load_diagnostic(retry, 'derived_docker_manifest_transport'))
            except Exception as error:
                diagnostics['compatibility_error_type'] = type(error).__name__
                raise DockerLoadError('Docker compatibility transport preparation or loading failed', diagnostics) from None
            finally:
                if receipt is not None:
                    receipt['private_temporary_directory_removed'] = not Path(temporary).exists()
                    proof = archive.parent / ('grafana-docker-load-' + binding['archive_sha256'][:16] + '-integrity.json')
                    receipt['integrity_receipt'] = str(proof)
                    proof.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
            if retry.returncode:
                raise DockerLoadError('Docker could not load the compatible exact28 transport', diagnostics)
        selected = next((ref for ref in references if exact_image(inspect(ref), binding)), None)
    if selected is None:
        raise DockerLoadError('Loaded candidate config, filesystem or platform differs', diagnostics)
    tagged = subprocess.run(['docker', 'tag', selected, tag], capture_output=True, check=False)
    if tagged.returncode:
        raise DockerLoadError('Docker could not label the verified candidate', diagnostics)
    actual = inspect(tag)
    if not exact_image(actual, binding):
        raise DockerLoadError('Resulting tag is not the authorized exact28 candidate', diagnostics)
    if load_receipt is not None:
        load_receipt.update(diagnostics)
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
        load_receipt = {}
        inspection = load_image(archive, binding, args.tag, load_receipt=load_receipt)
        inspection_path.parent.mkdir(parents=True, exist_ok=True)
        inspection_path.write_text(json.dumps(inspection, indent=2) + '\n', encoding='utf-8')
        verified.pop('authorization_evaluated', None)
        result = {**verified, 'passed': True, 'mode': 'new_exact28_authorized_load_only_scan_gate_still_required',
                  'artifact': 'grafana', 'archive': str(archive), 'tag': args.tag,
                  'request_sha256': exact.REQUEST_SHA256, 'transport_sha256': TRANSPORT_SHA256,
                  'policy_authorization_verified': True, 'old_authorization_inherited': False,
                  'archive_source': source, 'publication': publication, 'inspection': str(inspection_path),
                  'docker_load': load_receipt}
    except Exception as error:
        # Never echo credentials, signed URLs, HTTP bodies or Docker output.
        result = {'passed': False, 'mode': 'failed_closed', 'error_type': type(error).__name__}
        if isinstance(error, FetchError):
            result['error'] = str(error)
        if isinstance(error, DockerLoadError):
            result['docker_load'] = error.diagnostics
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
