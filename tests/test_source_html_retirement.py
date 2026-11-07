"""Exact HTML snapshot facts supplement, and never rewrite, the real failed source scan."""
import copy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from scripts import check_grafana_active_source_retirement as gate


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'deployment/security/grafana-source-html9-evidence-20261107'


@pytest.fixture(scope='module')
def context():
    selection = gate.load_review(ROOT, datetime.now(timezone.utc), profile='maintenance')
    review = gate.load_html_review(ROOT, selection)
    scan = gate.source.old.json_loads(gzip.decompress((EVIDENCE / 'ci-source-fs-20261107.raw.json.gz').read_bytes()))
    previous = gate.source.old.json_loads((EVIDENCE / 'ci-candidate-failed-verdict-20261107.json').read_bytes())
    return selection, review, scan, previous


def test_real_fourteen_high_rows_are_recomputed_without_filtering_or_rewriting_old_verdicts(context):
    _, review, scan, previous = context
    before = copy.deepcopy((scan, previous))
    with pytest.raises(ValueError, match='Exact raw five') as legacy:
        gate.apply_retirement(previous, scan)
    result = gate.apply_html_retirement(previous, scan, review, str(legacy.value))
    assert result['passed'] and not result['global_security_approved'] and not result['new_human_risk_acceptance']
    assert result['raw_blocking_count'] == 14 and result['legacy_blocking_count'] == 5
    assert result['additional_html_blocking_count'] == result['source_html_not_selected_count'] == 9
    assert result['preserved_candidate_gate'] == previous and previous['passed'] is False
    assert result['legacy_five_row_gate']['passed'] is False and (scan, previous) == before
    assert {row['actual_native_dependency']['Version'] for row in result['source_html_not_selected']} == {'v0.62.0'}
    assert hashlib.sha256(inspect.getsource(gate.apply_retirement).encode()).hexdigest() == review['reviewed_ci']['old_five_row_function_sha256']


def test_historical_ci_completion_and_both_failed_verdicts_recompute_from_the_real_commit_tree(context):
    review, scan = context[1], context[2]
    tree = json.loads((ROOT / review['reviewed_ci']['historical_source_tree']).read_bytes())
    assert tree['sha'] == review['reviewed_ci']['commit_tree_sha'] and tree['truncated'] is False
    targets = sorted([[row['path'], 'lang-pkgs', gate.source.old.MANIFEST_TYPES[Path(row['path']).name]]
        for row in tree['tree'] if row['type'] == 'blob' and Path(row['path']).name in gate.source.old.MANIFEST_TYPES])
    assert len(targets) == 47
    receipt = json.loads((EVIDENCE / 'ci-completion-20261107.json').read_bytes())
    clock = datetime.fromisoformat(receipt['finished_at']) + timedelta(seconds=1)
    original = gate.source.old.evaluate(scan, receipt, raw_sha256=review['reviewed_ci']['raw_sha256'],
        source_commit=receipt['source_commit'], root=ROOT, now=clock, tracked_clean=receipt['tracked_clean'], expected_targets=targets)
    assert original == json.loads((EVIDENCE / 'ci-original-failed-verdict-20261107.json').read_bytes())
    candidate = gate.source.evaluate(scan, receipt, original, raw_sha256=review['reviewed_ci']['raw_sha256'],
        source_commit=receipt['source_commit'], root=ROOT, now=clock, tracked_clean=receipt['tracked_clean'], expected_targets=targets)
    assert candidate == {key: value for key, value in context[3].items() if key != 'preserved_original_verdict_file_sha256'}
    assert original['passed'] is False and candidate['passed'] is False
    assert review['reviewed_ci']['historical_replay_is_not_a_fresh_security_scan'] is True


@pytest.mark.parametrize('change', ['target', 'advisory', 'package', 'version', 'severity', 'missing', 'duplicate',
                                   'duplicate_unknown', 'new_high', 'new_critical', 'secret_low', 'suppression', 'companion_severity', 'candidate'])
def test_unknown_changed_duplicate_missing_or_secret_rows_remain_blocked(context, change):
    _, review, scan, previous = copy.deepcopy(context)
    record = next(row for row in scan['Results'] if row['Target'] == gate.OLD_SOURCE)
    item = next(row for row in record['Vulnerabilities'] if row['VulnerabilityID'] == 'CVE-2026-78661')
    if change == 'target':
        record['Target'] = 'unreviewed/source/go.mod'
    elif change == 'missing':
        record['Vulnerabilities'].remove(item)
    elif change == 'duplicate':
        record['Vulnerabilities'].append(copy.deepcopy(item))
    elif change == 'duplicate_unknown':
        record['Vulnerabilities'].append({**item, 'Severity': 'UNKNOWN'})
    elif change in ('new_high', 'new_critical'):
        record['Vulnerabilities'].append({**item, 'VulnerabilityID': 'UNREVIEWED',
            'Severity': 'HIGH' if change == 'new_high' else 'CRITICAL'})
    elif change == 'secret_low':
        record['Secrets'] = [{'RuleID': 'synthetic-private-key', 'Severity': 'LOW'}]
    elif change == 'suppression':
        record['ModifiedFindings'] = [copy.deepcopy(item)]
    elif change == 'companion_severity':
        next(row for row in record['Vulnerabilities'] if row['VulnerabilityID'] == 'CVE-2026-97045')['Severity'] = 'UNKNOWN'
    elif change == 'candidate':
        previous['unresolved'].pop()
    else:
        key = {'advisory': 'VulnerabilityID', 'package': 'PkgName', 'version': 'InstalledVersion', 'severity': 'Severity'}[change]
        item[key] = 'UNREVIEWED'
    with pytest.raises(ValueError):
        gate.apply_html_retirement(previous, scan, review, 'Original exact-five branch remains failed')


@pytest.mark.parametrize('change', ['subject', 'request', 'authorization'])
def test_another_candidate_or_approval_cannot_inherit_the_technical_binding(context, change):
    selection = copy.deepcopy(context[0])
    if change == 'subject':
        selection['subject']['binary_sha256']['usr/share/grafana/bin/grafana'] = '0' * 64
    else:
        selection[change + '_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        gate.load_html_review(ROOT, selection)


def test_changed_review_cannot_enable_a_disposition(context, tmp_path):
    path = tmp_path / gate.HTML_REVIEW_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes((ROOT / gate.HTML_REVIEW_PATH).read_bytes() + b' ')
    with pytest.raises(ValueError):
        gate.load_html_review(tmp_path, context[0])


@pytest.mark.parametrize('change', ['native_version', 'native_replace', 'snapshot', 'fixed_overlay', 'plugin_elf'])
def test_actual_native_fixed_module_source_and_elf_facts_are_checked(context, tmp_path, change):
    review, selection = context[1], context[0]
    index = 1 if change == 'plugin_elf' else 0
    row, fact = review['rows'][index], review['native_source_facts'][index]
    core = 'deployment/security/grafana-maintenance-evidence/core-actual-actual-native-buildinfo.json'
    plugins = 'deployment/security/grafana-maintenance-evidence/plugins-actual-native-buildinfo.json'
    for name in (core, plugins, row['target'], fact['production_go_mod']):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    if change in ('native_version', 'native_replace'):
        native = json.loads((tmp_path / core).read_bytes())
        module = next(item for item in native['Deps'] if item['Path'] == 'golang.org/x/net')
        if change == 'native_version':
            module['Version'] = 'v0.61.0'
        else:
            module['Replace'] = {'Path': 'golang.org/x/net', 'Version': 'v0.61.0'}
        (tmp_path / core).write_text(json.dumps(native), encoding='utf-8')
    elif change == 'snapshot':
        path = tmp_path / row['target']
        path.write_bytes(path.read_bytes() + b'\n')
    elif change == 'fixed_overlay':
        path = tmp_path / fact['production_go_mod']
        text = path.read_text(encoding='utf-8')
        assert 'golang.org/x/net v0.62.0' in text
        path.write_text(text.replace('golang.org/x/net v0.62.0', 'golang.org/x/net v0.61.0'), encoding='utf-8')
    else:
        native = json.loads((tmp_path / plugins).read_bytes())
        native['grafana-postgresql-datasource']['sha256'] = '0' * 64
        (tmp_path / plugins).write_text(json.dumps(native), encoding='utf-8')
    with pytest.raises(ValueError):
        gate.current_html_module_facts(tmp_path, [row], selection['subject'])
