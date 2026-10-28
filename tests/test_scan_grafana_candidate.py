"""Only the new authorized subject reaches pinned native collection operations."""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import types

import pytest

from scripts import scan_grafana_candidate as collector


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, 'ROOT', tmp_path)
    monkeypatch.setattr(collector.gate, 'REQUEST_SHA256', 'e' * 64)
    layers = ['sha256:' + 'a' * 64]
    config = json.dumps({'rootfs': {'diff_ids': layers}}).encode()
    config_digest = 'sha256:' + hashlib.sha256(config).hexdigest()
    manifest = json.dumps({'config': {'digest': config_digest}}).encode()
    cpe = 'cpe:fixture:grafana:13.2.4'
    binding = {'artifact': 'grafana', 'archive_bytes': 1, 'archive_sha256': 'b' * 64,
        'index_digest': 'sha256:' + 'c' * 64, 'platform_manifest_digest': 'sha256:' + 'd' * 64,
        'config_digest': config_digest, 'diff_ids': layers,
        'binary_sha256': {'usr/share/grafana/bin/grafana': 'f' * 64},
        'allowed_findings': [{'id': f'CVE-FIXTURE-{i}', 'target': 'usr/share/grafana/bin/grafana',
            'package': 'fixture', 'version': '1', 'severity': 'HIGH',
            'disposition': 'NOT_AFFECTED'} for i in range(28)],
        'minimum_syft_types': {'binary': 1},
        'expected_native_application': {'name': 'grafana', 'version': '13.2.4', 'type': 'binary', 'cpe': cpe},
        'expires': '2026-11-25T23:59:59+08:00'}
    request = {'binding': binding}
    auth = {'accepted': True, 'request_sha256': collector.gate.REQUEST_SHA256,
            'subject': collector.gate.subject_for(binding),
            'exact_target_advisory_rows': copy.deepcopy(binding['allowed_findings']),
            'policy': {**dict.fromkeys(collector.gate.POLICY_FLAGS, True), 'expires': binding['expires']}}
    security = tmp_path / 'deployment/security'
    security.mkdir(parents=True)
    auth_path = tmp_path / collector.gate.AUTH_PATH
    auth_path.write_text(json.dumps(auth), encoding='utf-8')
    (security / 'native-jvm-tools.json').write_text(json.dumps({'database': {'fixture': True}}), encoding='utf-8')
    monkeypatch.setattr(collector.gate, 'load_request', lambda root: request)
    monkeypatch.setattr(collector.gate.legacy, 'load_binding',
                        lambda *_a, **_k: pytest.fail('Old exact18 loader cannot be used'))
    actual = {'Id': config_digest, 'Architecture': 'amd64', 'Os': 'linux', 'RootFS': {'Layers': layers}}
    sbom = {'source': {'metadata': {'imageID': config_digest, 'config': base64.b64encode(config).decode(),
            'manifest': base64.b64encode(manifest).decode(),
            'manifestDigest': 'sha256:' + hashlib.sha256(manifest).hexdigest()}},
            'artifacts': [{'type': 'binary', 'name': 'grafana', 'version': '13.2.4', 'cpes': [{'cpe': cpe}]}]}
    calls = []
    prepared = types.SimpleNamespace(env={'PINNED_DATABASE': 'true'},
        assert_unchanged=lambda stage: calls.append(('unchanged', stage)))

    def executable(tool, lock, cache, tool_dir):
        calls.append(('tool', tool, lock, cache, tool_dir))
        return Path('/mock') / tool

    def database(grype, spec, cache, output, env, evidence):
        calls.append(('database', spec, dict(env)))
        return prepared

    def run(args, *, env=None):
        calls.append(('run', [str(arg) for arg in args], env))
        if args[0] == 'docker':
            return json.dumps([actual]).encode()
        if Path(args[0]).name == 'syft':
            destination = next(str(arg).removeprefix('syft-json=') for arg in args
                               if str(arg).startswith('syft-json='))
            Path(destination).write_text(json.dumps(sbom), encoding='utf-8')
        else:
            Path(args[args.index('--file') + 1]).write_text('{"matches":[]}', encoding='utf-8')
        return b''

    monkeypatch.setattr(collector, 'executable', executable)
    monkeypatch.setattr(collector, 'prepare_database', database)
    monkeypatch.setattr(collector, 'run', run)
    args = argparse.Namespace(image='new-candidate:ci', output_dir=tmp_path / '.cache/eval/native',
        tool_dir=tmp_path / '.cache/tools', db_cache=tmp_path / '.cache/db')
    return args, auth_path, actual, sbom, calls, prepared


@pytest.mark.parametrize('change', ['pending', 'wrong_request', 'missing'])
def test_new_authorization_precedes_any_docker_tool_or_download(setup, change):
    args, path, _actual, _sbom, calls, _prepared = setup
    auth = json.loads(path.read_bytes())
    if change == 'pending':
        auth['accepted'] = False
    elif change == 'wrong_request':
        auth['request_sha256'] = '0' * 64
    else:
        auth = {}
    path.write_text(json.dumps(auth), encoding='utf-8')
    result = collector.collect(args)
    assert result['operational_success'] is result['security_gate_claim'] is False
    assert calls == [] and not args.output_dir.exists()


def test_actual_loaded_identity_is_checked_before_tools_or_database(setup):
    args, _auth, actual, _sbom, calls, _prepared = setup
    actual['Id'] = 'sha256:' + '0' * 64
    result = collector.collect(args)
    assert result['operational_success'] is False
    assert len(calls) == 1 and calls[0][0] == 'run' and calls[0][1][0] == 'docker'
    assert json.loads((args.output_dir / 'collector.json').read_bytes())['operational_success'] is False


def test_pinned_full_controls_and_native_source_chain_do_not_grant_a_gate_pass(setup, monkeypatch):
    args, _auth, actual, _sbom, calls, _prepared = setup
    monkeypatch.setenv('GRYPE_IGNORE', 'unreviewed')
    monkeypatch.setenv('SYFT_EXCLUDE', '/usr/share/grafana')
    result = collector.collect(args)
    assert result['operational_success'] is True
    assert result['security_gate_claim'] is result['artifact_approved'] is False
    assert result['actual_image_id'] == actual['Id'] and result['old_authorization_inherited'] is False
    database_call = next(call for call in calls if call[0] == 'database')
    assert database_call[1] == {'fixture': True}
    assert 'GRYPE_IGNORE' not in database_call[2] and 'SYFT_EXCLUDE' not in database_call[2]
    assert database_call[2]['GRYPE_CHECK_FOR_APP_UPDATE'] == 'false'
    assert ('unchanged', 'after-full-scan') in calls
    syconfig = json.loads((args.output_dir / 'syft-config.json').read_bytes())
    grconfig = json.loads((args.output_dir / 'grype-config.json').read_bytes())
    assert syconfig['exclude'] == [] and grconfig['ignore'] == []
    assert grconfig['only-fixed'] is grconfig['only-notfixed'] is False
    assert result['required_next_step'].startswith('check_grafana_candidate_exact28.py')


@pytest.mark.parametrize('failure', ['syft', 'grype', 'database_changed', 'native_sbom'])
def test_native_operation_or_identity_failure_remains_closed(setup, monkeypatch, failure):
    args, _auth, _actual, sbom, _calls, prepared = setup
    original = collector.run

    def run(command, **kwargs):
        if Path(command[0]).name == failure:
            raise ValueError('Required native operation failed')
        return original(command, **kwargs)

    monkeypatch.setattr(collector, 'run', run)
    if failure == 'database_changed':
        def changed(_stage):
            raise ValueError('Pinned native database changed')
        prepared.assert_unchanged = changed
    elif failure == 'native_sbom':
        sbom['source']['metadata']['imageID'] = 'sha256:' + '0' * 64
    result = collector.collect(args)
    assert result['operational_success'] is result['security_gate_claim'] is False
    assert result['artifact_approved'] is False
    assert json.loads((args.output_dir / 'collector.json').read_bytes())['operational_success'] is False
