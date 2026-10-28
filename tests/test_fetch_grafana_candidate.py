"""Exact28 loader boundaries using tiny bytes, mocked HTTPS and Docker only."""
from copy import deepcopy
from io import BytesIO
import gzip
import hashlib
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from scripts import fetch_grafana_candidate as fetch


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Response(BytesIO):
    def __init__(self, raw, declared=None):
        super().__init__(raw)
        self.headers = {} if declared is None else {'Content-Length': str(declared)}

    def getcode(self):
        return 200


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, 'ROOT', tmp_path)
    monkeypatch.setattr(fetch, 'PART_BYTES', 8)
    monkeypatch.setattr(fetch.exact, 'REQUEST_SHA256', 'e' * 64)
    data = b'bound-OCI-fixture'
    binding = {'artifact': 'grafana', 'archive_bytes': len(data), 'archive_sha256': sha(data),
               'index_digest': 'sha256:' + '1' * 64, 'platform_manifest_digest': 'sha256:' + '2' * 64,
               'config_digest': 'sha256:' + '3' * 64, 'diff_ids': ['sha256:' + '4' * 64],
               'binary_sha256': {'usr/share/grafana/bin/grafana': '5' * 64},
               'source_tag': 'deepresearch-grafana-candidate:ci', 'expires': '2026-11-25T23:59:59+08:00',
               'allowed_findings': [{'id': f'CVE-FIXTURE-{i}', 'target': 'usr/share/grafana/bin/grafana',
                                     'package': 'fixture', 'version': 'v1', 'severity': 'HIGH',
                                     'disposition': 'NOT_AFFECTED'} for i in range(28)]}
    request = {'binding': binding}
    auth = {'accepted': True, 'request_sha256': fetch.exact.REQUEST_SHA256,
            'subject': fetch.exact.subject_for(binding),
            'exact_target_advisory_rows': deepcopy(binding['allowed_findings']),
            'policy': {**dict.fromkeys(fetch.exact.POLICY_FLAGS, True), 'expires': binding['expires']}}
    auth_path = tmp_path / fetch.exact.AUTH_PATH
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(json.dumps(auth), encoding='utf-8')
    monkeypatch.setattr(fetch.exact, 'load_request', lambda root: deepcopy(request))
    base = 'grafana-' + sha(data)[:8] + '.oci.tar'
    parts, payloads = [], {}
    for index, offset in enumerate(range(0, len(data), fetch.PART_BYTES)):
        part = data[offset:offset + fetch.PART_BYTES]
        asset_id = 700 + index
        payloads[asset_id] = part
        parts.append({'index': index, 'asset_id': asset_id, 'asset_name': base + f'.part-{index:03d}',
                      'api_url': f'https://api.github.com/repos/{fetch.REPOSITORY}/releases/assets/{asset_id}',
                      'bytes': len(part), 'sha256': sha(part), 'remote_digest': 'sha256:' + sha(part),
                      'remote_state': 'uploaded'})
    transport = {'schema': 'deepresearch-grafana-candidate-transport-v1', 'artifact': 'grafana',
                 'repository': fetch.REPOSITORY, 'request_sha256': fetch.exact.REQUEST_SHA256,
                 'transport_mode': 'github_asset_parts', 'release_id': 456,
                 'release_tag': fetch.RELEASE_TAG, 'asset_name': base, 'asset_sha256': sha(data),
                 'archive_bytes': len(data), 'part_bytes': fetch.PART_BYTES, 'parts': parts}
    transport_path = tmp_path / fetch.TRANSPORT_PATH
    transport_path.write_text(json.dumps(transport), encoding='utf-8')
    monkeypatch.setattr(fetch, 'TRANSPORT_SHA256', sha(transport_path.read_bytes()))
    release = {'id': 456, 'tag_name': fetch.RELEASE_TAG, 'draft': False,
               'published_at': '2026-10-28T08:00:00Z',
               'assets': [{'id': p['asset_id'], 'name': p['asset_name'], 'size': p['bytes'],
                           'digest': p['remote_digest'], 'state': 'uploaded', 'url': p['api_url']}
                          for p in parts]}
    metadata_calls, download_calls = [], []

    class Opener:
        def open(self, request, timeout):
            metadata_calls.append(request)
            return Response(json.dumps(release).encode())

    def asset_response(asset_id, hosts):
        assert hosts == fetch.STORAGE_HOSTS
        download_calls.append(asset_id)
        return Response(payloads[asset_id], len(payloads[asset_id]))

    monkeypatch.setattr(fetch, 'build_opener', lambda *_: Opener())
    monkeypatch.setattr(fetch, 'open_asset_response', asset_response)
    monkeypatch.setattr(fetch.subprocess, 'run', lambda *a, **k: pytest.fail('unexpected Docker call'))
    return SimpleNamespace(root=tmp_path, data=data, binding=binding, request=request, auth=auth,
                           auth_path=auth_path, transport=transport, transport_path=transport_path,
                           release=release, metadata_calls=metadata_calls, download_calls=download_calls,
                           payloads=payloads, destination=tmp_path / '.cache/eval/approved-grafana.oci.tar')


@pytest.mark.parametrize('change', ['input', 'pending', 'subject', 'scope', 'policy'])
def test_authority_failure_precedes_all_transport_network_and_docker(fixture, monkeypatch, change):
    f = fixture
    if change == 'input':
        def changed(_root):
            raise ValueError('Pinned compilation input changed')
        monkeypatch.setattr(fetch.exact, 'load_request', changed)
    elif change == 'pending':
        f.auth['accepted'] = False
    elif change == 'subject':
        f.auth['subject']['config_digest'] = 'sha256:' + 'a' * 64
    elif change == 'scope':
        f.auth['exact_target_advisory_rows'].pop()
    else:
        f.auth['policy']['keep_all_raw'] = False
    f.auth_path.write_text(json.dumps(f.auth))
    monkeypatch.setattr(fetch, 'load_transport', lambda *a: pytest.fail('transport before authorization'))
    assert fetch.main([]) == 1
    assert not f.metadata_calls and not f.download_calls


@pytest.mark.parametrize('change', ['unbound', 'changed'])
def test_transport_pin_failure_cannot_use_existing_cache(fixture, monkeypatch, change):
    f = fixture
    f.destination.parent.mkdir(parents=True)
    f.destination.write_bytes(f.data)
    if change == 'unbound':
        monkeypatch.setattr(fetch, 'TRANSPORT_SHA256', '')
    else:
        f.transport_path.write_bytes(f.transport_path.read_bytes() + b' ')
    assert fetch.main([]) == 1
    assert not f.metadata_calls and not f.download_calls
    assert f.destination.read_bytes() == f.data


@pytest.mark.parametrize('change', ['draft', 'unpublished', 'release-id', 'tag', 'asset-id', 'asset-name',
                                   'asset-size', 'asset-digest', 'asset-state', 'asset-url', 'duplicate'])
def test_live_publication_and_every_asset_identity_required_even_for_complete_cache(fixture, change):
    f = fixture
    f.destination.parent.mkdir(parents=True)
    f.destination.write_bytes(f.data)
    if change == 'draft':
        f.release['draft'] = True
    elif change == 'unpublished':
        f.release['published_at'] = None
    elif change == 'release-id':
        f.release['id'] += 1
    elif change == 'tag':
        f.release['tag_name'] = 'old-exact18-release'
    elif change == 'duplicate':
        f.release['assets'].append(deepcopy(f.release['assets'][0]))
    else:
        field = {'asset-id': 'id', 'asset-name': 'name', 'asset-size': 'size', 'asset-digest': 'digest',
                 'asset-state': 'state', 'asset-url': 'url'}[change]
        f.release['assets'][0][field] = 'changed'
    with pytest.raises(fetch.FetchError):
        fetch.download_parts(f.destination, f.binding, f.transport)
    assert len(f.metadata_calls) == 1 and not f.download_calls
    assert f.destination.read_bytes() == f.data


@pytest.mark.parametrize('change', ['order', 'duplicate-id', 'name', 'size', 'digest', 'repository', 'request'])
def test_malformed_parts_reject_before_http(fixture, change):
    f = fixture
    t = deepcopy(f.transport)
    if change == 'order':
        t['parts'].reverse()
    elif change == 'duplicate-id':
        t['parts'][1]['asset_id'] = t['parts'][0]['asset_id']
    elif change == 'name':
        t['parts'][0]['asset_name'] = 'old-approved18.part-000'
    elif change == 'size':
        t['parts'][0]['bytes'] -= 1
    elif change == 'digest':
        t['parts'][0]['remote_digest'] = 'sha256:' + '0' * 64
    else:
        t[change if change == 'repository' else 'request_sha256'] = 'changed'
    with pytest.raises(fetch.FetchError):
        fetch.download_parts(f.destination, f.binding, t)
    assert not f.metadata_calls and not f.download_calls


def test_all_parts_and_whole_hash_required_with_live_publication_on_each_cache_reuse(fixture, monkeypatch):
    f = fixture
    monkeypatch.setenv('GITHUB_TOKEN', 'synthetic-token')
    path, publication = fetch.download_parts(f.destination, f.binding, f.transport)
    assert path.read_bytes() == f.data and publication['verified_asset_count'] == len(f.transport['parts'])
    assert f.download_calls == [p['asset_id'] for p in f.transport['parts']]
    assert f.metadata_calls[0].full_url == f'https://api.github.com/repos/{fetch.REPOSITORY}/releases/456'
    assert f.metadata_calls[0].get_header('Authorization') == 'Bearer synthetic-token'
    f.download_calls.clear()
    fetch.download_parts(f.destination, f.binding, f.transport)
    assert len(f.metadata_calls) == 2 and not f.download_calls


@pytest.mark.parametrize('change', ['short', 'long', 'hash', 'declared'])
def test_bad_part_is_retained_without_promoting_an_archive(fixture, monkeypatch, change):
    f = fixture
    expected = f.payloads[700]
    content = {'short': expected[:-1], 'long': expected + b'x', 'hash': b'x' * len(expected),
               'declared': expected}[change]
    monkeypatch.setattr(fetch, 'open_asset_response', lambda *_: Response(
        content, len(expected) + 1 if change == 'declared' else None))
    with pytest.raises(fetch.FetchError):
        fetch.download_parts(f.destination, f.binding, f.transport)
    assert not f.destination.exists()
    if change != 'declared':
        assert list(f.destination.parent.rglob('*.partial'))


def test_changed_whole_digest_does_not_accept_valid_individual_parts(fixture):
    f = fixture
    binding, transport = deepcopy(f.binding), deepcopy(f.transport)
    binding['archive_sha256'] = transport['asset_sha256'] = '0' * 64
    transport['asset_name'] = 'grafana-00000000.oci.tar'
    for part, asset in zip(transport['parts'], f.release['assets'], strict=True):
        part['asset_name'] = asset['name'] = transport['asset_name'] + f".part-{part['index']:03d}"
    with pytest.raises(fetch.FetchError, match='whole SHA'):
        fetch.download_parts(f.destination, binding, transport)
    assert not f.destination.exists()


def test_explicit_local_archive_still_requires_authority_transport_and_full_oci(fixture, monkeypatch):
    f = fixture
    archive = f.root / 'recovery.oci.tar'
    archive.write_bytes(f.data)
    events = []

    def verify(path, binding, *, max_archive_bytes):
        assert path == archive and path.read_bytes() == f.data and binding == f.binding
        assert max_archive_bytes == 2 * 1024**3
        events.append('oci')
        return {'authorization_evaluated': False, 'whole_archive_sha256': sha(f.data)}

    actual = {'Id': f.binding['config_digest']}
    monkeypatch.setattr(fetch, 'verify_oci_archive', verify)
    monkeypatch.setattr(fetch, 'load_image', lambda *a, **k: events.append('load') or actual)
    assert fetch.main(['--archive', str(archive)]) == 0
    result = json.loads((f.root / '.cache/eval/approved-grafana-load.json').read_text())
    inspection = json.loads(Path(result['inspection']).read_text())
    assert result['passed'] and result['archive_source'] == 'explicit_local_recovery'
    assert result['publication'] == {'mode': 'explicit_local_archive_no_remote_publication_claim'}
    assert result['policy_authorization_verified'] and not result['old_authorization_inherited']
    assert 'authorization_evaluated' not in result and inspection == actual
    assert events == ['oci', 'load'] and not f.metadata_calls and not f.download_calls


@pytest.mark.parametrize('change', ['archive', 'source-after-download', 'authorization-after-download'])
def test_verification_or_authority_change_blocks_docker_after_bytes_arrive(fixture, monkeypatch, change):
    f = fixture

    def verify(*args, **kwargs):
        if change == 'archive':
            raise ValueError('OCI chain differs')
        if change == 'source-after-download':
            monkeypatch.setattr(fetch.exact, 'load_request', lambda *_: (_ for _ in ()).throw(
                ValueError('Pinned source changed')))
        else:
            f.auth['accepted'] = False
            f.auth_path.write_text(json.dumps(f.auth))
        return {}

    monkeypatch.setattr(fetch, 'verify_oci_archive', verify)
    monkeypatch.setattr(fetch, 'load_image', lambda *a, **k: pytest.fail('must not reach Docker'))
    assert fetch.main([]) == 1
    assert f.destination.read_bytes() == f.data


def test_default_main_rechecks_live_release_after_full_oci_before_docker(fixture, monkeypatch):
    f = fixture
    events = []

    def verify(*a, **k):
        events.append('oci')
        f.release['draft'] = True
        return {}

    monkeypatch.setattr(fetch, 'verify_oci_archive', verify)
    monkeypatch.setattr(fetch, 'load_image', lambda *a, **k: pytest.fail('release became unpublished'))
    assert fetch.main([]) == 1
    assert events == ['oci'] and len(f.metadata_calls) == 2


def test_docker_only_loads_exact_config_layers_and_platform_before_tagging(fixture, monkeypatch):
    f = fixture
    actual = {'Id': f.binding['config_digest'], 'RootFS': {'Layers': f.binding['diff_ids']},
              'Os': 'linux', 'Architecture': 'amd64'}
    calls, state = [], {'loaded': False}

    def run(command, **kwargs):
        calls.append(command)
        if command[:3] == ['docker', 'image', 'inspect']:
            return SimpleNamespace(returncode=0 if state['loaded'] else 1,
                                   stdout=json.dumps([actual]).encode())
        if command[:2] == ['docker', 'load']:
            state['loaded'] = True
            return SimpleNamespace(returncode=0)
        assert command == ['docker', 'tag', f.binding['config_digest'], fetch.SCAN_TAG]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(fetch.subprocess, 'run', run)
    assert fetch.load_image(f.destination, f.binding, fetch.SCAN_TAG) == actual
    assert ['docker', 'load', '--input', str(f.destination)] in calls
    assert ['docker', 'tag', f.binding['config_digest'], fetch.SCAN_TAG] in calls
    assert all(command[1] in {'image', 'load', 'tag'} for command in calls)
    for field, value in [('Id', 'other'), ('RootFS', {'Layers': []}), ('Os', 'windows'), ('Architecture', 'arm64')]:
        changed = {**actual, field: value}
        assert not fetch.exact_image(changed, f.binding)


def test_network_error_never_echoes_signed_url_or_token(fixture, monkeypatch, capsys):
    f = fixture
    monkeypatch.setattr(fetch, 'verify_publication', lambda *a: (_ for _ in ()).throw(
        URLError('https://objects.githubusercontent.com/?signed=SECRET;Bearer TOKEN')))
    assert fetch.main([]) == 1
    result = (f.root / '.cache/eval/approved-grafana-load.json').read_text()
    text = result + capsys.readouterr().out
    assert 'SECRET' not in text and 'TOKEN' not in text and 'objects.githubusercontent' not in text


def test_result_cannot_overwrite_source_or_use_old_tag(fixture, monkeypatch):
    f = fixture
    original = f.transport_path.read_bytes()
    assert fetch.main(['--output', str(f.transport_path)]) == 1
    assert f.transport_path.read_bytes() == original and not f.metadata_calls
    with pytest.raises(fetch.FetchError, match='namespace'):
        fetch.load_image(f.destination, f.binding, 'deepresearch-approved-grafana:20261026')


@pytest.fixture
def compressed_oci(tmp_path):
    """Two real gzip layers exercise identical compressed payloads and DiffIDs."""
    blobs = {}

    def add(raw, media):
        digest = 'sha256:' + sha(raw)
        blobs['blobs/sha256/' + digest[7:]] = raw
        return {'digest': digest, 'size': len(raw), 'mediaType': media}

    layers, diffs = [], []
    for name, data in [('usr/bin/app', b'expected ELF'), ('etc/test.conf', b'fixture configuration')]:
        stream = BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, BytesIO(data))
        raw = stream.getvalue()
        diffs.append('sha256:' + sha(raw))
        layers.append(add(gzip.compress(raw, mtime=0), 'application/vnd.oci.image.layer.v1.tar+gzip'))
    config = add(json.dumps({'os': 'linux', 'architecture': 'amd64',
                            'rootfs': {'type': 'layers', 'diff_ids': diffs}}).encode(),
                 'application/vnd.oci.image.config.v1+json')
    manifest = add(json.dumps({'schemaVersion': 2, 'config': config, 'layers': layers}).encode(),
                   'application/vnd.oci.image.manifest.v1+json')
    index = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json',
                        'manifests': [{**manifest, 'platform': {'os': 'linux', 'architecture': 'amd64'}}]}).encode()
    outer = {'oci-layout': b'{"imageLayoutVersion":"1.0.0"}', 'index.json': index, **blobs}
    path = tmp_path / 'synthetic-compressed.oci.tar'
    with tarfile.open(path, 'w') as archive:
        for name in ('blobs', 'blobs/sha256'):
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            archive.addfile(member)
        for name, data in outer.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, BytesIO(data))
    binding = {'archive_bytes': path.stat().st_size, 'archive_sha256': sha(path.read_bytes()),
               'root_layout': 'direct_manifest', 'index_digest': 'sha256:' + sha(index),
               'platform_manifest_digest': manifest['digest'], 'config_digest': config['digest'],
               'diff_ids': diffs, 'binary_sha256': {'usr/bin/app': sha(b'expected ELF')},
               'source_tag': 'deepresearch-grafana-candidate:ci'}
    return path, binding


def test_manifest_bridge_preserves_gzip_blobs_config_index_diffids_and_final_executable(compressed_oci):
    original, binding = compressed_oci
    original_bytes = original.read_bytes()
    fetch.verify_oci_archive(original, binding, max_archive_bytes=2 * 1024**3)
    derived, proof = fetch.prepare_docker_load_archive(original, binding, original.parent / 'derived.tar')
    assert original.read_bytes() == original_bytes
    with tarfile.open(original) as source, tarfile.open(derived) as destination:
        assert set(destination.getnames()) == {*source.getnames(), 'manifest.json'}
        for member in source.getmembers():
            if member.isfile():
                assert source.extractfile(member).read() == destination.extractfile(member.name).read()
        bridge = json.load(destination.extractfile('manifest.json'))
        assert bridge[0]['RepoTags'] == [binding['source_tag']]
        assert bridge[0]['Config'] == 'blobs/sha256/' + binding['config_digest'][7:]
        for path, diff_id in zip(bridge[0]['Layers'], binding['diff_ids'], strict=True):
            payload = destination.extractfile(path).read()
            assert payload[:3] == b'\x1f\x8b\x08'
            assert 'sha256:' + sha(gzip.decompress(payload)) == diff_id
    # Derived SHA is solely a factual transport check; this supplies no approval.
    facts = fetch.verify_oci_archive(derived, {**binding, 'archive_bytes': proof['derived_archive_bytes'],
        'archive_sha256': proof['derived_archive_sha256']}, max_archive_bytes=2 * 1024**3)
    assert facts['binary_sha256'] == binding['binary_sha256']
    assert facts['recomputed_diff_ids'] == binding['diff_ids']
    assert facts['config_digest'] == binding['config_digest']
    assert not facts['authorization_evaluated'] and not proof['authorization_evaluated']


def test_derived_integrity_rejects_a_changed_layer_and_source_hash_mismatch(compressed_oci):
    original, binding = compressed_oci
    derived, proof = fetch.prepare_docker_load_archive(original, binding, original.parent / 'derived.tar')
    changed = original.parent / 'changed.tar'
    with tarfile.open(derived) as source, tarfile.open(changed, 'w') as destination:
        manifest_bytes = source.extractfile('manifest.json').read()
        changed_path = json.loads(manifest_bytes)[0]['Layers'][0]
        for member in source.getmembers():
            if member.isfile():
                payload = source.extractfile(member).read()
                if member.name == changed_path:
                    payload = bytes([payload[0] ^ 1]) + payload[1:]
                destination.addfile(member, BytesIO(payload))
            else:
                destination.addfile(member)
    with pytest.raises(fetch.FetchError, match='payload changed'):
        fetch.verify_docker_load_archive(changed, proof['original_members'], manifest_bytes)
    with pytest.raises(fetch.FetchError, match='Cached candidate bytes differ'):
        fetch.prepare_docker_load_archive(original, {**binding, 'archive_sha256': 'f' * 64},
                                           original.parent / 'never-created.tar')
    assert not (original.parent / 'never-created.tar').exists()


@pytest.mark.parametrize('stderr', [b'permission denied', b'no space left on device',
    b'open /var/lib/docker/tmp/docker-import-123/blobs/json: permission denied',
    b'open /var/lib/docker/tmp/docker-import-123/other/json: no such file or directory'])
def test_unrelated_docker_errors_never_use_compatibility_fallback(compressed_oci, monkeypatch, stderr):
    archive, binding = compressed_oci
    monkeypatch.setattr(fetch, 'inspect', lambda *_: None)
    monkeypatch.setattr(fetch.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stderr=stderr))
    monkeypatch.setattr(fetch, 'prepare_docker_load_archive', lambda *a: pytest.fail('unrelated fallback'))
    with pytest.raises(fetch.DockerLoadError) as error:
        fetch.load_image(archive, binding, fetch.SCAN_TAG)
    assert len(error.value.diagnostics['attempts']) == 1
    assert error.value.diagnostics['attempts'][0]['stderr_excerpt'] == stderr.decode()


@pytest.mark.parametrize('outcome', ['success', 'retry-failed', 'wrong-diffids'])
def test_exact_legacy_lookup_allows_only_one_bridge_retry_with_private_temp_cleanup(
    compressed_oci, monkeypatch, outcome
):
    archive, binding = compressed_oci
    calls, loads = [], []
    actual = {'Id': binding['config_digest'], 'Os': 'linux', 'Architecture': 'amd64',
              'RootFS': {'Layers': binding['diff_ids']}}
    stderr = b'open /var/lib/docker/tmp/docker-import-3554767565/blobs/json: no such file or directory\n'

    def inspect(_reference):
        if len(loads) < 2:
            return None
        return {**actual, 'RootFS': {'Layers': []}} if outcome == 'wrong-diffids' else actual

    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ['docker', 'load']:
            loads.append(Path(command[-1]))
            if len(loads) == 1:
                return SimpleNamespace(returncode=1, stderr=stderr)
            assert len(loads) == 2 and loads[1].is_file() and loads[1].parent != archive.parent
            with tarfile.open(loads[1]) as derived:
                assert derived.getmember('manifest.json').isfile()
            return SimpleNamespace(returncode=int(outcome == 'retry-failed'), stderr=b'retry diagnostic')
        assert command[:2] == ['docker', 'tag']
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(fetch, 'inspect', inspect)
    monkeypatch.setattr(fetch.subprocess, 'run', run)
    receipt = {}
    if outcome == 'success':
        assert fetch.load_image(archive, binding, fetch.SCAN_TAG, load_receipt=receipt) == actual
        assert len([c for c in calls if c[:2] == ['docker', 'tag']]) == 1
    else:
        with pytest.raises(fetch.DockerLoadError) as error:
            fetch.load_image(archive, binding, fetch.SCAN_TAG)
        receipt = error.value.diagnostics
        assert not [c for c in calls if c[:2] == ['docker', 'tag']]
    assert len(loads) == 2 and loads[0] == archive and not loads[1].exists()
    assert not loads[1].parent.exists()
    assert receipt['compatibility_fallback_used'] and len(receipt['attempts']) == 2
    proof = receipt['derived_transport_integrity']
    assert proof['private_temporary_directory_removed'] and Path(proof['integrity_receipt']).is_file()
    assert json.loads(Path(proof['integrity_receipt']).read_text()) == proof
    assert sha(archive.read_bytes()) == binding['archive_sha256']


def test_docker_error_excerpt_is_bounded_and_redacts_urls_and_credentials(monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'secret-fixture-token')
    stderr = b'Bearer secret-fixture-token https://host.invalid/?signed=PRIVATE ' + b'x' * 8192
    result = fetch.load_diagnostic(SimpleNamespace(returncode=1, stderr=stderr), 'original_approved_oci')
    assert result['stderr_bytes'] == len(stderr) and result['stderr_sha256'] == sha(stderr)
    assert len(result['stderr_excerpt']) <= 4096 and result['stderr_excerpt_truncated']
    assert 'secret-fixture-token' not in result['stderr_excerpt'] and 'PRIVATE' not in result['stderr_excerpt']
