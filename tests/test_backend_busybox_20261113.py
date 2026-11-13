from copy import deepcopy
import io
import json
import struct
import tarfile

import pytest

from scripts import check_backend_busybox_20261113 as gate


def archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as target:
        for name, raw in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            target.addfile(member, io.BytesIO(raw))
    return stream.getvalue()


@pytest.fixture
def contract(tmp_path):
    # Synthetic boundary fixtures are separate from the actual image/scan replay.
    binary = bytearray(32)
    binary[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<H', binary, 18, 62)
    binary = bytes(binary)
    legal = archive({'metadata/build.json': b'{}', 'metadata/backend-benign-regression.json': b'{}'})
    link = io.BytesIO()
    with tarfile.open(fileobj=link, mode='w') as target:
        member = tarfile.TarInfo('sh')
        member.type = tarfile.SYMTYPE
        member.linkname = '/bin/busybox'
        target.addfile(member)
    paths = ['/bin/busybox', '/usr/bin/ssl_client', '/lib/ld-musl-x86_64.so.1']
    payload = {path: {'bytes': len(binary), 'sha256': gate.sha(binary)} for path in paths}
    needed = {path: ['libc.musl-x86_64.so.1'] for path in paths}
    identities = [['CVE-2025-60876', name, '1.37.0-r31', 'apk', 'Medium', 'nvd:cpe']
                  for name in ['busybox', 'busybox-binsh', 'ssl_client']]
    profile = {'schema': 'backend-busybox-fixed-source-20261113-v1', 'risk_acceptance': False,
               'raw_report_modified': False, 'source_inputs': {'source.txt': gate.sha(b'fixed-source')},
               'evidence_sha256': {'source.txt': gate.sha(b'fixed-source')},
               'payload': payload, 'needed_libraries': needed, 'legal_source_files': gate.archive_files(legal),
               'retained_finding_identities': identities, 'dispositions': ['fixed', 'shared-fixed', 'not-affected-relay']}
    (tmp_path / 'source.txt').write_bytes(b'fixed-source')
    (tmp_path / gate.PROFILE).parent.mkdir(parents=True)
    (tmp_path / gate.PROFILE).write_text(json.dumps(profile))
    sbom = {'files': [{'location': {'path': path}, 'digests': [{'algorithm': 'sha256', 'value': gate.sha(binary)}],
                       'executable': {'importedLibraries': needed[path]}} for path in paths],
            'source': {'metadata': {'imageID': 'sha256:fixture'}}}
    report = {'source': {'target': {'imageID': 'sha256:fixture'}}, 'matches': [
        {'vulnerability': {'id': row[0], 'severity': row[4], 'namespace': row[5]},
         'artifact': {'name': row[1], 'version': row[2], 'type': row[3]}} for row in identities]}
    return tmp_path, binary, legal, link.getvalue(), sbom, report


def evaluate(contract, **changes):
    root, binary, legal, link, sbom, report = contract
    return gate.assess(changes.get('report', report), changes.get('sbom', sbom),
                       changes.get('busybox', binary), binary, link, changes.get('legal', legal), binary, root=root)


def test_exact_payload_source_closure_and_all_three_raw_rows_are_required(contract):
    verdict = evaluate(contract)
    assert verdict['passed'] and verdict['raw_findings'] == 3
    assert verdict['all_severity_technical_clear'] and not verdict['raw_report_modified']


def test_original_or_replaced_payload_cannot_inherit_fixed_code_review(contract):
    with pytest.raises(ValueError, match='payload differs'):
        evaluate(contract, busybox=contract[1] + b'changed')


@pytest.mark.parametrize('change', ['added', 'missing', 'severity', 'id', 'version', 'ignored'])
def test_any_full_raw_finding_change_requires_a_new_review(contract, change):
    report = deepcopy(contract[-1])
    if change == 'added':
        report['matches'].append(deepcopy(report['matches'][0]))
    elif change == 'missing':
        report['matches'].pop()
    elif change == 'ignored':
        report['ignoredMatches'] = [report['matches'][0]]
    elif change in {'severity', 'id'}:
        report['matches'][0]['vulnerability'][change] = 'unreviewed'
    else:
        report['matches'][0]['artifact'][change] = 'unreviewed'
    with pytest.raises(ValueError, match='all-severity'):
        evaluate(contract, report=report)


def test_current_source_change_cannot_reuse_profile(contract):
    (contract[0] / 'source.txt').write_bytes(b'changed-source')
    with pytest.raises(ValueError, match='reviewed source changed'):
        evaluate(contract)


def test_actual_sbom_native_dependencies_and_digest_cannot_be_substituted(contract):
    for field, replacement in [('digests', [{'algorithm': 'sha256', 'value': '0' * 64}]),
                               ('executable', {'importedLibraries': ['libunreviewed.so']})]:
        sbom = deepcopy(contract[-2])
        sbom['files'][0][field] = replacement
        with pytest.raises(ValueError):
            evaluate(contract, sbom=sbom)


def test_additional_or_missing_legal_source_content_is_rejected(contract):
    with pytest.raises(ValueError, match='closure differs'):
        evaluate(contract, legal=archive({'metadata/build.json': b'{}'}))
    with pytest.raises(ValueError, match='Unsafe'):
        gate.archive_files(archive({'../escape': b'no'}))


def test_backend_build_uses_explicit_sources_and_never_runs_adversarial_regression():
    recipe = (gate.ROOT / 'business-backend/Dockerfile').read_text()
    assert 'COPY deployment/vendors/grafana-20261110/busybox/ /recipe/' in recipe
    assert 'RUN sh /recipe/build.sh' in recipe
    assert 'RUN --network=none python3 /backend_busybox_benign.py' in recipe
    assert 'RUN --network=none python3 /recipe/regression.py' not in recipe
    assert not any(line.startswith(('COPY . ', 'ADD ')) for line in recipe.splitlines())
    assert 'COPY --from=backend-busybox-fix /out/busybox /bin/busybox' in recipe
    assert '/usr/share/deepresearch/backend-busybox/corresponding-source/' in recipe


def test_backend_inventory_selects_root_context_and_preserves_native_profile():
    from scripts import runtime_ci_inventory as inventory

    configuration = {'services': {'minio': {'image': inventory.APPROVED_MINIO},
                     'backend': {'build': {'context': str(gate.ROOT), 'dockerfile': 'business-backend/Dockerfile'}}}}
    _, custom = inventory.matrices(configuration, gate.ROOT)
    assert custom['include'] == [{'name': 'backend', 'context': '.', 'dockerfile': 'business-backend/Dockerfile',
                                  'services': 'backend', 'native_jvm_profile': 'business-java'}]
    configuration['services']['backend']['build'] = {'context': str(gate.ROOT / 'business-backend')}
    with pytest.raises(ValueError, match='Backend must include'):
        inventory.matrices(configuration, gate.ROOT)


def test_cloud_build_overlay_changes_only_backend_build_and_keeps_frozen_template():
    import yaml

    overlay = yaml.safe_load((gate.ROOT / 'deployment/compose/compose.cloud.backend-build.yaml').read_bytes())
    assert overlay == {'services': {'backend': {'build': {
        'context': '.', 'dockerfile': 'business-backend/Dockerfile',
    }}}}
    assert gate.sha((gate.ROOT / 'deployment/compose/compose.cloud.yaml').read_bytes()) == (
        '3212fded2d09598aba9562a31efbfb63d8919ee9401d7f01872f83c9b137b2c9')
