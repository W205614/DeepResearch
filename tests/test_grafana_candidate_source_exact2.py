"""Exact new-source facts; synthetic counterexamples, no Docker/scanner/models."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib

import pytest
from scripts import check_grafana_candidate_source_exact2 as gate


def sha(value):
    return hashlib.sha256(gate.old.canonical(value)).hexdigest()


@pytest.fixture
def fixture():
    def finding(advisory, target=gate.SOURCE, package=gate.MODULE, version=gate.VERSION, severity='HIGH', kind='vulnerability'):
        return {'kind': kind, 'id': advisory, 'target': target, 'package': package, 'version': version, 'severity': severity}
    inventory = [[gate.MODULE, gate.VERSION]]
    rows = [finding(advisory) for advisory in sorted(gate.IDS)]
    jose = finding('CVE-2026-116587', 'deployment/vendors/grafana/main/go.mod', 'github.com/go-jose/go-jose/v4', 'v4.1.4')
    original = {'passed': False, 'mode': 'failed_closed', 'scan_completion_verified': True,
        'technical_not_applicable_count': 2, 'technical_not_applicable': [finding('OLD-1'), finding('OLD-2')],
        'unresolved': rows + [jose], 'unresolved_count': 3, 'raw_counts': {'HIGH': 5, 'UNKNOWN': 99},
        'raw_secret_counts': {'HIGH': 0}, 'retained_nonblocking': [{'id': 'RETAIN-UNKNOWN'}]}
    scan = {'Results': [{'Target': gate.SOURCE, 'Class': 'lang-pkgs', 'Type': 'gomod',
                        'Packages': [{'Name': gate.MODULE, 'Version': gate.VERSION}]}]}
    review = {'source': {'inventory_count': 1, 'inventory_sha256': sha(inventory)}}
    return original, scan, review


def test_only_new_two_are_applied_and_old_jose_still_blocks(fixture):
    original, scan, review = fixture
    before = deepcopy(fixture)
    result = gate.apply_exact_two(original, scan, review)
    assert not result['passed'] and result['unresolved_count'] == 1
    assert result['unresolved'][0]['id'] == 'CVE-2026-116587'
    assert result['technical_not_applicable_count'] == 4
    assert result['candidate_source2_technical_not_applicable_count'] == 2
    assert result['raw_counts'] == original['raw_counts'] and result['retained_nonblocking'] == original['retained_nonblocking']
    assert not result['artifact_approved'] and not result['old_human_authorization_inherited']
    assert fixture == before


@pytest.mark.parametrize('kind,severity', [('vulnerability', 'HIGH'), ('vulnerability', 'CRITICAL'), ('secret', 'HIGH')])
def test_any_additional_blocking_finding_remains(fixture, kind, severity):
    original, scan, review = fixture
    original['unresolved'].append({'kind': kind, 'id': 'EXTRA', 'target': gate.SOURCE,
        'package': gate.MODULE, 'version': gate.VERSION, 'severity': severity})
    result = gate.apply_exact_two(original, scan, review)
    assert not result['passed'] and result['unresolved_count'] == 2
    assert any(f['id'] == 'EXTRA' for f in result['unresolved'])


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'target', 'version', 'package', 'severity', 'secret'])
def test_no_missing_duplicate_or_expanded_tuple_can_pass(fixture, mutation):
    original, scan, review = fixture
    if mutation == 'missing':
        original['unresolved'].pop(0)
    elif mutation == 'duplicate':
        original['unresolved'].append(deepcopy(original['unresolved'][0]))
    elif mutation == 'secret':
        original['unresolved'][0]['kind'] = 'secret'
    else:
        original['unresolved'][0][mutation] = 'different'
    with pytest.raises(gate.old.DispositionError, match='exact2 finding'):
        gate.apply_exact_two(original, scan, review)


@pytest.mark.parametrize('mutation', ['empty', 'duplicate', 'changed', 'missing_target'])
def test_new_source_inventory_and_target_must_be_complete(fixture, mutation):
    original, scan, review = fixture
    row = scan['Results'][0]
    if mutation == 'empty':
        row['Packages'] = []
    elif mutation == 'duplicate':
        row['Packages'] *= 2
    elif mutation == 'changed':
        row['Packages'][0]['Version'] = 'v9'
    else:
        row['Target'] = 'other/go.mod'
    with pytest.raises(gate.old.DispositionError):
        gate.apply_exact_two(original, scan, review)


@pytest.mark.parametrize('mutation', ['completion', 'old2_count', 'old_error'])
def test_original_technical_route_must_remain_valid(fixture, mutation):
    original, scan, review = fixture
    if mutation == 'completion':
        original['scan_completion_verified'] = False
    elif mutation == 'old2_count':
        original['technical_not_applicable_count'] = 1
    else:
        original['error'] = 'OLD EVIDENCE FAILURE'
    with pytest.raises(gate.old.DispositionError, match='Old exact2'):
        gate.apply_exact_two(original, scan, review)


def test_saved_original_verdict_cannot_be_replaced_by_a_claim(fixture, monkeypatch):
    original, scan, review = fixture
    monkeypatch.setattr(gate.old, 'evaluate', lambda *args, **kwargs: original)
    monkeypatch.setattr(gate, 'load_review', lambda *args: review)
    fake = deepcopy(original)
    fake['scan_completion_verified'] = False
    result = gate.evaluate(scan, {}, fake, raw_sha256='a' * 64, source_commit='b' * 40)
    assert not result['passed'] and 'independently recomputed' in result['error']


def test_existing_output_is_never_overwritten(tmp_path, monkeypatch):
    output = tmp_path / 'old-result.json'
    output.write_bytes(b'ORIGINAL RAW OR VERDICT')
    monkeypatch.setattr(gate.sys, 'argv', ['checker', '--scan', 'missing', '--scan-receipt', 'missing',
        '--original-verdict', 'missing', '--output', str(output)])
    with pytest.raises(SystemExit) as error:
        gate.main()
    assert error.value.code == 1 and output.read_bytes() == b'ORIGINAL RAW OR VERDICT'


def test_real_pinned_candidate_graph_and_independently_extracted_native_are_consistent():
    review = gate.load_review(gate.ROOT, datetime(2026, 10, 28, tzinfo=timezone.utc))
    assert review['graph']['verified_facts']['packages'] == 4200
    assert review['graph']['verified_facts']['modules'] == 522
    assert review['source']['path'] == gate.SOURCE and not review['artifact_approved']


def test_json_duplicate_fields_are_not_allowed():
    with pytest.raises(gate.old.DispositionError):
        gate.old.json_loads('{"human_risk_acceptance":true,"human_risk_acceptance":false}')


def test_review_expiry_cannot_be_inherited_indefinitely():
    with pytest.raises(gate.old.DispositionError, match='expired'):
        gate.load_review(gate.ROOT, datetime(2027, 1, 1, tzinfo=timezone.utc))
