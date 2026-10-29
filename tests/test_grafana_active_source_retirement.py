"""Conditional selector and source-retirement counterexamples; no services/models."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path

import pytest
from scripts import check_grafana_active_source_retirement as gate
from scripts import runtime_ci_inventory as inventory


CI = '''jobs:
  enterprise-compose-smoke:
    steps:
      - name: Load approved Grafana
        run: python scripts/fetch_grafana_candidate.py
      - run: python scripts/smoke-enterprise.py --start
'''
SECURITY = '''jobs:
  dependency-and-image-scan:
    steps:
      - run: python scripts/runtime_ci_inventory.py
      - name: Original
        continue-on-error: true
        run: >-
          python scripts/check_fs_source_applicability.py
          --scan .cache/eval/source-fs/raw.json --scan-receipt .cache/eval/source-fs/completion.json
          --output .cache/eval/source-fs-original-verdict.json
      - name: Candidate
        continue-on-error: true
        run: >-
          python scripts/check_grafana_candidate_source_exact2.py
          --scan .cache/eval/source-fs/raw.json --scan-receipt .cache/eval/source-fs/completion.json
          --original-verdict .cache/eval/source-fs-original-verdict.json
          --output .cache/eval/source-fs-candidate-verdict.json
      - name: Final
        if: always() && steps.source_scan.outcome == 'success'
        run: >-
          python scripts/check_grafana_active_source_retirement.py
          --scan .cache/eval/source-fs/raw.json --scan-receipt .cache/eval/source-fs/completion.json
          --original-verdict .cache/eval/source-fs-original-verdict.json
          --candidate-verdict .cache/eval/source-fs-candidate-verdict.json
          --runtime-inventory .cache/eval/runtime-image-inventory.json
          --output .cache/eval/source-fs-verdict.json
  runtime-image-scan:
    steps:
      - name: Load MinIO
        if: matrix.artifact == 'minio'
        run: python scripts/fetch_approved_image.py --artifact "$ARTIFACT"
      - name: Load Grafana
        if: matrix.artifact == 'grafana'
        run: python scripts/fetch_grafana_candidate.py --tag deepresearch-scan-grafana:ci --output .cache/eval/approved-grafana-load.json
      - name: Collect actual Grafana native evidence
        if: matrix.artifact == 'grafana' && steps.approved_scan.outcome == 'success'
        run: python scripts/scan_grafana_candidate.py --image deepresearch-scan-grafana:ci --output-dir .cache/eval/grafana-native
      - name: Enforce Grafana
        if: matrix.artifact == 'grafana' && steps.approved_scan.outcome == 'success'
        run: >-
          python scripts/check_grafana_candidate_exact28.py
          --archive .cache/eval/approved-grafana.oci.tar --scan runtime-image.json
          --grype-scan .cache/eval/grafana-native/full-grype.json --sbom .cache/eval/grafana-native/full-syft.json
          --inspect .cache/eval/approved-grafana-load-inspect.json
          --scanner-exit-code 0 --grype-exit-code 0 --output .cache/eval/grafana-ci-disposition.json
'''


def put(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def selector_fixture(tmp_path, monkeypatch):
    for name, text in [('compose.yaml', 'services: {grafana: {image: deepresearch-approved-grafana:20261028}}'),
                       ('.github/workflows/ci.yml', CI), ('.github/workflows/security.yml', SECURITY)]:
        put(tmp_path, name, text)
    selected = {'image': inventory.APPROVED_GRAFANA, 'request_sha256': 'a' * 64,
                'authorization_sha256': 'b' * 64, 'authorization_verified': True,
                'subject': {'archive_sha256': 'c' * 64}}
    observed = {'schema': 'runtime-image-inventory-v2', 'source_commit': 'd' * 40, 'github_sha': 'd' * 40,
        'grafana_selection': selected, 'vendor_matrix': {'include': [
            {'image': inventory.APPROVED_GRAFANA, 'services': 'grafana', 'artifact': 'grafana', 'approved_artifact': True}]},
        'custom_matrix': {'include': []}}
    review = {'subject': selected['subject'], 'request_sha256': selected['request_sha256'],
        'authorization_sha256': selected['authorization_sha256'],
        'selector_files_sha256': {name: gate.approved.file_hash(tmp_path / name) for name in gate.SELECTOR_FILES},
        'workflow_facts': gate.workflow_facts(tmp_path)}
    monkeypatch.setattr(inventory, 'build_record', lambda root: deepcopy(observed))
    return tmp_path, observed, review


def test_concrete_new_loaders_and_both_gates_match_current_inventory(selector_fixture):
    root, observed, review = selector_fixture
    assert gate.verify_selectors(root, deepcopy(observed), review, 'd' * 40) == observed


@pytest.mark.parametrize('mutation', ['stale', 'old_tag', 'other_subject', 'missing', 'duplicate', 'build', 'wrong_commit', 'unapproved', 'other_grafana'])
def test_approval_alone_cannot_hide_a_wrong_current_selector(selector_fixture, mutation):
    root, observed, review = selector_fixture
    recorded = deepcopy(observed)
    if mutation == 'stale':
        recorded['source_commit'] = 'e' * 40
    elif mutation == 'old_tag':
        observed['grafana_selection']['image'] = 'deepresearch-approved-grafana:20261026'
    elif mutation == 'other_subject':
        observed['grafana_selection']['subject'] = {'archive_sha256': 'f' * 64}
    elif mutation == 'missing':
        observed['vendor_matrix']['include'] = []
    elif mutation == 'duplicate':
        observed['vendor_matrix']['include'] *= 2
    elif mutation == 'build':
        observed['custom_matrix']['include'] = [{'services': 'grafana'}]
    elif mutation == 'wrong_commit':
        observed['source_commit'] = 'e' * 40
    elif mutation == 'unapproved':
        observed['grafana_selection']['authorization_verified'] = False
    else:
        observed['vendor_matrix']['include'].append({'artifact': '', 'services': 'other', 'image': 'grafana/grafana:13.2.4'})
    if mutation != 'stale':
        recorded = deepcopy(observed)
    with pytest.raises(ValueError):
        gate.verify_selectors(root, recorded, review, 'd' * 40)


@pytest.mark.parametrize('old,new', [
    ('scripts/fetch_grafana_candidate.py', 'scripts/fetch_approved_image.py'),
    ("if: matrix.artifact == 'minio'", 'if: matrix.approved_artifact == true'),
    ('scripts/check_grafana_candidate_exact28.py', 'scripts/grafana_image_disposition.py'),
    ('scripts/scan_grafana_candidate.py', 'scripts/scan_grafana_image.py'),
    ('--output .cache/eval/source-fs-candidate-verdict.json', '--output .cache/eval/source-fs-original-verdict.json'),
    ("if: always() && steps.source_scan.outcome == 'success'", 'continue-on-error: true'),
])
def test_concrete_workflow_bypass_or_old_references_fail(selector_fixture, old, new):
    root, _, _ = selector_fixture
    file = root / '.github/workflows/security.yml'
    file.write_text(file.read_text().replace(old, new))
    with pytest.raises(ValueError):
        gate.workflow_facts(root)


def test_reviewed_workflow_bytes_cannot_change_even_with_matching_inventory(selector_fixture):
    root, observed, review = selector_fixture
    (root / 'compose.yaml').write_text('services: {}')
    with pytest.raises(ValueError, match='selector bytes'):
        gate.verify_selectors(root, observed, review, 'd' * 40)


@pytest.fixture
def source_fixture():
    jose = {'kind': 'vulnerability', 'id': gate.SCOPE[0], 'target': gate.OLD_SOURCE,
            'package': gate.PACKAGE, 'version': 'v4.1.4', 'severity': 'HIGH'}
    previous = {'scan_completion_verified': True, 'technical_not_applicable_count': 4,
        'candidate_source2_technical_not_applicable_count': 2, 'unresolved': [jose], 'unresolved_count': 1,
        'technical_not_applicable': [{'id': 'OLD-1'}, {'id': 'OLD-2'}, {'id': 'NEW-1'}, {'id': 'NEW-2'}],
        'raw_counts': {'HIGH': 5, 'UNKNOWN': 17}, 'retained_nonblocking': [{'id': 'GO-UNKNOWN-UNMODIFIED'}]}
    rows = [{'VulnerabilityID': 'OTHER-' + str(i), 'PkgName': 'other', 'InstalledVersion': '1', 'Severity': 'HIGH'}
            for i in range(4)] + [{'VulnerabilityID': jose['id'], 'PkgName': jose['package'],
                                 'InstalledVersion': jose['version'], 'Severity': jose['severity']}]
    scan = {'Results': [{'Target': gate.OLD_SOURCE, 'Vulnerabilities': rows}]}
    return previous, scan


def test_one_shadowed_constraint_retires_and_all_raw_and_previous_results_remain(source_fixture):
    previous, scan = source_fixture
    before = deepcopy(source_fixture)
    result = gate.apply_retirement(previous, scan)
    assert result['passed'] and result['technical_not_applicable_count'] == 5
    assert result['retired_old_constraint_count'] == 1 and result['unresolved_count'] == 0
    assert result['raw_counts'] == previous['raw_counts'] and result['retained_nonblocking'] == previous['retained_nonblocking']
    assert not result['new_human_risk_acceptance'] and not result['running_container_replacement_claimed']
    assert source_fixture == before


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'new_HIGH', 'wrong_target', 'wrong_version', 'wrong_severity', 'secret_LOW', 'secret_HIGH'])
def test_no_additional_missing_duplicate_changed_or_secret_finding_can_retire(source_fixture, mutation):
    previous, scan = source_fixture
    if mutation == 'missing':
        previous['unresolved'] = []
    elif mutation == 'duplicate':
        previous['unresolved'] *= 2
    elif mutation == 'new_HIGH':
        scan['Results'][0]['Vulnerabilities'].append(deepcopy(scan['Results'][0]['Vulnerabilities'][0]))
    elif mutation.startswith('secret_'):
        scan['Results'][0]['Secrets'] = [{'RuleID': 'SYNTHETIC', 'Severity': mutation.split('_')[1], 'Match': 'NEVER_EMIT'}]
    else:
        previous['unresolved'][0][mutation.removeprefix('wrong_')] = 'changed'
    with pytest.raises(ValueError):
        gate.apply_retirement(previous, scan)


def test_preserved_candidate_verdict_must_equal_fresh_two_stage_computation(source_fixture, monkeypatch):
    previous, scan = source_fixture
    monkeypatch.setattr(gate.source, 'evaluate', lambda *args, **kwargs: previous)
    candidate = {**deepcopy(previous), 'preserved_original_verdict_file_sha256': 'a' * 64}
    candidate['raw_counts'] = {'HIGH': 4}
    result = gate.evaluate(scan, {}, {}, candidate, {}, original_file_sha256='a' * 64,
                           raw_sha256='b' * 64, source_commit='c' * 40)
    assert not result['passed'] and 'independent recomputation' in result['error']


def test_inventory_reads_new_authorization_not_the_old_grafana_approval(monkeypatch):
    monkeypatch.setattr(inventory.candidate, 'load_request', lambda root: {'binding': {'approved_tag': inventory.APPROVED_GRAFANA}})
    monkeypatch.setattr(inventory.candidate, 'repository_file', lambda root, name: Path('new-authorization'))
    monkeypatch.setattr(inventory.candidate, 'read_json', lambda path: {'accepted': False})
    with pytest.raises(ValueError, match='pending'):
        inventory.load_grafana_binding(Path('fixture'))


def test_output_never_overwrites_any_prior_file(tmp_path, monkeypatch):
    output = tmp_path / 'original.json'
    output.write_bytes(b'IMMUTABLE-RAW-OR-PRIOR-VERDICT')
    monkeypatch.setattr(gate.sys, 'argv', ['gate', '--scan', 'missing', '--scan-receipt', 'missing',
        '--original-verdict', 'missing', '--candidate-verdict', 'missing', '--runtime-inventory', 'missing', '--output', str(output)])
    with pytest.raises(SystemExit) as result:
        gate.main()
    assert result.value.code == 1 and output.read_bytes() == b'IMMUTABLE-RAW-OR-PRIOR-VERDICT'


def test_missing_change_driven_policy_fails_before_selector_use(monkeypatch):
    record = {'schema': 'grafana-active-source-retirement-technical-review-v1',
        'actor_kind': 'automated_independent_agent', 'review_kind': 'conditional_source_retirement',
        'human_risk_acceptance': False, 'facts_passed': True, 'enabled': True, 'expires': '2026-11-25T23:59:59+08:00'}
    monkeypatch.setattr(gate.approved, 'repository_file', lambda root, name: Path('fixture'))
    monkeypatch.setattr(gate.approved, 'read_json', lambda path: record)
    monkeypatch.setattr(gate.approved, 'file_hash', lambda path: gate.REVIEW_SHA256)
    with pytest.raises(ValueError):
        gate.load_review(Path('fixture'), datetime(2027, 1, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize('name', ['.github/workflows/new-runtime.yml', 'deployment/compose/new-runtime.yaml'])
def test_new_selector_file_cannot_escape_the_current_review(selector_fixture, name):
    root, observed, review = selector_fixture
    put(root, name, 'new selector requiring review')
    with pytest.raises(ValueError, match='selector path set'):
        gate.verify_selectors(root, observed, review, 'd' * 40)
