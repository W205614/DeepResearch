"""Exact fixed-code supplement; unknown findings, bytes and secrets still block."""
from copy import deepcopy
import hashlib
import json
import shutil

import pytest

from scripts import check_agent_native_packages as gate
from tests.test_agent_native_packages import NOW, inputs as inputs


@pytest.fixture
def supplemental(inputs):
    report, facts, lock = inputs
    review = gate.load_supplement(lock)
    package = next(p for p in lock['packages'] if p['name'] == 'libsqlite3-0')
    base = report['Results'][0]['Vulnerabilities'][0]
    for advisory in gate.SUPPLEMENT_FIXES:
        report['Results'][0]['Vulnerabilities'].append(deepcopy(base) | {'VulnerabilityID': advisory})
    facts['runtime_probe'] = {'sqlite': '3.54.1', 'loaded': {'libsqlite3-0': [
        {'path': package['library'], 'sha256': package['library_sha256']}]}}
    return report, facts, lock, review


def check(values):
    report, facts, lock, review = values
    return gate.evaluate(report, facts, lock, now=NOW, supplement=review)


def test_only_two_supplemental_exact_fixed_tuples_pass_without_mutating_inputs(supplemental):
    before = deepcopy(supplemental)
    result = check(supplemental)
    assert result['passed'] and result['raw_counts'] == {'HIGH': 4}
    added = [row for row in result['verified_fixed'] if row.get('review_kind') == 'FIXED_CODE']
    assert {row['id'] for row in added} == {'CVE-2026-119374', 'CVE-2026-119379'}
    assert all(row['risk_acceptance'] is False and row['supplement_sha256'] == gate.SUPPLEMENT_SHA256 for row in added)
    assert result['native_lock_sha256'] == 'c8354130c02ef81c173bcc62cfb2af717ec7af8f5acf98b2fb3a2782d0260454'
    assert result['raw_reports_modified'] is False and supplemental == before


def test_absent_supplement_keeps_both_fresh_findings_blocking(supplemental):
    report, facts, lock, _ = supplemental
    result = gate.evaluate(report, facts, lock, now=NOW)
    assert not result['passed']
    assert {row['id'] for row in result['unresolved']} == {'CVE-2026-119374', 'CVE-2026-119379'}


@pytest.mark.parametrize('attack', ['unreviewed-id', 'other-package', 'older-version', 'newer-version',
                                  'critical', 'other-source', 'language-finding'])
def test_supplement_never_generalizes_to_another_finding_tuple(supplemental, attack):
    report, _, _, _ = supplemental
    row = report['Results'][0]['Vulnerabilities'][-1]
    if attack == 'unreviewed-id':
        row['VulnerabilityID'] = 'CVE-2026-119375'
    elif attack == 'other-package':
        row['PkgName'], row['InstalledVersion'] = 'os-package-0', '1'
    elif attack in {'older-version', 'newer-version'}:
        version, release = ('3.54.0', '1') if attack == 'older-version' else ('3.54.2', '1')
        report['Results'][0]['Packages'].append({'Name': 'libsqlite3-0', 'Version': version, 'Release': release})
        row['InstalledVersion'] = version + '-' + release
    elif attack == 'critical':
        row['Severity'] = 'CRITICAL'
    elif attack == 'other-source':
        row['DataSource'] = {'ID': 'nvd'}
    else:
        report['Results'][0]['Vulnerabilities'].remove(row)
        report['Results'][1]['Vulnerabilities'] = [row]
        report['Results'][1]['Packages'].append({'Name': 'libsqlite3-0', 'Version': '3.54.1', 'Release': '2'})
    assert not check(supplemental)['passed']


@pytest.mark.parametrize('attack', ['file-sha', 'loaded-sha', 'probe-missing', 'loaded-version',
                                  'loaded-path', 'loaded-map-sha', 'duplicate-loaded-copy'])
def test_supplement_requires_current_exact_uniquely_loaded_library(supplemental, attack):
    _, facts, _, _ = supplemental
    package = facts['packages']['libsqlite3-0']
    if attack == 'file-sha':
        package['library_sha256'] = '0' * 64
    elif attack == 'loaded-sha':
        package['loaded_library_sha256'] = '0' * 64
    elif attack == 'probe-missing':
        facts.pop('runtime_probe')
    elif attack == 'loaded-version':
        facts['runtime_probe']['sqlite'] = '3.46.1'
    elif attack == 'loaded-path':
        facts['runtime_probe']['loaded']['libsqlite3-0'][0]['path'] = '/usr/local/lib/libsqlite3.so.0'
    elif attack == 'loaded-map-sha':
        facts['runtime_probe']['loaded']['libsqlite3-0'][0]['sha256'] = '0' * 64
    else:
        loaded = facts['runtime_probe']['loaded']['libsqlite3-0']
        loaded.append(deepcopy(loaded[0]))
    with pytest.raises((ValueError, KeyError)):
        check(supplemental)


@pytest.mark.parametrize('attack', ['risk-waiver', 'base-lock', 'package-version', 'package-sha',
                                  'additional-advisory', 'critical-scope', 'wrong-upstream-fix'])
def test_supplement_metadata_cannot_expand_or_replace_the_original_review(supplemental, attack):
    review = supplemental[3]
    if attack == 'risk-waiver':
        review['risk_acceptance'] = True
    elif attack == 'base-lock':
        review['base_native_lock_sha256'] = '0' * 64
    elif attack == 'package-version':
        review['package']['version'] = '3.54.2-1'
    elif attack == 'package-sha':
        review['package']['library_sha256'] = '0' * 64
    elif attack == 'additional-advisory':
        review['findings'].append(deepcopy(review['findings'][0]) | {'id': 'CVE-2026-119375'})
    elif attack == 'critical-scope':
        review['findings'][0]['severity'] = 'CRITICAL'
    else:
        review['findings'][0]['upstream_fix'] = '0' * 40
    with pytest.raises(ValueError):
        check(supplemental)


@pytest.mark.parametrize('attack', ['secret', 'malformed-secret', 'unknown-high', 'missing-python',
                                  'filtered', 'duplicate', 'image-mismatch'])
def test_original_complete_scan_and_secret_barriers_still_apply(supplemental, attack):
    report = supplemental[0]
    if attack == 'secret':
        report['Results'][0]['Secrets'] = [{'Severity': 'LOW', 'Match': 'never-disclose-this-fixture'}]
    elif attack == 'malformed-secret':
        report['Secrets'] = ''
    elif attack == 'unknown-high':
        finding = deepcopy(report['Results'][0]['Vulnerabilities'][0]) | {'VulnerabilityID': 'CVE-UNREVIEWED'}
        report['Results'][0]['Vulnerabilities'].append(finding)
        assert not check(supplemental)['passed']
        return
    elif attack == 'missing-python':
        report['Results'].pop()
    elif attack == 'filtered':
        report['SuppressedFindings'] = [{}]
    elif attack == 'duplicate':
        report['Results'][0]['Vulnerabilities'].append(deepcopy(report['Results'][0]['Vulnerabilities'][-1]))
    else:
        report['Metadata']['ImageID'] = 'sha256:' + '0' * 64
    with pytest.raises(ValueError) as failure:
        check(supplemental)
    assert 'never-disclose-this-fixture' not in str(failure.value)


@pytest.mark.parametrize('attack', ['supplement-bytes', 'evidence-bytes', 'evidence-missing'])
def test_pinned_supplement_and_each_primary_evidence_file_fail_closed(tmp_path, attack):
    lock = gate.load_lock()
    review = gate.load_supplement(lock)
    paths = [gate.SUPPLEMENT_PATH, *review['evidence']]
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(gate.ROOT / relative, target)
    if attack == 'supplement-bytes':
        path = tmp_path / gate.SUPPLEMENT_PATH
        path.write_bytes(path.read_bytes() + b' ')
    else:
        path = tmp_path / next(iter(review['evidence']))
        if attack == 'evidence-bytes':
            path.write_bytes(path.read_bytes() + b'changed')
        else:
            path.rename(path.with_name(path.name + '.retained'))
    with pytest.raises((ValueError, OSError)):
        gate.load_supplement(lock, root=tmp_path)


def test_original_lock_and_all_original_evidence_bytes_are_unchanged():
    lock = gate.load_lock()
    assert gate.digest(gate.LOCK) == gate.LOCK_SHA256
    assert lock['packages'][0]['fixed_advisories'] == ['CVE-2026-119373']
    assert lock['packages'][1]['fixed_advisories'] == ['CVE-2026-76844', 'CVE-2026-85091']
    review = gate.load_supplement(lock)
    source_path = next(path for path in review['evidence'] if path.endswith('sqlite-3.54.1-source-review.json'))
    source = json.loads((gate.ROOT / source_path).read_bytes())
    assert source['sqlite3_c_sha3_256'] == 'e2b869d3003b33c942b7dd7063604fbc53553e1b66609570df39b9b7b558ee81'
    assert source['official_release_sha3_match'] and all(source['fixed_code_checks'].values())
    assert source['active_patch_targets_reverting_reviewed_functions'] == []
    assert review['review_kind'] == 'FIXED_CODE' and review['risk_acceptance'] is False
    assert hashlib.sha256((gate.ROOT / gate.SUPPLEMENT_PATH).read_bytes()).hexdigest() == gate.SUPPLEMENT_SHA256
