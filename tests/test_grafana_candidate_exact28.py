"""New exact28 facts cannot inherit old acceptance or hide incomplete scans."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from scripts import check_grafana_candidate_exact28 as gate


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def facts(monkeypatch):
    # Small synthetic identities exercise the real shared scanner evaluator.
    # OCI byte integrity is covered separately by its bounded verifier tests.
    monkeypatch.setattr(gate, 'REQUEST_SHA256', 'e' * 64)
    main = 'usr/share/grafana/bin/grafana'
    targets = [main] + [f'usr/share/grafana/plugin{index}/backend' for index in range(13)]
    tempo = targets[1]
    allowed = []
    for index in range(11):
        allowed.append({'id': f'CVE-FIXTURE-{index}', 'target': main, 'package': 'grafana-app',
                        'version': 'v0.0.0-fixture', 'severity': 'CRITICAL' if index < 2 else 'HIGH',
                        'disposition': 'NOT_AFFECTED'})
    for advisory in ('CVE-2026-21728', 'CVE-2026-28377'):
        allowed.append({'id': advisory, 'target': tempo, 'package': 'github.com/grafana/tempo',
                        'version': 'v1.5.1-fixture', 'severity': 'HIGH', 'disposition': 'FIXED'})
    for target in targets[:5]:
        allowed.append({'id': 'GO-2026-5932', 'target': target, 'package': 'golang.org/x/crypto',
                        'version': 'v0.58.0', 'severity': 'UNKNOWN', 'disposition': 'NOT_AFFECTED'})
    for target in targets[5:10]:
        for advisory in ('CVE-2026-78661', 'CVE-2026-97045'):
            allowed.append({'id': advisory, 'target': target, 'package': 'golang.org/x/net',
                            'version': 'v0.61.0', 'severity': 'UNKNOWN', 'disposition': 'NOT_AFFECTED'})
    aliases = []
    for raw, canonical in [('GHSA-p4r4-xvrq-gvmc', 'CVE-2026-21728'),
                           ('GHSA-ffqx-q65f-36jf', 'CVE-2026-28377')]:
        aliases.append({'scanner': 'Grype', 'raw_id': raw, 'canonical_id': canonical,
                        'target': tempo, 'package': 'github.com/grafana/tempo',
                        'version': 'v1.5.1-fixture', 'severity': 'HIGH'})
    grype_rows = [{**row, 'id': next((a['raw_id'] for a in aliases if a['canonical_id'] == row['id']),
                                    row['id'])} for row in allowed if
                  row['id'] in {'CVE-2026-21728', 'CVE-2026-28377', 'GO-2026-5932'}]
    diff_ids = ['sha256:' + 'a' * 64]
    config = json.dumps({'architecture': 'amd64', 'os': 'linux',
                         'rootfs': {'type': 'layers', 'diff_ids': diff_ids}}).encode()
    config_digest = 'sha256:' + sha(config)
    manifest = json.dumps({'config': {'digest': config_digest}}).encode()
    cpe = 'cpe:2.3:a:grafana:grafana:13.2.4:*:*:*:*:*:*:*'
    sbom = {'source': {'metadata': {'imageID': config_digest, 'config': base64.b64encode(config).decode(),
            'manifest': base64.b64encode(manifest).decode(), 'manifestDigest': 'sha256:' + sha(manifest)}},
            'artifacts': [{'name': 'grafana', 'version': '13.2.4', 'type': 'binary', 'cpes': [{'cpe': cpe}]},
                          {'name': 'fixture-apk', 'version': '1', 'type': 'apk'},
                          {'name': 'fixture-npm', 'version': '1', 'type': 'npm'},
                          {'name': 'fixture-go', 'version': '1', 'type': 'go-module'}]}
    results = [{'Target': 'fixture (alpine 3.24.2)', 'Class': 'os-pkgs', 'Type': 'alpine',
                'Packages': [{'Name': 'fixture-apk', 'Version': '1'}]},
               {'Target': 'Node.js', 'Class': 'lang-pkgs', 'Type': 'node-pkg',
                'Packages': [{'Name': 'fixture-npm', 'Version': '1'}]}]
    inventory = {'alpine': [{'name': 'fixture-apk', 'version': '1'}],
                 'Node.js': [{'name': 'fixture-npm', 'version': '1'}]}
    for target in targets:
        rows = [row for row in allowed if row['target'] == target]
        pairs = {(row['package'], row['version']) for row in rows} or {('fixture-module', '1')}
        if target == main:
            pairs.add(('fixture-local', None))
        packages = [{'Name': name, 'Version': version} for name, version in sorted(pairs)]
        inventory[target] = [{'name': p['Name'], 'version': p['Version']} for p in packages]
        results.append({'Target': target, 'Class': 'lang-pkgs', 'Type': 'gobinary', 'Packages': packages,
                        'Vulnerabilities': [{'VulnerabilityID': r['id'], 'PkgName': r['package'],
                            'InstalledVersion': r['version'], 'Severity': r['severity']} for r in rows]})
    coverage = {target: len(packages) for target, packages in inventory.items()}
    binding = {'artifact': 'grafana', 'root_layout': 'direct_manifest',
        'archive_bytes': 1, 'archive_sha256': 'b' * 64, 'archive_limit_bytes': gate.MAX_ARCHIVE_BYTES,
        'index_digest': 'sha256:' + 'c' * 64, 'platform_manifest_digest': 'sha256:' + 'd' * 64,
        'config_digest': config_digest, 'diff_ids': diff_ids,
        'binary_sha256': {target: 'f' * 64 for target in targets},
        'allowed_findings': allowed, 'exact_trivy_rows': copy.deepcopy(allowed),
        'exact_grype_rows': grype_rows, 'scanner_exact_aliases': aliases,
        'exact_trivy_coverage': coverage, 'minimum_trivy_coverage': coverage,
        'exact_trivy_inventory': inventory,
        'exact_syft_types': {'apk': 1, 'npm': 1, 'go-module': 1, 'binary': 1},
        'minimum_syft_types': {'apk': 1, 'npm': 1, 'go-module': 1, 'binary': 1},
        'expected_native_application': {'name': 'grafana', 'version': '13.2.4', 'type': 'binary', 'cpe': cpe},
        'inventory_identity': {'local_source_modules': [{'target': main, 'module': 'fixture-local',
            'elf_sha256': 'f' * 64, 'source_go_mod': 'sources/local.mod',
            'module_directive': 'module fixture-local', 'replace_file': 'sources/main.mod',
            'replace_directive': 'replace fixture-local => ./fixture-local'}]},
        'evidence_files': {}, 'recipe_files': {},
        'expires': (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()}
    trivy = {'SchemaVersion': 2, 'Trivy': {'Version': '0.75.0'}, 'ArtifactType': 'container_image',
             'Metadata': {'ImageID': config_digest, 'DiffIDs': diff_ids}, 'Results': results}
    grype = {'matches': [{'vulnerability': {'id': row['id'], 'severity': row['severity'].title()},
                          'artifact': {'name': row['package'], 'version': row['version'],
                                       'locations': [{'path': '/' + row['target']}]}} for row in grype_rows],
              'source': {'target': {'imageID': config_digest}},
              'descriptor': {'db': {'status': {'valid': True}}}, 'ignoredMatches': []}
    inspection = {'Id': config_digest, 'Architecture': 'amd64', 'Os': 'linux',
                  'RootFS': {'Layers': diff_ids}}
    request = {'schema': 'deepresearch-grafana-candidate-request-v1', 'technical_ready': True,
               'expires': binding['expires'], 'binding': binding}
    return trivy, grype, sbom, inspection, request


def verify(facts):
    return gate.verify_full_scans(*facts[:4], facts[4]['binding'], trivy_exit_code=0, grype_exit_code=0)


def authorization(request):
    return {'accepted': True, 'request_sha256': gate.REQUEST_SHA256,
            'subject': gate.subject_for(request['binding']),
            'exact_target_advisory_rows': copy.deepcopy(request['binding']['allowed_findings']),
            'policy': {**dict.fromkeys(gate.POLICY_FLAGS, True), 'expires': request['expires']}}


def test_exact28_real_evaluator_preserves_raw_and_cannot_load_old_approval(facts, monkeypatch):
    monkeypatch.setattr(gate.legacy, 'load_binding', lambda *_a, **_k: pytest.fail('Old acceptance used'))
    before = copy.deepcopy(facts)
    gate.validate_contract(facts[4]['binding'])
    result = verify(facts)
    assert result['passed'] and len(result['verified_dispositions']['Trivy']) == 28
    assert len(result['verified_dispositions']['Grype']) == 7
    assert result['raw_reports_modified'] is False and facts == before


@pytest.mark.parametrize('trivy_code,grype_code', [(1, 0), (0, 1)])
def test_operational_scanner_failure_cannot_be_reviewed_away(facts, trivy_code, grype_code):
    with pytest.raises(ValueError):
        gate.verify_full_scans(*facts[:4], facts[4]['binding'],
                              trivy_exit_code=trivy_code, grype_exit_code=grype_code)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'extra', 'id', 'version', 'severity', 'target'])
def test_exact_trivy_counter_closes_the_legacy_missing_row_gap(facts, change):
    record = facts[0]['Results'][2]
    rows = record['Vulnerabilities']
    if change == 'missing':
        rows.pop()
    elif change == 'duplicate':
        rows.append(copy.deepcopy(rows[0]))
    elif change == 'extra':
        rows.append({**rows[0], 'VulnerabilityID': 'UNREVIEWED'})
    elif change == 'target':
        record['Target'] = 'usr/bin/unreviewed'
    else:
        key = {'id': 'VulnerabilityID', 'version': 'InstalledVersion', 'severity': 'Severity'}[change]
        rows[0][key] = 'LOW' if change == 'severity' else 'CHANGED'
    with pytest.raises(ValueError):
        verify(facts)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'extra', 'duplicate_location', 'alias'])
def test_exact_grype_counter_closes_missing_or_duplicated_native_rows(facts, change):
    matches = facts[1]['matches']
    if change == 'missing':
        matches.pop()
    elif change == 'duplicate':
        matches.append(copy.deepcopy(matches[0]))
    elif change == 'extra':
        matches.append({**matches[0], 'vulnerability': {'id': 'NEW', 'severity': 'High'}})
    elif change == 'duplicate_location':
        matches[0]['artifact']['locations'].append(copy.deepcopy(matches[0]['artifact']['locations'][0]))
    else:
        matches[0]['vulnerability']['id'] = 'GHSA-UNREVIEWED'
    with pytest.raises(ValueError):
        verify(facts)


@pytest.mark.parametrize('severity', ['UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'])
def test_all_secrets_fail_without_emitting_match_values(facts, severity):
    facts[0]['Results'][0]['Secrets'] = [{'Severity': severity, 'Match': 'DO-NOT-EMIT'}]
    with pytest.raises(ValueError) as error:
        verify(facts)
    assert 'DO-NOT-EMIT' not in str(error.value)


@pytest.mark.parametrize('field,value', [('Secrets', False), ('Packages', {}),
                                       ('Vulnerabilities', None), ('SuppressedFindings', [{}])])
def test_malformed_and_filtered_scan_collections_fail(facts, field, value):
    facts[0]['Results'][0][field] = value
    with pytest.raises(ValueError):
        verify(facts)


@pytest.mark.parametrize('change', ['package', 'missing_package', 'native_image', 'native_layers',
                                   'sbom_types', 'native_config', 'native_version', 'grype_image', 'ignored'])
def test_full_native_identity_and_complete_inventory_are_required(facts, change):
    trivy, grype, sbom, inspection, _request = facts
    if change == 'package':
        trivy['Results'][2]['Packages'][0]['Version'] = '999'
    elif change == 'missing_package':
        trivy['Results'][2]['Packages'].pop()
    elif change == 'native_image':
        inspection['Id'] = 'sha256:' + '0' * 64
    elif change == 'native_layers':
        inspection['RootFS']['Layers'] = ['sha256:' + '0' * 64]
    elif change == 'sbom_types':
        sbom['artifacts'].pop()
    elif change == 'native_config':
        sbom['source']['metadata']['config'] = base64.b64encode(b'{}').decode()
    elif change == 'native_version':
        sbom['artifacts'][0]['version'] = '13.2.3'
    elif change == 'grype_image':
        grype['source']['target']['imageID'] = 'sha256:' + '0' * 64
    else:
        grype['ignoredMatches'] = [{}]
    with pytest.raises(ValueError):
        verify(facts)


@pytest.mark.parametrize('severity', ['LOW', 'MEDIUM'])
def test_medium_low_remain_visible_under_existing_policy(facts, severity):
    rows = facts[0]['Results'][2]['Vulnerabilities']
    rows.append({**rows[0], 'VulnerabilityID': 'LOWER-SEVERITY', 'Severity': severity})
    result = verify(facts)
    assert result['passed']
    assert any(row['id'] == 'LOWER-SEVERITY' for row in result['remaining_unexcluded_medium_low'])


@pytest.mark.parametrize('accepted', [None, False, True])
def test_prepare_never_authorizes_even_with_an_accepted_file(facts, accepted):
    auth = authorization(facts[4]) if accepted is not None else None
    if auth is not None:
        auth['accepted'] = accepted
    result = gate.final_result(verify(facts), facts[4], auth, prepare=True)
    assert result['passed'] is False and result['technical_ready'] is True
    assert result['approval_pending'] is True and result['old_authorization_inherited'] is False


@pytest.mark.parametrize('change', ['absent', 'pending', 'request', 'subject', 'rows18', 'missing',
                                   'duplicate', 'disposition', 'policy', 'expires'])
def test_only_new_complete_exact28_authorization_can_pass(facts, change):
    auth = authorization(facts[4])
    if change == 'absent':
        auth = None
    elif change == 'pending':
        auth['accepted'] = False
    elif change == 'request':
        auth['request_sha256'] = '0' * 64
    elif change == 'subject':
        auth['subject']['config_digest'] = 'sha256:' + '0' * 64
    elif change == 'rows18':
        auth['exact_target_advisory_rows'] = auth['exact_target_advisory_rows'][:18]
    elif change == 'missing':
        auth['exact_target_advisory_rows'].pop()
    elif change == 'duplicate':
        auth['exact_target_advisory_rows'][-1] = copy.deepcopy(auth['exact_target_advisory_rows'][0])
    elif change == 'disposition':
        auth['exact_target_advisory_rows'][0]['disposition'] = 'FIXED'
    elif change == 'policy':
        auth['policy']['keep_all_raw'] = False
    else:
        auth['policy']['expires'] = '2099-01-01T00:00:00+00:00'
    with pytest.raises(ValueError):
        gate.final_result(verify(facts), facts[4], auth)


def test_new_exact_subject_can_pass_after_separate_complete_authorization(facts):
    result = gate.final_result(verify(facts), facts[4], authorization(facts[4]))
    assert result['passed'] is True and result['approval_pending'] is False
    assert result['raw_reports_modified'] is False


def pinned_request(facts, tmp_path, monkeypatch):
    # Synthetic request tests isolate its existing factual contract. The real
    # policy/subject binding is exercised in test_security_review_policy_integration.
    monkeypatch.setattr(gate, 'validate_review_policy', lambda *_a, **_k: None)
    request = copy.deepcopy(facts[4])
    files = {'sources/local.mod': b'module fixture-local\n',
             'sources/main.mod': b'replace fixture-local => ./fixture-local\n',
             'reviews/one.json': b'{"facts":true}', 'reviews/two.json': b'{"facts":true}'}
    for directory in gate.COPIED_SOURCE_DIRS:
        files[directory + '/go.mod'] = b'module fixture-compile-input\n'
    request['binding']['recipe_files'] = {name: sha(raw) for name, raw in files.items()
                                        if any(name.startswith(d + '/') for d in gate.COPIED_SOURCE_DIRS)}
    for name, raw in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    request['files_sha256'] = {name: sha(raw) for name, raw in files.items()}
    request['independent_reviews'] = [{'path': name, 'sha256': request['files_sha256'][name]}
                                      for name in ('reviews/one.json', 'reviews/two.json')]
    path = tmp_path / gate.REQUEST_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(request), encoding='utf-8')
    monkeypatch.setattr(gate, 'REQUEST_SHA256', sha(path.read_bytes()))
    return request, path


@pytest.mark.parametrize('change', ['new_file', 'missing_file', 'different_set'])
def test_added_missing_or_changed_copied_source_inventory_fails(tmp_path, change):
    files = {}
    for directory in gate.COPIED_SOURCE_DIRS:
        name = directory + '/go.mod'
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'module fixture\n')
        files[name] = sha(path.read_bytes())
    binding = {'recipe_files': files}
    gate.validate_copied_source_inventory(tmp_path, binding)
    directory = gate.COPIED_SOURCE_DIRS[0]
    if change == 'new_file':
        (tmp_path / directory / 'new.go').write_bytes(b'package fixture\n')
    elif change == 'missing_file':
        files[directory + '/missing.go'] = '0' * 64
    else:
        files[directory + '/renamed.mod'] = files.pop(directory + '/go.mod')
    with pytest.raises(ValueError):
        gate.validate_copied_source_inventory(tmp_path, binding)


@pytest.mark.parametrize('directory_link', [False, True])
def test_copied_source_files_and_directories_cannot_be_links(tmp_path, directory_link):
    files = {}
    for directory in gate.COPIED_SOURCE_DIRS:
        name = directory + '/go.mod'
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'module fixture\n')
        files[name] = sha(path.read_bytes())
    outside = tmp_path / 'outside-copy'
    outside.mkdir()
    source = outside if directory_link else outside / 'source.go'
    if not directory_link:
        source.write_bytes(b'package fixture\n')
    link = tmp_path / gate.COPIED_SOURCE_DIRS[0] / 'linked'
    try:
        link.symlink_to(source, target_is_directory=directory_link)
    except OSError as error:
        pytest.skip('Host does not permit test symlinks: ' + type(error).__name__)
    with pytest.raises(ValueError):
        gate.validate_copied_source_inventory(tmp_path, {'recipe_files': files})


def test_request_and_every_new_review_source_file_are_pinned(facts, tmp_path, monkeypatch):
    request, _path = pinned_request(facts, tmp_path, monkeypatch)
    assert gate.load_request(tmp_path) == request
    (tmp_path / 'reviews/one.json').write_bytes(b'{}')
    with pytest.raises(ValueError):
        gate.load_request(tmp_path)


@pytest.mark.parametrize('change', ['unset_pin', 'request_bytes', 'technical_not_ready',
                                   'old_review', 'missing_review', 'old18'])
def test_request_cannot_inherit_old_scope_or_skip_review(facts, tmp_path, monkeypatch, change):
    request, path = pinned_request(facts, tmp_path, monkeypatch)
    if change == 'unset_pin':
        monkeypatch.setattr(gate, 'REQUEST_SHA256', '')
    elif change == 'request_bytes':
        path.write_bytes(path.read_bytes() + b' ')
    else:
        if change == 'technical_not_ready':
            request['technical_ready'] = False
        elif change == 'old_review':
            request['independent_reviews'][0]['path'] = (
                'deployment/security/grafana-fixed-evidence/independent-review.json')
        elif change == 'missing_review':
            request['independent_reviews'].pop()
        else:
            request['binding']['allowed_findings'] = request['binding']['allowed_findings'][:18]
        path.write_text(json.dumps(request), encoding='utf-8')
        monkeypatch.setattr(gate, 'REQUEST_SHA256', sha(path.read_bytes()))
    with pytest.raises(ValueError):
        gate.load_request(tmp_path)


@pytest.mark.parametrize('raw', [b'{"accepted":false,"accepted":true}', b'{"x":NaN}'])
def test_duplicate_or_nonfinite_json_is_not_authorization(raw):
    with pytest.raises(ValueError):
        gate.strict_json(raw)


def test_cli_preparation_does_integrity_before_any_authorization(facts, tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    monkeypatch.setattr(gate, 'load_request', lambda *_a: facts[4])
    paths = {name: tmp_path / (name + '.json') for name in ('archive', 'scan', 'grype-scan', 'sbom', 'inspect')}
    for name, value in zip(('scan', 'grype-scan', 'sbom', 'inspect'), facts[:4], strict=True):
        paths[name].write_text(json.dumps(value), encoding='utf-8')
    paths['archive'].write_bytes(b'INTEGRITY-ONLY-NOT-AN-APPROVAL')
    calls = []

    def integrity(path, binding, **kwargs):
        calls.append((path, len(binding['binary_sha256']), kwargs))
        return {'authorization_evaluated': False}

    monkeypatch.setattr(gate, 'verify_oci_archive', integrity)
    output = tmp_path / '.cache/eval/prepared.json'
    args = [item for name, path in paths.items() for item in ('--' + name, str(path))]
    assert gate.main(args + ['--scanner-exit-code', '0', '--grype-exit-code', '0',
                            '--prepare', '--output', str(output)]) == 1
    result = json.loads(output.read_bytes())
    assert calls and calls[0][1] == 14
    assert result['passed'] is False and result['technical_ready'] is result['approval_pending'] is True
    assert result['archive']['authorization_evaluated'] is False
    assert result['raw_scan_sha256'] == sha(paths['scan'].read_bytes())


@pytest.mark.parametrize('alias', ['scan', 'existing_link', 'outside_cache'])
def test_cli_outputs_cannot_overwrite_raw_evidence(tmp_path, monkeypatch, alias):
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    cache = tmp_path / '.cache/eval'
    cache.mkdir(parents=True)
    paths = {name: cache / (name + '.json') for name in ('archive', 'scan', 'grype-scan', 'sbom', 'inspect')}
    for path in paths.values():
        path.write_bytes(b'ORIGINAL')
    output = cache / 'result.json'
    if alias == 'scan':
        output = paths['scan']
    elif alias == 'existing_link':
        output.hardlink_to(paths['scan'])
    else:
        output = tmp_path / 'result.json'
    args = [item for name, path in paths.items() for item in ('--' + name, str(path))]
    assert gate.main(args + ['--scanner-exit-code', '0', '--grype-exit-code', '0',
                            '--output', str(output)]) == 1
    assert all(path.read_bytes() == b'ORIGINAL' for path in paths.values())
