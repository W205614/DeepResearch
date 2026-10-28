"""Synthetic producer boundaries; never runs Trivy, Docker or supplier adapters."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from scripts import run_fs_source_scan as producer

COMMIT = 'a' * 40


@pytest.fixture
def scan_fixture(tmp_path, monkeypatch):
    root, output = tmp_path / 'repo', tmp_path / 'receipt'
    root.mkdir()
    targets = [[f'module-{i}/go.mod', 'lang-pkgs', 'gomod'] for i in range(33)]
    scope = {'scanner_version': '0.75.0', 'required_severities': producer.SEVERITIES,
             'required_scan_targets': targets}
    path = root / producer.SCOPE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'scope': scope}))
    tracked = [(row[0], 'b' * 40) for row in targets] + [(producer.SCOPE, 'b' * 40)]
    monkeypatch.setattr(producer, 'snapshot', lambda _: (COMMIT, tracked))
    monkeypatch.delenv('GITHUB_SHA', raising=False)
    monkeypatch.setattr(producer, 'export_source', lambda _root, dest, _tracked: dest.mkdir())
    scanner = tmp_path / 'synthetic-scanner'
    scanner.write_bytes(b'NOT_A_REAL_SCANNER')
    scan = {'SchemaVersion': 2, 'ArtifactType': 'filesystem', 'Trivy': {'Version': '0.75.0'},
            'ReportID': 'fresh-synthetic-report',
            'Results': [{'Target': t, 'Class': c, 'Type': kind,
                         'Packages': [{'Name': 'fixture/' + t, 'Version': '1'}]} for t, c, kind in targets]}
    scan['Results'][0]['Vulnerabilities'] = [{'VulnerabilityID': 'SYNTHETIC-HIGH', 'Severity': 'HIGH'},
                                           {'VulnerabilityID': 'SYNTHETIC-MEDIUM', 'Severity': 'MEDIUM'}]
    calls = []

    def execute(args, **kwargs):
        calls.append((args, kwargs))
        if '--version' in args:
            return subprocess.CompletedProcess(args, 0, b'Version: 0.75.0\n', b'')
        scan.setdefault('CreatedAt', datetime.now(timezone.utc).isoformat())
        Path(args[args.index('--output') + 1]).write_text(json.dumps(scan))
        return subprocess.CompletedProcess(args, 0, b'', b'')

    monkeypatch.setattr(producer.subprocess, 'run', execute)
    return root, scanner, output, scan, calls, tracked


def invoke(fixture, **kwargs):
    root, scanner, output, *_ = fixture
    return producer.run(root, scanner, output, source_sha=COMMIT, **kwargs)


def test_complete_full_raw_preserves_low_medium_and_secret_counts_without_verdict(scan_fixture, monkeypatch):
    scan_fixture[3]['Results'].append({'Target': 'sample.txt', 'Class': 'secret', 'Type': 'secret',
                                      'Secrets': [{'RuleID': 'SYNTHETIC', 'Severity': 'LOW', 'Match': 'DO_NOT_COPY'}]})
    monkeypatch.setenv('TRIVY_SKIP_DIRS', 'deployment/vendors')
    result = invoke(scan_fixture)
    assert result['completed'] and result['operational_success'] and result['configured_finding_exit_code'] == 0
    assert result['language_target_count'] == 33 and result['result_count'] == 34
    assert result['raw_vulnerability_counts']['MEDIUM'] == result['raw_secret_counts']['LOW'] == 1
    assert result['security_verdict'] == 'not_evaluated_completion_only'
    assert 'DO_NOT_COPY' not in json.dumps(result)
    args, options = scan_fixture[4][-1]
    assert args[args.index('--severity') + 1] == ','.join(producer.SEVERITIES)
    assert '--list-all-pkgs' in args and '--include-dev-deps' in args
    assert '--skip-dirs' not in args and '--skip-files' not in args
    assert 'TRIVY_SKIP_DIRS' not in options['env']
    assert result['filters']['ignorefile_used'] is True and result['filters']['ignorefile_rules_empty'] is True
    assert result['filters']['ignorefile_mode'] == 'verified_empty_neutral'
    assert result['filters']['ignorefile_sha256'] == producer.EMPTY_SHA256
    assert result['filters']['include_dev_dependencies'] is True


def test_operational_exit1_with_valid_findings_is_never_completion(scan_fixture, monkeypatch):
    real = producer.subprocess.run

    def failing(args, **kwargs):
        value = real(args, **kwargs)
        if '--version' not in args:
            value.returncode = 1
        return value

    monkeypatch.setattr(producer.subprocess, 'run', failing)
    result = invoke(scan_fixture)
    assert not result['completed'] and not result['operational_success'] and result['process_exit_code'] == 1


def test_producer_receipt_matches_independent_checker_completion_contract(scan_fixture):
    from scripts import check_fs_source_applicability as checker
    scan_fixture[3]['Results'][0]['Packages'] = [{'Name': 'fixture/module', 'Version': '1'}]
    result = invoke(scan_fixture)
    scope = {'scanner_version': '0.75.0', 'required_severities': producer.SEVERITIES,
             'required_scan_targets': [[r['Target'], r['Class'], r['Type']] for r in scan_fixture[3]['Results']],
             'source_path': 'module-0/go.mod', 'source_inventory_count': 1,
             'source_inventory_sha256': hashlib.sha256(producer.canonical([['fixture/module', '1']])).hexdigest()}
    checker.validate_completion(scan_fixture[3], result, result['raw_sha256'], scope,
                                COMMIT, True, [], datetime.now(timezone.utc))


def test_new_tracked_manifest_extends_full_coverage_without_changing_old_review(scan_fixture):
    target = ['new-runtime/go.mod', 'lang-pkgs', 'gomod']
    scan_fixture[5].append((target[0], 'd' * 40))
    scan_fixture[3]['Results'].append({'Target': target[0], 'Class': target[1], 'Type': target[2],
                                     'Packages': [{'Name': 'new/runtime', 'Version': '1'}]})
    receipt = invoke(scan_fixture)
    assert receipt['completed'] and receipt['language_target_count'] == 34
    from scripts import check_fs_source_applicability as checker
    baseline = [[r['Target'], r['Class'], r['Type']] for r in scan_fixture[3]['Results'][:-1]]
    scope = {'scanner_version': '0.75.0', 'required_severities': producer.SEVERITIES,
             'required_scan_targets': baseline, 'source_path': 'module-0/go.mod', 'source_inventory_count': 1,
             'source_inventory_sha256': hashlib.sha256(producer.canonical([['fixture/module-0/go.mod', '1']])).hexdigest()}
    checker.validate_completion(scan_fixture[3], receipt, receipt['raw_sha256'], scope,
                                COMMIT, True, [], datetime.now(timezone.utc), expected_targets=baseline + [target])
    with pytest.raises(checker.DispositionError, match='coverage'):
        checker.validate_completion(scan_fixture[3], receipt, receipt['raw_sha256'], scope,
                                    COMMIT, True, [], datetime.now(timezone.utc), expected_targets=baseline)


def test_missing_new_tracked_manifest_is_still_incomplete(scan_fixture):
    scan_fixture[5].append(('new-runtime/go.mod', 'd' * 40))
    assert not invoke(scan_fixture)['completed']


def test_old_required_manifest_cannot_be_removed_by_extending_the_inventory(scan_fixture):
    scan_fixture[5].pop(0)
    scan_fixture[5].append(('new-runtime/go.mod', 'd' * 40))
    result = invoke(scan_fixture)
    assert not result['completed'] and not scan_fixture[4]


def test_consumer_derives_manifest_paths_from_the_commit_not_producer_data(tmp_path, monkeypatch):
    from scripts import check_fs_source_applicability as checker
    def execute(args, **kwargs):
        assert args == ['git', 'ls-tree', '-r', '--name-only', '-z', COMMIT]
        return subprocess.CompletedProcess(args, 0,
            b'old/go.mod\0new/go.mod\0src/app.py\0frontend/package-lock.json\0', b'')
    monkeypatch.setattr(checker.subprocess, 'run', execute)
    assert checker.committed_manifest_targets(tmp_path, COMMIT) == [
        ['frontend/package-lock.json', 'lang-pkgs', 'npm'], ['new/go.mod', 'lang-pkgs', 'gomod'],
        ['old/go.mod', 'lang-pkgs', 'gomod']]


@pytest.mark.parametrize('coverage', ['missing', False, None, 1, 'true'])
def test_omitted_or_untyped_development_dependency_coverage_is_rejected(scan_fixture, coverage):
    from scripts import check_fs_source_applicability as checker
    result = invoke(scan_fixture)
    if coverage == 'missing':
        result['filters'].pop('include_dev_dependencies')
    else:
        result['filters']['include_dev_dependencies'] = coverage
    scope = {'scanner_version': '0.75.0', 'required_severities': producer.SEVERITIES}
    with pytest.raises(checker.DispositionError, match='suppression/exclusion'):
        checker.validate_completion(scan_fixture[3], result, result['raw_sha256'], scope,
                                    COMMIT, True, [], datetime.now(timezone.utc))


@pytest.mark.parametrize('case', ['no_ReportID', 'old_report', 'missing_target', 'duplicate_target',
                                  'suppressed', 'false_errors', 'false_findings', 'empty_inventory', 'wrong_scanner'])
def test_invalid_or_partial_report_cannot_create_completion(scan_fixture, case):
    scan = scan_fixture[3]
    if case == 'no_ReportID':
        scan.pop('ReportID')
    elif case == 'old_report':
        scan['CreatedAt'] = '2020-01-01T00:00:00+00:00'
    elif case == 'missing_target':
        scan['Results'].pop()
    elif case == 'duplicate_target':
        scan['Results'].append(scan['Results'][-1])
    elif case == 'suppressed':
        scan['SuppressedFindings'] = [{'ID': 'anything'}]
    elif case == 'false_errors':
        scan['Errors'] = False
    elif case == 'false_findings':
        scan['Results'][0]['Secrets'] = None
    elif case == 'empty_inventory':
        scan['Results'][0]['Packages'] = []
    else:
        scan['Trivy']['Version'] = '0.74.0'
    result = invoke(scan_fixture)
    assert not result['completed'] and not result['operational_success']


def test_old_output_is_never_overwritten_or_reused(scan_fixture):
    output = scan_fixture[2]
    output.mkdir()
    (output / 'raw.json').write_bytes(b'OLD REPORT')
    with pytest.raises(producer.CompletionError, match='fresh'):
        invoke(scan_fixture)
    assert (output / 'raw.json').read_bytes() == b'OLD REPORT' and not scan_fixture[4]


def test_severity_reduction_rejected_before_scanner_execution(scan_fixture):
    result = invoke(scan_fixture, severities=['HIGH', 'CRITICAL'])
    assert not result['completed'] and not scan_fixture[4]


def test_neutral_ignore_file_cannot_gain_rules(scan_fixture, monkeypatch):
    execute = producer.subprocess.run

    def tamper(args, **kwargs):
        result = execute(args, **kwargs)
        if '--ignorefile' in args:
            Path(args[args.index('--ignorefile') + 1]).write_text('CVE-2099-1\n')
        return result

    monkeypatch.setattr(producer.subprocess, 'run', tamper)
    assert not invoke(scan_fixture)['completed']


def test_neutral_ignore_file_missing_or_symbolic_link_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / 'empty.ignore'
    with pytest.raises(producer.CompletionError):
        producer.verify_neutral_ignorefile(path)
    path.write_bytes(b'')
    monkeypatch.setattr(Path, 'is_symlink', lambda candidate: candidate == path)
    with pytest.raises(producer.CompletionError):
        producer.verify_neutral_ignorefile(path)


def test_explicit_SHA_cannot_override_different_GitHub_SHA(scan_fixture, monkeypatch):
    monkeypatch.setenv('GITHUB_SHA', 'c' * 40)
    assert not invoke(scan_fixture)['completed'] and not scan_fixture[4]


def test_source_change_during_scan_blocks_completion(scan_fixture, monkeypatch):
    responses = iter([(COMMIT, scan_fixture[-1]), ('c' * 40, scan_fixture[-1])])
    monkeypatch.setattr(producer, 'snapshot', lambda _: next(responses))
    assert not invoke(scan_fixture)['completed']


@pytest.mark.parametrize('mode', ['complete', 'omitted', 'transformed'])
def test_full_git_export_checks_every_tracked_blob(tmp_path, monkeypatch, mode):
    data = b'original tracked source\n'
    oid = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

    def archive(args, **kwargs):
        with tarfile.open(args[args.index('--output') + 1], 'w') as stream:
            if mode != 'omitted':
                body = data if mode == 'complete' else b'TRANSFORMED'
                item = tarfile.TarInfo('source.txt')
                item.size = len(body)
                stream.addfile(item, io.BytesIO(body))

    monkeypatch.setattr(producer.subprocess, 'run', archive)
    if mode == 'complete':
        producer.export_source(tmp_path, tmp_path / 'source', [('source.txt', oid)])
        assert (tmp_path / 'source/source.txt').read_bytes() == data
    else:
        with pytest.raises(producer.CompletionError):
            producer.export_source(tmp_path, tmp_path / 'source', [('source.txt', oid)])
