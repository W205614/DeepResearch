"""Real parser/hash/selection counterexamples; fixtures never claim runtime acceptance."""
from argparse import Namespace
import base64
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import tarfile
from types import SimpleNamespace

import pytest

from scripts import check_grafana_source_build_20261110 as gate
from scripts import scan_grafana_source_20261110 as collector


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def put(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, sort_keys=True) + '\n').encode())
    return gate.digest(path)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


@pytest.fixture
def facts(tmp_path, monkeypatch):
    original = {gate.MAIN: sha(b'old-main')}
    original.update({f'usr/share/grafana/data/plugins-bundled/p{i}/p{i}_linux_amd64': sha(f'old-plugin-{i}'.encode())
                     for i in range(13)})
    monkeypatch.setattr(gate.retained, 'load_request', lambda root: {'binding': {'binary_sha256': original}})
    source = tmp_path / gate.DOCKERFILE
    source.parent.mkdir(parents=True)
    source.write_text('FROM scratch\n')
    native = {**original, gate.MAIN: sha(b'new-main')}
    layers = ['sha256:' + sha(b'fixture-layer')]
    config = {'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': layers},
              'config': {'User': '472', 'Env': ['PATH=/bin'], 'Entrypoint': ['/run.sh'],
                         'Cmd': None, 'WorkingDir': '/usr/share/grafana', 'Volumes': {},
                         'ExposedPorts': {'3000/tcp': {}}, 'Healthcheck': None, 'StopSignal': None}}
    config_raw = encode(config)
    image_id = 'sha256:' + sha(config_raw)
    manifest = {'schemaVersion': 2, 'config': {'digest': image_id}, 'layers': []}
    manifest_raw = encode(manifest)
    inspection = {'Id': image_id, 'Os': 'linux', 'Architecture': 'amd64',
                  'RootFS': {'Layers': layers}, 'Config': deepcopy(config['config'])}
    results = [{'Target': 'fixture-alpine', 'Class': 'os-pkgs', 'Type': 'alpine',
                'Packages': [{'Name': 'busybox', 'Version': '1.37.0-r31'}]},
               {'Target': 'Node.js', 'Class': 'lang-pkgs', 'Type': 'node-pkg',
                'Packages': [{'Name': 'fixture-web', 'Version': '1.0.0'}]}]
    results += [{'Target': name, 'Class': 'lang-pkgs', 'Type': 'gobinary',
                 'Packages': [{'Name': 'fixture-module-' + str(i), 'Version': 'v1.0.0'}]}
                for i, name in enumerate(native)]
    low = {'VulnerabilityID': 'CVE-FIXTURE-LOW', 'PkgName': 'busybox',
           'InstalledVersion': '1.37.0-r31', 'Severity': 'LOW'}
    results[0]['Vulnerabilities'] = [low]
    now = datetime.now(timezone.utc).isoformat()
    trivy = {'SchemaVersion': 2, 'ArtifactType': 'container_image', 'Trivy': {'Version': '0.75.0'},
             'CreatedAt': now, 'Metadata': {'ImageID': image_id, 'DiffIDs': layers}, 'Results': results}
    match = {'vulnerability': {'id': 'CVE-FIXTURE-MEDIUM', 'severity': 'Medium'},
             'artifact': {'name': 'busybox', 'version': '1.37.0-r31',
                          'locations': [{'path': '/lib/apk/db/installed'}]}}
    grype = {'descriptor': {'name': 'grype', 'version': '0.121.0', 'timestamp': now,
                           'db': {'status': {'built': now, 'valid': True}}},
             'source': {'target': {'imageID': image_id}}, 'matches': [match]}
    artifacts = [{'name': 'fixture-' + str(i), 'type': 'go-module', 'version': 'v1.0.0',
                  'locations': [{'path': '/' + name}]} for i, name in enumerate(native)]
    artifacts += [{'name': 'busybox', 'version': '1.37.0-r31', 'type': 'apk'},
                  {'name': 'grafana', 'version': '13.2.5', 'type': 'binary'}]
    sbom = {'source': {'type': 'image', 'metadata': {'userInput': image_id, 'imageID': image_id,
        'config': base64.b64encode(config_raw).decode(), 'manifest': base64.b64encode(manifest_raw).decode(),
        'manifestDigest': 'sha256:' + sha(manifest_raw)}}, 'artifacts': artifacts}
    inventory = {}
    for result in results:
        key = result['Type'] if result['Class'] == 'os-pkgs' else result['Target']
        inventory[key] = sha(encode(sorted(gate.retained.shared.package_counter(result['Packages']).items())))
    receipt = {'schema': 'grafana-source-build-receipt-v1', 'profile': gate.PROFILE, 'image': gate.IMAGE,
        'source_inputs': {gate.DOCKERFILE: gate.digest(source)}, 'native_sha256': native,
        'runtime_manifest_sha256': sha(b'fixture-complete-runtime-manifest'),
        'runtime_config': gate.runtime_config(config['config']), 'image_id': image_id,
        'config_digest': image_id, 'diff_ids': layers}
    evidence = {'deployment/security/fixture-facts.json': put(tmp_path, 'deployment/security/fixture-facts.json',
                                                            {'kind': 'synthetic parser fixture'})}
    reviewed = {'Trivy': [{'id': low['VulnerabilityID'], 'target': 'fixture-alpine', 'package': low['PkgName'],
        'version': low['InstalledVersion'], 'severity': 'LOW', 'disposition': 'FIXED',
        'technical_basis': 'Synthetic fixed-code fact, never a production waiver', 'evidence': list(evidence)}],
        'Grype': [{'id': match['vulnerability']['id'], 'target': 'lib/apk/db/installed', 'package': 'busybox',
        'version': '1.37.0-r31', 'severity': 'MEDIUM', 'disposition': 'FIXED',
        'technical_basis': 'Synthetic fixed-code fact, never a production waiver', 'evidence': list(evidence)}]}
    profile = {'schema': 'grafana-source-build-20261110-v1', 'status': 'verified', 'profile': gate.PROFILE,
        'image': gate.IMAGE, 'dockerfile': gate.DOCKERFILE, 'context': '.', 'source_inputs': receipt['source_inputs'],
        'expected': {**{k: receipt[k] for k in ('native_sha256', 'runtime_manifest_sha256', 'runtime_config')},
                     'syft_types': dict(Counter(a['type'] for a in artifacts)), 'trivy_inventory_sha256': inventory},
        'reviewed_findings': reviewed, 'evidence_sha256': evidence,
        'independent_reviews': ['deployment/security/fixture-review-a.json', 'deployment/security/fixture-review-b.json']}
    for index, name in enumerate(profile['independent_reviews']):
        review = {'actor': '/root/fixture-reviewer-' + str(index), 'actor_kind': 'automated_independent_agent',
                  'facts_passed': True, 'risk_acceptance': False, 'binary_redistribution_approved': False,
                  'subject': gate.review_subject(profile)}
        evidence[name] = put(tmp_path, name, review)
    put(tmp_path, gate.PROFILE_PATH, profile)
    return SimpleNamespace(root=tmp_path, profile=profile, receipt=receipt, inspection=inspection,
                           trivy=trivy, grype=grype, sbom=sbom, original_native=original)


def assess(facts):
    profile = gate.load_profile(facts.root)
    gate.verify_receipt(facts.receipt, facts.inspection, profile, root=facts.root)
    return gate.assess_scans(facts.trivy, facts.grype, facts.sbom, facts.inspection, facts.receipt, profile,
                            trivy_exit_code=0, grype_exit_code=0)


def test_all_severity_exact_facts_pass_without_claiming_binary_redistribution(facts):
    result = assess(facts)
    assert result['passed'] and result['unresolved_count'] == 0
    assert result['verified_all_severity_findings'] == {'Trivy': 1, 'Grype': 1}
    assert result['raw_counts'] == {'Trivy': {'LOW': 1}, 'Grype': {'MEDIUM': 1}}
    assert result['binary_redistribution_approved'] is False


@pytest.mark.parametrize('mutation', ['new_low', 'new_medium', 'missing_low', 'missing_medium', 'duplicate_low'])
def test_low_and_medium_are_required_exact_findings_not_unchecked_remainders(facts, mutation):
    low = facts.trivy['Results'][0]['Vulnerabilities']
    medium = facts.grype['matches']
    if mutation == 'new_low':
        low.append({**low[0], 'VulnerabilityID': 'CVE-FIXTURE-NEW-LOW'})
    elif mutation == 'new_medium':
        medium.append({**medium[0], 'vulnerability': {'id': 'CVE-FIXTURE-NEW-MEDIUM', 'severity': 'Medium'}})
    elif mutation == 'missing_low':
        low.clear()
    elif mutation == 'missing_medium':
        medium.clear()
    else:
        low.append(deepcopy(low[0]))
    with pytest.raises(ValueError):
        assess(facts)


@pytest.mark.parametrize('mutation', ['image_id', 'diff_ids', 'configuration', 'source_hash', 'native', 'missing_plugin'])
def test_receipt_cannot_borrow_another_image_source_or_runtime_configuration(facts, mutation):
    if mutation == 'image_id':
        facts.receipt['image_id'] = 'sha256:' + 'f' * 64
    elif mutation == 'diff_ids':
        facts.inspection['RootFS']['Layers'] = ['sha256:' + 'f' * 64]
    elif mutation == 'configuration':
        facts.inspection['Config']['User'] = '0'
    elif mutation == 'source_hash':
        (facts.root / gate.DOCKERFILE).write_text('FROM changed-source\n')
    elif mutation == 'native':
        facts.receipt['native_sha256'] = {**facts.receipt['native_sha256'], gate.MAIN: 'f' * 64}
    else:
        facts.receipt['native_sha256'] = {k: v for k, v in facts.receipt['native_sha256'].items() if k != next(
            name for name in facts.receipt['native_sha256'] if name != gate.MAIN)}
    with pytest.raises(ValueError):
        assess(facts)


@pytest.mark.parametrize('mutation', ['missing_target', 'filtered', 'ignored_native', 'invalid_db', 'stale_trivy',
                                    'stale_grype', 'stale_database', 'wrong_trivy_id', 'wrong_diffids', 'wrong_native_id'])
def test_failed_filtered_stale_or_incomplete_scan_cannot_pass(facts, mutation):
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    if mutation == 'missing_target':
        facts.trivy['Results'].pop()
    elif mutation == 'filtered':
        facts.trivy['SuppressedFindings'] = [{'id': 'CVE-FIXTURE-HIDDEN'}]
    elif mutation == 'ignored_native':
        facts.grype['ignoredMatches'] = [{'vulnerability': {'id': 'CVE-FIXTURE-HIDDEN'}}]
    elif mutation == 'invalid_db':
        facts.grype['descriptor']['db']['status']['valid'] = False
    elif mutation == 'stale_trivy':
        facts.trivy['CreatedAt'] = old
    elif mutation == 'stale_grype':
        facts.grype['descriptor']['timestamp'] = old
    elif mutation == 'stale_database':
        facts.grype['descriptor']['db']['status']['built'] = old
    elif mutation == 'wrong_trivy_id':
        facts.trivy['Metadata']['ImageID'] = 'sha256:' + 'f' * 64
    elif mutation == 'wrong_diffids':
        facts.trivy['Metadata']['DiffIDs'] = ['sha256:' + 'f' * 64]
    else:
        facts.grype['source']['target']['imageID'] = 'sha256:' + 'f' * 64
    with pytest.raises(ValueError):
        assess(facts)


def test_native_manifest_chain_is_verified_even_when_the_config_hash_matches(facts):
    facts.sbom['source']['metadata']['manifest'] = base64.b64encode(encode({'config': {'digest': 'sha256:' + 'f' * 64}})).decode()
    with pytest.raises(ValueError):
        assess(facts)


def test_same_filesystem_with_forged_sbom_runtime_config_is_not_the_actual_image(facts):
    metadata = facts.sbom['source']['metadata']
    config = json.loads(base64.b64decode(metadata['config']))
    config['config']['Cmd'] = ['unreviewed-command']
    raw = encode(config)
    altered_id = 'sha256:' + sha(raw)
    manifest = encode({'schemaVersion': 2, 'config': {'digest': altered_id}, 'layers': []})
    metadata.update(config=base64.b64encode(raw).decode(), imageID=altered_id,
                    manifest=base64.b64encode(manifest).decode(), manifestDigest='sha256:' + sha(manifest))
    facts.receipt['config_digest'] = altered_id
    facts.grype['source']['target']['imageID'] = altered_id
    with pytest.raises(ValueError):
        assess(facts)


@pytest.mark.parametrize('mutation', ['review_subject', 'unbound_review', 'missing_expected_target',
                                    'changed_runtime_contract', 'changed_technical_evidence'])
def test_independent_reviews_bind_the_full_current_subject(facts, mutation):
    profile = facts.profile
    if mutation == 'review_subject':
        name = profile['independent_reviews'][0]
        review = gate.read(facts.root / name)
        review['subject'] = {'profile': 'another-subject'}
        profile['evidence_sha256'][name] = put(facts.root, name, review)
    elif mutation == 'unbound_review':
        profile['evidence_sha256'].pop(profile['independent_reviews'][0])
    elif mutation == 'missing_expected_target':
        profile['expected']['trivy_inventory_sha256'].pop(gate.MAIN)
    elif mutation == 'changed_technical_evidence':
        name = 'deployment/security/fixture-facts.json'
        profile['evidence_sha256'][name] = put(facts.root, name, {'kind': 'changed proof requiring new review'})
    else:
        profile['expected']['runtime_config'] = {**profile['expected']['runtime_config'], 'User': '0'}
    put(facts.root, gate.PROFILE_PATH, profile)
    with pytest.raises(ValueError):
        gate.load_profile(facts.root)


def tar_file(name, payload):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    stream.seek(0)
    return stream


def test_actual_native_copy_uses_payload_hash_and_rejects_other_entries():
    assert collector.copied_file_hash(tar_file('grafana', b'actual-native-fixture'), 'grafana') == sha(b'actual-native-fixture')
    with pytest.raises(ValueError):
        collector.copied_file_hash(tar_file('other', b'actual-native-fixture'), 'grafana')


def test_preparation_requires_new_main_all_old_plugins_and_current_sources(facts):
    profile = collector.preparation_profile(facts.receipt, facts.root)
    gate.verify_receipt(facts.receipt, facts.inspection, profile, root=facts.root)
    plugin = next(name for name in facts.receipt['native_sha256'] if name != gate.MAIN)
    facts.receipt['native_sha256'][plugin] = 'f' * 64
    with pytest.raises(ValueError):
        collector.preparation_profile(facts.receipt, facts.root)


def test_default_collector_rejects_pending_profile_before_any_docker_or_tool_operation(facts, monkeypatch):
    facts.profile['status'] = 'pending'
    put(facts.root, gate.PROFILE_PATH, facts.profile)
    monkeypatch.setattr(collector, 'run', lambda *_a, **_k: pytest.fail('Operation before review readiness'))
    args = Namespace(image=gate.IMAGE, output_dir=facts.root / '.cache/new-collection',
                     build_receipt=facts.root / 'receipt.json', prepare=False,
                     tool_dir=facts.root / '.cache/tools', db_cache=facts.root / '.cache/db')
    result = collector.collect(args, root=facts.root)
    assert not result['operational_collection'] and not result['verified']
    assert not args.output_dir.exists()


@pytest.mark.parametrize('prepare', [False, True])
def test_full_native_collection_preserves_unresolved_raw_and_never_grants_acceptance(facts, monkeypatch, prepare):
    receipt_path = facts.root / 'receipt.json'
    put(facts.root, 'receipt.json', facts.receipt)
    tools = {name: facts.root / '.cache/tools' / name for name in ('syft', 'grype')}
    for name, path in tools.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('synthetic pinned-tool boundary ' + name).encode())
    put(facts.root, 'deployment/security/native-jvm-tools.json', {
        'tools': {'syft': {'version': 'v1.55.0'}, 'grype': {'version': 'v0.121.0'}},
        'database': {'schema': 'fixture-controlled-import'}})
    unresolved = {'vulnerability': {'id': 'CVE-FIXTURE-UNRESOLVED', 'severity': 'High'},
                  'artifact': {'name': 'fixture-module', 'version': 'v0.24.0',
                               'locations': [{'path': '/' + gate.MAIN}]}}
    facts.grype['matches'].append(unresolved)
    raw_grype = encode(facts.grype) + b'\n'
    raw_sbom = encode(facts.sbom) + b'\n'
    operations, database_checks = [], []
    monkeypatch.setenv('SYFT_EXCLUDE', '/usr/share/grafana')
    monkeypatch.setenv('GRYPE_IGNORE', 'CVE-FIXTURE-UNRESOLVED')
    if prepare:
        monkeypatch.setattr(gate, 'load_profile', lambda *_a, **_k: pytest.fail('Prepare must not invent a verified profile'))
    monkeypatch.setattr(collector, 'executable', lambda name, *_a: tools[name])
    monkeypatch.setattr(collector, 'verify_actual_native',
        lambda image_id, expected, evidence: evidence.update(native_sha256=dict(expected), removed=True))

    def database(_tool, spec, _cache, _output, env, _evidence):
        assert spec['schema'] == 'fixture-controlled-import'
        assert 'SYFT_EXCLUDE' not in env and 'GRYPE_IGNORE' not in env
        return SimpleNamespace(env=env, assert_unchanged=database_checks.append)

    monkeypatch.setattr(collector, 'prepare_database', database)

    def run(arguments, *, env=None, **_kwargs):
        operations.append(arguments)
        if arguments[:3] == ['docker', 'image', 'inspect']:
            return encode([facts.inspection])
        if arguments[1:] == ['version', '-o', 'json']:
            return encode({'version': '1.55.0' if arguments[0] == tools['syft'] else '0.121.0'})
        assert env is not None and 'SYFT_EXCLUDE' not in env and 'GRYPE_IGNORE' not in env
        config = json.loads(arguments[arguments.index('--config') + 1].read_bytes())
        if arguments[0] == tools['syft']:
            assert config['exclude'] == [] and '--override-default-catalogers' not in arguments
            assert arguments[arguments.index('scan') + 1] == 'docker:' + facts.inspection['Id']
            output = arguments[arguments.index('-o') + 1].removeprefix('syft-json=')
            (facts.root / output).write_bytes(raw_sbom)
        else:
            assert config['ignore'] == [] and not config['only-fixed'] and not config['only-notfixed']
            arguments[arguments.index('--file') + 1].write_bytes(raw_grype)
        return b''

    monkeypatch.setattr(collector, 'run', run)
    args = Namespace(image=gate.IMAGE, output_dir=facts.root / '.cache/full-native', build_receipt=receipt_path,
                     prepare=prepare, tool_dir=facts.root / '.cache/tools', db_cache=facts.root / '.cache/db')
    result = collector.collect(args, root=facts.root)
    assert result.get('operational_collection'), result
    assert not result['verified'] and not result['security_gate_claim'] and not result['binary_redistribution_approved']
    assert result['sourceBuild']['profile_review_verified'] is (not prepare)
    assert result['raw_grype_counts'] == {'Medium': 1, 'High': 1}
    assert (args.output_dir / 'full-grype.json').read_bytes() == raw_grype
    assert (args.output_dir / 'full-syft.json').read_bytes() == raw_sbom
    assert database_checks == ['after-full-source-build-scan']
    assert len(operations) == 5


def test_actual_native_hash_failure_still_removes_only_its_owned_never_started_container(monkeypatch):
    container = 'a' * 64
    image_id = 'sha256:' + 'b' * 64
    operations = []

    def run(arguments):
        operations.append(arguments)
        if arguments[:2] == ['docker', 'create']:
            return container.encode()
        if arguments[:2] == ['docker', 'inspect']:
            name = operations[0][operations[0].index('--name') + 1]
            return encode([{'Name': '/' + name, 'Image': image_id, 'State': {'Status': 'created'},
                            'HostConfig': {'NetworkMode': 'none'},
                            'Config': {'Labels': {'com.deepresearch.purpose': 'grafana-native-facts'}}}])
        assert arguments == ['docker', 'rm', '-v', container]
        return b''

    monkeypatch.setattr(collector, 'run', run)
    monkeypatch.setattr(collector.subprocess, 'Popen', lambda *_a, **_k: SimpleNamespace(
        stdout=tar_file('grafana', b'wrong-native-bytes'), wait=lambda **_kw: 0, poll=lambda: 0))
    evidence = {}
    with pytest.raises(ValueError, match='Actual Grafana ELF'):
        collector.verify_actual_native(image_id, {gate.MAIN: sha(b'reviewed-native-bytes')}, evidence)
    assert evidence['removed'] is True
    assert operations[-1] == ['docker', 'rm', '-v', container]
