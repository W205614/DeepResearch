"""Direct cases for only the newly introduced exact technical boundary."""
import copy
import json

import pytest

from scripts import check_minio_openpgp_exact2 as gate


@pytest.fixture
def facts():
    binding = json.loads((gate.ROOT / 'deployment/security/minio-fixed-disposition.json').read_bytes())['binding']
    review = gate.load_technical_review(binding)
    report = {'Results': [{'Target': target, 'Vulnerabilities': [{
        'VulnerabilityID': 'GO-2026-5932', 'PkgName': 'golang.org/x/crypto',
        'InstalledVersion': 'v0.58.0', 'Severity': 'UNKNOWN'}]} for target in sorted(gate.TARGETS)]}
    original = {'passed': False, 'raw_counts': {'HIGH': 1, 'CRITICAL': 0},
        'verified_fixed_count': 1, 'unresolved_count': 2,
        'verified_fixed': [{'vulnerability': binding['vulnerability'],
                           'target': binding['finding']['target'], 'disposition': 'FIXED'}],
        'unresolved': [{'reason': 'unknown_severity', 'finding': 'GO-2026-5932'}] * 2}
    return binding, review, report, original


def test_exact_two_keep_original_failure_and_raw_unchanged(facts):
    binding, review, report, original = facts
    before = copy.deepcopy((report, original))
    result = gate.apply_exact_technical_facts(original, report, binding, review)
    assert result['passed'] and len(result['technical_applied']) == 2
    assert result['original_gate']['passed'] is False
    assert all(row['raw_severity'] == 'UNKNOWN' for row in result['technical_applied'])
    assert (report, original) == before


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'advisory', 'package', 'version', 'target', 'severity', 'extra_unknown'])
def test_changed_missing_duplicate_or_new_unknown_never_inherits_facts(facts, change):
    binding, review, report, original = facts
    if change == 'missing':
        report['Results'].pop()
    elif change == 'duplicate':
        report['Results'].append(copy.deepcopy(report['Results'][0]))
    elif change == 'extra_unknown':
        extra = copy.deepcopy(report['Results'][0])
        extra['Vulnerabilities'][0]['VulnerabilityID'] = 'UNREVIEWED-NEW'
        report['Results'].append(extra)
    elif change == 'target':
        report['Results'][0]['Target'] = 'usr/bin/another'
    else:
        field = {'advisory': 'VulnerabilityID', 'package': 'PkgName',
                 'version': 'InstalledVersion', 'severity': 'Severity'}[change]
        report['Results'][0]['Vulnerabilities'][0][field] = 'UNREVIEWED-CHANGED'
    with pytest.raises(gate.original.DispositionError):
        gate.apply_exact_technical_facts(original, report, binding, review)


def test_other_high_remains_blocking_even_with_exact_unknowns(facts):
    binding, review, report, original = facts
    original['raw_counts']['HIGH'] = 2
    original['unresolved_count'] = 3
    original['unresolved'].append({'vulnerability': 'UNREVIEWED-HIGH', 'severity': 'HIGH'})
    with pytest.raises(gate.original.DispositionError):
        gate.apply_exact_technical_facts(original, report, binding, review)


@pytest.mark.parametrize('severity', ['LOW', 'UNKNOWN'])
def test_secret_stays_blocking_at_any_severity(facts, severity):
    binding, review, report, original = facts
    report['Results'][0]['Secrets'] = [{'RuleID': 'private-key', 'Severity': severity}]
    with pytest.raises(gate.original.DispositionError):
        gate.apply_exact_technical_facts(original, report, binding, review)


def test_changed_review_bytes_cannot_activate_a_disposition(facts, tmp_path, monkeypatch):
    binding, _, _, _ = facts
    raw = (gate.ROOT / gate.REVIEW_PATH).read_bytes()
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    path = tmp_path / gate.REVIEW_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(raw + b' ')
    with pytest.raises(gate.original.DispositionError):
        gate.load_technical_review(binding)


def test_original_green_stays_original_without_inventing_technical_matches(facts):
    binding, review, report, original = facts
    for row in report['Results']:
        row['Vulnerabilities'] = []
    original.update(passed=True, unresolved_count=0, unresolved=[])
    result = gate.apply_exact_technical_facts(original, report, binding, review)
    assert result['passed'] and result['technical_applied'] == []
    assert result['original_gate'] is original


@pytest.mark.parametrize('field,value', [('Secrets', False), ('Secrets', 0), ('Secrets', {}),
                                         ('Vulnerabilities', {}), ('Packages', False)])
def test_malformed_collection_is_not_an_empty_success(facts, field, value):
    binding, review, report, original = facts
    report['Results'][0][field] = value
    with pytest.raises(gate.original.DispositionError):
        gate.apply_exact_technical_facts(original, report, binding, review)


@pytest.mark.parametrize('raw', ['{"Results":[],"Results":[{}]}', '{"x":NaN}'])
def test_ambiguous_or_nonfinite_json_is_rejected(raw):
    with pytest.raises(gate.original.DispositionError):
        gate.strict_json(raw)


@pytest.mark.parametrize('alias', ['scan', 'archive', 'inspect', 'other_output', 'outside_cache', 'existing_hardlink'])
def test_unsafe_output_returns_failure_without_overwriting_evidence(tmp_path, monkeypatch, alias, capsys):
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    cache = tmp_path / '.cache/eval'
    cache.mkdir(parents=True)
    inputs = {name: cache / (name + '.json') for name in ('scan', 'archive', 'inspect')}
    for path in inputs.values():
        path.write_bytes(b'ORIGINAL_EVIDENCE')
    original_output, output = cache / 'original.json', cache / 'final.json'
    if alias in inputs:
        output = inputs[alias]
    elif alias == 'other_output':
        output = original_output
    elif alias == 'existing_hardlink':
        frozen = tmp_path / 'frozen-proof.txt'
        frozen.write_bytes(b'ORIGINAL_EVIDENCE')
        output = cache / 'existing-frozen-link.json'
        output.hardlink_to(frozen)
    else:
        output = tmp_path / 'protected-source.py'
        output.write_bytes(b'ORIGINAL_EVIDENCE')
    protected = {path: path.read_bytes() for path in [*inputs.values(), output] if path.exists()}
    argv = [part for name, path in inputs.items() for part in ('--' + name, str(path))]
    argv += ['--scanner-exit-code', '0', '--original-output', str(original_output), '--output', str(output)]
    assert gate.main(argv) == 1
    assert json.loads(capsys.readouterr().out)['mode'] == 'failed_closed'
    assert all(path.read_bytes() == raw for path, raw in protected.items())
    assert not original_output.exists()


def test_linked_output_ancestor_cannot_redirect_evidence_writes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    frozen = tmp_path / 'frozen-directory'
    frozen.mkdir()
    cache = tmp_path / '.cache/eval'
    cache.parent.mkdir()
    try:
        cache.symlink_to(frozen, target_is_directory=True)
    except OSError:
        pytest.skip('Creating symlinks needs platform permission; Linux CI exercises this case')
    inputs = {}
    for name in ('scan', 'archive', 'inspect'):
        path = tmp_path / (name + '.json')
        path.write_bytes(b'ORIGINAL_EVIDENCE')
        inputs[name] = path
    argv = [part for name, path in inputs.items() for part in ('--' + name, str(path))]
    argv += ['--scanner-exit-code', '0', '--original-output', str(cache / 'original.json'),
             '--output', str(cache / 'final.json')]
    assert gate.main(argv) == 1
    assert json.loads(capsys.readouterr().out)['mode'] == 'failed_closed'
    assert list(frozen.iterdir()) == []
    assert all(path.read_bytes() == b'ORIGINAL_EVIDENCE' for path in inputs.values())
