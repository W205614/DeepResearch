"""Boundaries for the four newly reviewed HTML findings, without raw suppression."""
import copy
import json

import pytest

from scripts import check_minio_html_exact4 as gate


@pytest.fixture
def facts():
    binding = json.loads((gate.ROOT / gate.original.POLICY).read_bytes())['binding']
    openpgp = gate.exact2.load_technical_review(binding)
    html = {'targets': [{'target': target, 'advisory': advisory, 'package': gate.PACKAGE,
        'version': gate.VERSION, 'raw_severity': 'UNKNOWN', 'elf_sha256': binding['binary_sha256'][target]}
        for target in sorted(gate.exact2.TARGETS) for advisory in sorted(gate.ADVISORIES)]}
    report = {'Results': []}
    for target in sorted(gate.exact2.TARGETS):
        report['Results'].append({'Target': target, 'Vulnerabilities': [
            {'VulnerabilityID': gate.exact2.ADVISORY, 'PkgName': gate.exact2.PACKAGE,
             'InstalledVersion': gate.exact2.VERSION, 'Severity': 'UNKNOWN'},
            *[{'VulnerabilityID': advisory, 'PkgName': gate.PACKAGE,
               'InstalledVersion': gate.VERSION, 'Severity': 'UNKNOWN'} for advisory in sorted(gate.ADVISORIES)]]})
    base = {'passed': False, 'raw_counts': {'HIGH': 1, 'CRITICAL': 0}, 'verified_fixed_count': 1,
        'verified_fixed': [{'vulnerability': binding['vulnerability'],
                           'target': binding['finding']['target'], 'disposition': 'FIXED'}],
        'unresolved_count': 6, 'unresolved': [{'reason': 'unknown_severity', 'finding': item['VulnerabilityID']}
            for result in report['Results'] for item in result['Vulnerabilities']]}
    return base, report, binding, openpgp, html


def test_exact_six_keep_the_original_failure_and_raw_bytes(facts):
    before = copy.deepcopy(facts)
    result = gate.apply_review(*facts)
    assert result['passed'] and len(result['technical_applied']) == 4
    assert result['original_gate'] is facts[0] and result['original_gate']['passed'] is False
    assert result['raw_report_modified'] is False and facts == before


@pytest.mark.parametrize('change', ['target', 'advisory', 'package', 'version', 'severity',
                                   'missing', 'duplicate', 'another_unknown'])
def test_new_changed_duplicate_or_missing_unknowns_fail_closed(facts, change):
    base, report, binding, openpgp, html = facts
    record = report['Results'][0]
    item = record['Vulnerabilities'][1]
    if change == 'target':
        record['Target'] = 'usr/bin/unreviewed'
    elif change == 'missing':
        record['Vulnerabilities'].remove(item)
    elif change == 'duplicate':
        record['Vulnerabilities'].append(copy.deepcopy(item))
    elif change == 'another_unknown':
        record['Vulnerabilities'].append({**item, 'VulnerabilityID': 'NEW-UNREVIEWED'})
    else:
        key = {'advisory': 'VulnerabilityID', 'package': 'PkgName',
               'version': 'InstalledVersion', 'severity': 'Severity'}[change]
        item[key] = 'UNREVIEWED'
    with pytest.raises(gate.original.DispositionError):
        gate.apply_review(base, report, binding, openpgp, html)


def test_other_high_cannot_inherit_the_fixed_single_finding(facts):
    facts[0]['raw_counts']['HIGH'] = 2
    facts[0]['unresolved_count'] += 1
    facts[0]['unresolved'].append({'vulnerability': 'NEW-HIGH', 'severity': 'HIGH'})
    with pytest.raises(gate.original.DispositionError):
        gate.apply_review(*facts)


@pytest.mark.parametrize('severity', ['LOW', 'UNKNOWN', 'HIGH'])
def test_every_secret_stays_blocking(facts, severity):
    facts[1]['Results'][0]['Secrets'] = [{'RuleID': 'private-key', 'Severity': severity}]
    with pytest.raises(gate.original.DispositionError):
        gate.apply_review(*facts)


@pytest.mark.parametrize('field,value', [('Secrets', False), ('Vulnerabilities', {}), ('Packages', 0)])
def test_malformed_collections_are_not_empty_successes(facts, field, value):
    facts[1]['Results'][0][field] = value
    with pytest.raises(gate.original.DispositionError):
        gate.apply_review(*facts)


def test_the_actual_independent_record_and_evidence_are_pinned(facts):
    review = gate.load_review(facts[2])
    assert len(review['targets']) == 4 and review['human_risk_acceptance'] is False


def test_changed_review_cannot_activate_a_disposition(facts, tmp_path, monkeypatch):
    raw = (gate.ROOT / gate.REVIEW_PATH).read_bytes()
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    path = tmp_path / gate.REVIEW_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(raw + b' ')
    with pytest.raises(gate.original.DispositionError):
        gate.load_review(facts[2])


@pytest.mark.parametrize('alias', ['scan', 'other_output', 'exact2_output', 'existing_link'])
def test_outputs_never_overwrite_evidence_or_each_other(tmp_path, monkeypatch, alias):
    monkeypatch.setattr(gate.exact2, 'ROOT', tmp_path)
    cache = tmp_path / '.cache/eval'
    cache.mkdir(parents=True)
    inputs = {name: cache / (name + '.json') for name in ('scan', 'archive', 'inspect')}
    for path in inputs.values():
        path.write_bytes(b'ORIGINAL')
    outputs = {name: cache / (name + '.json') for name in ('original-output', 'exact2-output', 'output')}
    if alias == 'scan':
        outputs['output'] = inputs['scan']
    elif alias == 'other_output':
        outputs['output'] = outputs['original-output']
    elif alias == 'exact2_output':
        outputs['output'] = outputs['exact2-output']
    else:
        outputs['output'].hardlink_to(inputs['scan'])
    protected = {path: path.read_bytes() for path in inputs.values()}
    args = [part for name, path in {**inputs, **outputs}.items() for part in ('--' + name, str(path))]
    assert gate.main(args + ['--scanner-exit-code', '0']) == 1
    assert all(path.read_bytes() == raw for path, raw in protected.items())
