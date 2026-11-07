"""The real CI report is retained; new classifications need exact new technical facts."""
import copy
import gzip
import hashlib
import inspect
import json

import pytest

from scripts import check_minio_html_exact4 as gate


EVIDENCE = gate.ROOT / 'deployment/security/minio-html4-classification-evidence-20261107'


@pytest.fixture(scope='module')
def context():
    binding = gate.original.load_binding()
    old = gate.load_review(binding)
    review = gate.load_classification_review(binding, old)
    raw = gzip.decompress((EVIDENCE / 'ci-runtime-image-20261107.json.gz').read_bytes())
    report = gate.exact2.strict_json(raw)
    inspection = gate.exact2.strict_json((EVIDENCE / 'ci-loaded-image-inspect-20261107.json').read_bytes())
    inspection = inspection[0] if isinstance(inspection, list) else inspection
    return binding, old, review, report, inspection


def apply(context, report):
    binding, old, review, _, inspection = context
    original = gate.original.evaluate_scan(report, inspection, binding, scanner_exit_code=0)
    with pytest.raises(gate.original.DispositionError) as legacy:
        gate.apply_review(original, report, binding, gate.exact2.load_technical_review(binding), old)
    result = gate.apply_classification_review(original, report, binding,
        gate.exact2.load_technical_review(binding), old, review, str(legacy.value))
    return original, result


def test_real_ci_replay_preserves_old_failure_and_full_raw_report(context):
    before = copy.deepcopy(context[3])
    original, result = apply(context, context[3])
    saved = gate.exact2.strict_json((EVIDENCE / 'ci-original-failed-verdict-20261107.json').read_bytes())
    assert original == {key: value for key, value in saved.items() if key not in ('archive', 'raw_scan_sha256', 'mode')}
    assert original['passed'] is False and original['raw_counts'] == {'HIGH': 3, 'CRITICAL': 0}
    assert result['passed'] and not result['global_security_approved'] and not result['human_risk_acceptance']
    assert result['legacy_html_gate']['passed'] is False and result['original_gate'] is original
    assert len(result['technical_applied']) == 4 and context[3] == before
    assert hashlib.sha256(inspect.getsource(gate.apply_review).encode()).hexdigest() == context[2]['reviewed_ci']['old_html_function_sha256']


@pytest.mark.parametrize('change', ['target', 'advisory', 'package', 'version', 'severity', 'fixed',
                                   'missing', 'duplicate', 'new_high', 'new_unknown', 'secret', 'suppression', 'inventory'])
def test_changed_or_unreviewed_real_rows_fail_closed(context, change):
    report = copy.deepcopy(context[3])
    record = next(row for row in report['Results'] if row['Target'] == 'usr/bin/mcli')
    item = next(row for row in record['Vulnerabilities'] if row['VulnerabilityID'] == 'CVE-2026-78661')
    if change == 'target':
        record['Target'] = 'usr/bin/unreviewed'
    elif change == 'missing':
        record['Vulnerabilities'].remove(item)
    elif change == 'duplicate':
        record['Vulnerabilities'].append(copy.deepcopy(item))
    elif change in ('new_high', 'new_unknown'):
        record['Vulnerabilities'].append({**item, 'VulnerabilityID': 'UNREVIEWED',
            'Severity': 'HIGH' if change == 'new_high' else 'UNKNOWN'})
    elif change == 'secret':
        record['Secrets'] = [{'RuleID': 'synthetic-private-key', 'Severity': 'LOW'}]
    elif change == 'suppression':
        record['SuppressedFindings'] = [copy.deepcopy(item)]
    elif change == 'inventory':
        next(row for row in record['Packages'] if row['Name'] == gate.PACKAGE)['Version'] = 'v0.62.0'
    else:
        field = {'advisory': 'VulnerabilityID', 'package': 'PkgName', 'version': 'InstalledVersion',
                 'severity': 'Severity', 'fixed': 'FixedVersion'}[change]
        item[field] = 'UNREVIEWED'
    with pytest.raises(gate.original.DispositionError):
        apply(context, report)


def test_another_elf_cannot_inherit_the_review(context):
    binding = copy.deepcopy(context[0])
    binding['binary_sha256']['usr/bin/mcli'] = '0' * 64
    with pytest.raises(gate.original.DispositionError):
        gate.load_classification_review(binding, context[1])


def test_tampered_review_is_rejected_before_any_disposition(context, tmp_path, monkeypatch):
    path = tmp_path / gate.CLASSIFICATION_REVIEW_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes((gate.ROOT / gate.CLASSIFICATION_REVIEW_PATH).read_bytes() + b' ')
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    with pytest.raises(gate.original.DispositionError):
        gate.load_classification_review(context[0], context[1])


@pytest.mark.parametrize('change', ['missing_import', 'html_package', 'duplicate_package', 'error', 'native_version'])
def test_complete_graph_and_native_facts_are_recomputed(context, change):
    build = context[1]['target_builds']['usr/bin/mcli']
    raw = gzip.decompress((gate.ROOT / build['graph']['path']).read_bytes())
    native = gate.exact2.strict_json(gzip.decompress((gate.ROOT /
        'deployment/security/minio-html4-evidence/actual-native-module-inventory-complete.json.gz').read_bytes()))['usr/bin/mcli']
    native = copy.deepcopy(native)
    first, end = json.JSONDecoder().raw_decode(raw.decode('utf-8'))
    if change == 'missing_import':
        first['Imports'] = [*first.get('Imports', []), 'example.invalid/missing-package']
        raw = json.dumps(first).encode() + raw[end:]
    elif change == 'html_package':
        raw += json.dumps({'ImportPath': 'golang.org/x/net/html', 'Imports': []}).encode()
    elif change == 'duplicate_package':
        raw += json.dumps(first).encode()
    elif change == 'error':
        first['Error'] = {'Err': 'synthetic incomplete compile graph'}
        raw = json.dumps(first).encode() + raw[end:]
    else:
        native[gate.PACKAGE]['version'] = 'v0.62.0'
    with pytest.raises(gate.original.DispositionError):
        gate.complete_graph_facts(raw, native)
