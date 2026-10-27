"""Direct source2 technical-route cases; synthetic facts, no scanner or Docker."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json

import pytest
from scripts import check_fs_source_applicability as gate

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
COMMIT = 'a' * 40


def hashed(value):
    return hashlib.sha256(gate.canonical(value)).hexdigest()


def put(root, path, value):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(gate.canonical(value))
    return gate.file_hash(file)


def repin(fixture, monkeypatch):
    root, scope, binding, review, scan = fixture
    digest = put(root, scope['technical_review_path'], review)
    scope['technical_review_sha256'] = digest
    put(root, gate.POLICY, {'schema': 'source2-independent-technical-applicability-v1', 'scope': scope})
    monkeypatch.setattr(gate, 'TECHNICAL_REVIEW_SHA256', digest)
    monkeypatch.setattr(gate, 'EXPECTED_SCOPE_SHA256', hashed(scope))


@pytest.fixture
def technical_fixture(tmp_path, monkeypatch):
    original = json.loads((gate.ROOT / gate.POLICY).read_bytes())
    scope = deepcopy(original['scope'])
    review = json.loads((gate.ROOT / scope['technical_review_path']).read_bytes())
    source = tmp_path / scope['source_path']
    source.parent.mkdir(parents=True)
    source.write_text('module fixture\nrequire ' + scope['module'] + ' ' + scope['version'] + '\n')
    scope['source_sha256'] = gate.file_hash(source)
    for row in scope['rows']:
        row['source_sha256'] = scope['source_sha256']
    scope['source_inventory_count'] = 1
    scope['source_inventory_sha256'] = hashed([(scope['module'], scope['version'])])
    scope['required_scan_targets'] = [[scope['source_path'], 'lang-pkgs', 'gomod']]
    graph_raw = gate.canonical({'ImportPath': 'fixture/main', 'Module': {'Path': 'fixture'}})
    graph = tmp_path / 'evidence/graph.gz'
    graph.parent.mkdir()
    graph.write_bytes(gzip.compress(graph_raw))
    scope['main_graph'] = {'target': scope['main_target'], 'compressed_evidence': 'evidence/graph.gz',
        'raw_sha256': hashlib.sha256(graph_raw).hexdigest(), 'raw_bytes': len(graph_raw),
        'packages': 1, 'format': 'go-list-json-stream'}
    scope['main_image_scan'] = 'evidence/main-image.json'
    scope['main_image_packages'] = 1
    image = {'Metadata': {'ImageID': scope['image_index']},
             'Results': [{'Target': scope['main_target'], 'Packages': [{'Name': 'fixture', 'Version': '1'}]}]}
    image_sha = put(tmp_path, scope['main_image_scan'], image)
    binding = {'expires': scope['expires'], 'index_digest': scope['image_index'],
        'binary_sha256': {scope['main_target']: scope['main_elf_sha256']},
        'recipe_files': {scope['source_path']: scope['source_sha256']},
        'evidence_files': {'evidence/graph.gz': gate.file_hash(graph), scope['main_image_scan']: image_sha},
        'compiled_graphs': [scope['main_graph']]}
    review.update(scope_rows=scope['rows'], graph=scope['main_graph'],
                  main_ELF_package_inventory_count=1, actual_bound_main_image_inventory_sha256=image_sha)
    scan = {'SchemaVersion': 2, 'ArtifactType': 'filesystem', 'Trivy': {'Version': '0.75.0'},
        'ReportID': 'synthetic-source2-report', 'CreatedAt': NOW.isoformat(), 'Results': [
        {'Target': scope['source_path'], 'Class': 'lang-pkgs', 'Type': 'gomod',
         'Packages': [{'Name': scope['module'], 'Version': scope['version']}],
         'Vulnerabilities': [{'VulnerabilityID': r['id'], 'PkgName': r['package'],
             'InstalledVersion': r['version'], 'Severity': r['severity']} for r in scope['rows']]}]}
    fixture = tmp_path, scope, binding, review, scan
    monkeypatch.setattr(gate.gfx, 'load_binding', lambda *args, **kwargs: binding)
    repin(fixture, monkeypatch)
    return fixture


def completion(scan):
    return {'schema': 'fs-scan-completion-v1', 'completed': True, 'operational_success': True,
        'timed_out': False, 'scanner_version': '0.75.0', 'raw_sha256': hashed(scan),
        'raw_report_id': scan['ReportID'], 'source_commit': COMMIT, 'scanners': ['vuln', 'secret'],
        'severities': list(gate.SEVERITIES), 'configured_finding_exit_code': 0, 'process_exit_code': 0,
        'started_at': NOW.isoformat(), 'finished_at': NOW.isoformat(), 'report_created_at': scan['CreatedAt'],
        'filters': {'ignorefile_used': True, 'ignorefile_rules_empty': True, 'ignorefile_sha256': gate.EMPTY_SHA256,
            'ignorefile_mode': 'verified_empty_neutral', 'include_dev_dependencies': True,
            'skip_files': [], 'skip_dirs': [], 'ignore_unfixed': False, 'vex_documents': []},
        'result_targets_sha256': hashed(sorted([[r['Target'], r.get('Class', ''), r.get('Type', '')]
                                               for r in scan['Results']]))}


def assess(fixture, *, receipt=None, now=NOW):
    root, _, _, _, scan = fixture
    return gate.evaluate(scan, receipt if receipt is not None else completion(scan), raw_sha256=hashed(scan),
                         source_commit=COMMIT, root=root, now=now)


def test_exact_two_raw_high_rows_are_technical_only_and_unchanged(technical_fixture):
    before = hashed(technical_fixture[-1])
    result = assess(technical_fixture)
    assert result['passed'] and result['technical_not_applicable_count'] == 2
    assert result['raw_counts']['HIGH'] == 2 and hashed(technical_fixture[-1]) == before
    assert all(row['technical_disposition'] == gate.TECHNICAL_DISPOSITION for row in result['technical_not_applicable'])
    assert not result['runtime_dispositions_inherited'] and not result['new_human_risk_acceptance']


def test_medium_low_unknown_remain_visible_without_new_applicability_labels(technical_fixture):
    scan = technical_fixture[-1]
    scan['Results'][0]['Vulnerabilities'] += [
        {'VulnerabilityID': 'RETAIN-' + s, 'PkgName': 'other', 'InstalledVersion': '1', 'Severity': s}
        for s in ['MEDIUM', 'LOW', 'UNKNOWN']]
    scan['Results'].append({'Target': 'sample.txt', 'Class': 'secret', 'Secrets': [
        {'RuleID': 'RETAIN-LOW-SECRET', 'Severity': 'LOW', 'Match': 'NEVER_PRINT_THIS_VALUE'}]})
    result = assess(technical_fixture)
    assert result['passed'] and result['retained_nonblocking_count'] == 4
    assert result['raw_counts']['UNKNOWN'] == result['raw_secret_counts']['LOW'] == 1
    assert all('technical_disposition' not in row for row in result['retained_nonblocking'])
    assert 'NEVER_PRINT_THIS_VALUE' not in json.dumps(result)


@pytest.mark.parametrize('case', ['other_HIGH', 'other_CRITICAL', 'secret_HIGH'])
def test_other_high_critical_or_secret_high_still_fails(technical_fixture, case):
    item = technical_fixture[-1]['Results'][0]
    if case == 'secret_HIGH':
        item['Secrets'] = [{'RuleID': 'CVE-2026-21728', 'Severity': 'HIGH', 'Match': 'NEVER_PRINT'}]
    else:
        item['Vulnerabilities'].append({'VulnerabilityID': 'NEW-HIGH', 'PkgName': 'other',
            'InstalledVersion': '1', 'Severity': case.removeprefix('other_')})
    result = assess(technical_fixture)
    assert not result['passed'] and result['unresolved_count'] == 1


@pytest.mark.parametrize('field,value', [('Target', 'different/go.mod'), ('InstalledVersion', 'v9.0.0'),
                                        ('Severity', 'CRITICAL')])
def test_exact_tuple_cannot_expand(technical_fixture, field, value):
    if field == 'Target':
        technical_fixture[-1]['Results'][0][field] = value
    else:
        technical_fixture[-1]['Results'][0]['Vulnerabilities'][0][field] = value
    assert not assess(technical_fixture)['passed']


@pytest.mark.parametrize('case', ['missing', 'duplicate'])
def test_missing_or_duplicate_exact_alert_cannot_claim_a_pass(technical_fixture, case):
    rows = technical_fixture[-1]['Results'][0]['Vulnerabilities']
    rows.pop() if case == 'missing' else rows.append(deepcopy(rows[0]))
    assert not assess(technical_fixture)['passed']


def test_operational_failure_cannot_be_dispositioned(technical_fixture):
    receipt = completion(technical_fixture[-1])
    receipt['process_exit_code'] = 1
    assert not assess(technical_fixture, receipt=receipt)['passed']


def test_review_raw_byte_pin_is_required(technical_fixture):
    root, scope, *_ = technical_fixture
    path = root / scope['technical_review_path']
    path.write_bytes(path.read_bytes() + b'\n')
    assert not assess(technical_fixture)['passed']


def test_even_repinned_human_role_is_not_the_independent_technical_review(technical_fixture, monkeypatch):
    technical_fixture[3]['actor_kind'] = 'human'
    repin(technical_fixture, monkeypatch)
    assert not assess(technical_fixture)['passed']


@pytest.mark.parametrize('case', ['source', 'image', 'ELF'])
def test_source_and_actual_bound_image_identity_drift_stays_closed(technical_fixture, case):
    root, scope, binding, *_ = technical_fixture
    if case == 'source':
        (root / scope['source_path']).write_text('changed source')
    elif case == 'image':
        binding['index_digest'] = 'sha256:' + '0' * 64
    else:
        binding['binary_sha256'][scope['main_target']] = '0' * 64
    assert not assess(technical_fixture)['passed']


def test_review_expiry_is_not_extended(technical_fixture):
    assert not assess(technical_fixture, now=datetime(2026, 11, 26, tzinfo=timezone.utc))['passed']


def test_real_review_is_exact_independent_source2_metadata_only():
    policy = json.loads((gate.ROOT / gate.POLICY).read_bytes())
    scope = policy['scope']
    review_file = gate.ROOT / scope['technical_review_path']
    review = json.loads(review_file.read_bytes())
    assert gate.file_hash(review_file) == scope['technical_review_sha256'] == gate.TECHNICAL_REVIEW_SHA256
    assert hashed(scope) == gate.EXPECTED_SCOPE_SHA256 and len(scope['rows']) == 2
    assert review['actor_kind'] == 'independent_root_coordinator' and review['facts_passed'] is True
    assert review['new_human_CVE_risk_acceptance'] is False and review['runtime18_verdicts_inherited'] is False
