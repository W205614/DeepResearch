"""Exercise the real source18 appendix over the unchanged fresh CI report."""
import copy
from datetime import datetime
import gzip
import hashlib
import inspect
import json
from pathlib import Path
import subprocess

import pytest

from scripts import check_grafana_active_source_retirement as gate
from scripts import grafana_source_candidate4 as addon

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'deployment/security/grafana-source-candidate4-evidence-20261107'


@pytest.fixture(scope='module')
def context():
    scan = json.loads(gzip.decompress((EVIDENCE / 'ci-source-fs.raw.json.gz').read_bytes()))
    receipt = json.loads((EVIDENCE / 'ci-completion.json').read_bytes())
    original = json.loads((EVIDENCE / 'ci-source-fs-original-verdict.json').read_bytes())
    candidate = json.loads((EVIDENCE / 'ci-source-fs-candidate-verdict.json').read_bytes())
    inventory = json.loads((EVIDENCE / 'ci-runtime-image-inventory.json').read_bytes())
    previous = json.loads((EVIDENCE / 'ci-source-fs-verdict.json').read_bytes())
    selection = gate.load_review(ROOT, datetime.fromisoformat(receipt['finished_at']), profile='maintenance')
    html = gate.load_html_review(ROOT, selection)
    review, builds = addon.load_review(gate, ROOT)
    prior = {key: value for key, value in candidate.items() if key != 'preserved_original_verdict_file_sha256'}
    return scan, receipt, original, candidate, inventory, previous, html, review, builds, prior


def test_full_fresh_eighteen_rows_preserve_both_real_failed_routes_and_unknown_visibility(context):
    scan, _, _, _, _, previous, html, review, builds, prior = context
    before = copy.deepcopy((scan, previous, prior))
    with pytest.raises(ValueError, match='Exact raw five'):
        gate.apply_retirement(prior, scan)
    with pytest.raises(ValueError, match='original five plus'):
        gate.apply_html_retirement(prior, scan, html, 'Original five-row route still fails')
    result = addon.apply_candidate4(gate, scan, prior, html, review, builds, previous)
    assert result['passed'] and result['raw_blocking_count'] == result['technical_not_applicable_count'] == 18
    assert result['full_result_target_count'] == 62 and result['candidate_blocking_count'] == 4
    assert result['visible_candidate_unknown_count'] == 5 and result['candidate_unknown_dispositions_granted'] == 0
    assert not result['preserved_legacy5_failed_verdict']['passed']
    assert result['preserved_source14_failed_verdict'] == previous and not previous['passed']
    assert not result['artifact_approved'] and not result['global_security_approved'] and not result['human_risk_acceptance']
    assert result['raw_report_modified'] is False and (scan, previous, prior) == before
    assert builds['main']['tempo_packages'] == [] and len(builds['tempo']['tempo_packages']) == 4


def test_historical_ci_replay_uses_full_62_manifest_coverage_and_preserves_raw_verdict_files(context, monkeypatch):
    scan, receipt, original, candidate, inventory, _, _, _, _, _ = context
    # Only the historical CI Git/environment inventory boundary is supplied from its
    # original artifact; live Windows paths and HEAD differ from the Linux merge job.
    monkeypatch.setattr(gate.inventory, 'build_record', lambda root: copy.deepcopy(inventory))
    targets = gate.source.old.committed_manifest_targets(ROOT, 'HEAD')
    assert len(targets) == 62
    args = dict(root=ROOT, now=datetime.fromisoformat(receipt['finished_at']), tracked_clean=True,
                source_commit=receipt['source_commit'], expected_targets=targets, raw_sha256=receipt['raw_sha256'],
                original_file_sha256=hashlib.sha256((EVIDENCE / 'ci-source-fs-original-verdict.json').read_bytes()).hexdigest())
    previous = gate.evaluate(scan, receipt, original, candidate, inventory, profile='maintenance', **args)
    assert previous['passed'] is False and previous['error'] == addon.OLD_ERROR
    result = addon.evaluate(gate, scan, receipt, original, candidate, inventory, previous=previous, **args)
    assert result['passed'] and result['preserved_original_verdict'] == original
    assert result['preserved_candidate_source2_verdict'] == candidate
    assert original['passed'] is False and candidate['passed'] is False


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'duplicate_unknown', 'id', 'package', 'version',
    'severity', 'target', 'new_high', 'new_critical', 'secret_low', 'companion', 'unknown_duplicate', 'unknown_high',
    'coverage', 'unresolved'])
def test_changed_unknown_missing_duplicate_or_secret_raw_rows_fail_closed(context, change):
    scan, _, _, _, _, previous, html, review, builds, prior = copy.deepcopy(context)
    target = next(row for row in scan['Results'] if row['Target'] == addon.PREFIX + 'plugins/tempo/go.mod')
    row = next(v for v in target['Vulnerabilities'] if v['VulnerabilityID'] == 'CVE-2026-21728')
    if change == 'missing':
        target['Vulnerabilities'].remove(row)
    elif change in ('duplicate', 'duplicate_unknown'):
        target['Vulnerabilities'].append({**row, **({'Severity': 'UNKNOWN'} if change == 'duplicate_unknown' else {})})
    elif change in ('id', 'package', 'version', 'severity'):
        row[{'id': 'VulnerabilityID', 'package': 'PkgName', 'version': 'InstalledVersion', 'severity': 'Severity'}[change]] = 'UNKNOWN'
    elif change == 'target':
        target['Target'] += '.unreviewed'
    elif change in ('new_high', 'new_critical'):
        target['Vulnerabilities'].append({**row, 'VulnerabilityID': 'UNREVIEWED',
            'Severity': 'HIGH' if change == 'new_high' else 'CRITICAL'})
    elif change == 'secret_low':
        target['Secrets'] = [{'RuleID': 'synthetic-private-key', 'Severity': 'LOW'}]
    elif change == 'companion':
        next(v for r in scan['Results'] for v in r.get('Vulnerabilities', [])
             if v['VulnerabilityID'] == 'CVE-2026-97045')['Severity'] = 'UNKNOWN'
    elif change.startswith('unknown_'):
        record = next(r for r in scan['Results'] if r['Target'] == addon.PREFIX + 'main/go.mod')
        item = next(v for v in record['Vulnerabilities'] if v['VulnerabilityID'] == 'GO-2026-5932')
        if change == 'unknown_high':
            item['Severity'] = 'HIGH'
        else:
            record['Vulnerabilities'].append(copy.deepcopy(item))
    elif change == 'coverage':
        scan['Results'].append({'Target': 'unreviewed/go.mod', 'Class': 'lang-pkgs', 'Type': 'gomod'})
    else:
        prior['unresolved'].pop()
    with pytest.raises(ValueError):
        addon.apply_candidate4(gate, scan, prior, html, review, builds, previous)


@pytest.mark.parametrize('change', ['source_sha', 'elf_sha', 'vcs', 'flags', 'packages', 'graph'])
def test_actual_source_elf_compiler_and_complete_graph_binding_drift_blocks(context, tmp_path, change):
    review = copy.deepcopy(context[7])
    spec = review['builds']['main']
    if change == 'graph':
        path = tmp_path / 'truncated.json.gz'
        path.write_bytes((ROOT / spec['graph']).read_bytes()[:80])
        spec['graph'] = str(path)
    else:
        key = {'source_sha': 'source_sha256', 'elf_sha': 'elf_sha256', 'vcs': 'vcs_revision',
               'flags': 'tags', 'packages': 'packages'}[change]
        spec[key] = 1 if change == 'packages' else '0' * 64
    with pytest.raises(ValueError):
        addon.verify_builds(gate, review, ROOT)


@pytest.mark.parametrize('change', ['duplicate', 'missing_import', 'deps_error', 'incomplete', 'native_version', 'replace'])
def test_full_graph_parser_rejects_ambiguous_incomplete_or_effectively_different_native_modules(change):
    root = {'ImportPath': 'example/main', 'Imports': ['example/lib'],
            'Module': {'Path': 'example/main', 'Main': True}}
    dep = {'ImportPath': 'example/lib', 'Module': {'Path': 'example/lib', 'Version': 'v1.0.0', 'Sum': 'h1:exact'}}
    native = {'Main': {'Path': 'example/main', 'Version': '(devel)'},
              'Deps': [{'Path': 'example/lib', 'Version': 'v1.0.0', 'Sum': 'h1:exact'}]}
    rows = [root, dep]
    if change == 'duplicate':
        rows.append(copy.deepcopy(dep))
    elif change == 'missing_import':
        root['Imports'].append('missing/package')
    elif change in ('deps_error', 'incomplete'):
        dep['DepsErrors' if change == 'deps_error' else 'Incomplete'] = ['missing'] if change == 'deps_error' else True
    elif change == 'native_version':
        native['Deps'][0]['Version'] = 'v0.1.0'
    else:
        native['Deps'][0]['Replace'] = {'Path': 'other/module', 'Version': 'v1.0.0', 'Sum': 'h1:exact'}
    with pytest.raises(ValueError):
        addon.graph_facts(gate, '\n'.join(json.dumps(row) for row in rows).encode(), native)


def test_changed_independent_review_and_old_function_rewrites_cannot_pass(tmp_path):
    path = tmp_path / addon.REVIEW_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes((ROOT / addon.REVIEW_PATH).read_bytes() + b' ')
    with pytest.raises(ValueError, match='review bytes changed'):
        addon.load_review(gate, tmp_path)
    # These exact function hashes were independently compared to 41aaaed before
    # freezing; the CI shallow checkout need not contain that ancestor object.
    original_hashes = {
        'apply_retirement': 'a79d8141c76e473019c9832af6963782897e5d878d57e17d81529b83347548bf',
        'apply_html_retirement': '8704243dacab6cc8dbfea2c2be7c070a4aa5ec8d1730c47c1007386cfbbf33a2',
        'evaluate': '76e8623e6edf92779dfb4427806c326b8fbe581a3feda1b7970b987b60d5c092',
    }
    for function in (gate.apply_retirement, gate.apply_html_retirement, gate.evaluate):
        assert hashlib.sha256(inspect.getsource(function).encode()).hexdigest() == original_hashes[function.__name__]


def test_exact_review_metadata_survive_actual_git_index_and_clean_lf_checkout(tmp_path):
    review = json.loads((ROOT / addon.REVIEW_PATH).read_bytes())
    exact_binary_text = [addon.REVIEW_PATH,
        'deployment/security/grafana-source-candidate4-evidence-20261107/actual-validation-image-elf-binding.json',
        'deployment/security/grafana-source-candidate4-evidence-20261107/actual-validation-image-inspect.json']
    expected = {addon.REVIEW_PATH: addon.REVIEW_SHA256, **review['evidence_files'],
                **{spec['source']: spec['source_sha256'] for spec in review['builds'].values()}}
    names = sorted(expected)
    repo = tmp_path / 'git-index'
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(['git', *args], cwd=repo, stderr=subprocess.PIPE)

    git('init')
    git('config', 'core.autocrlf', 'true')
    (repo / '.gitattributes').write_bytes((ROOT / '.gitattributes').read_bytes())
    attributes = repo / 'deployment/security/.gitattributes'
    attributes.parent.mkdir(parents=True)
    attributes.write_bytes((ROOT / 'deployment/security/.gitattributes').read_bytes())
    for name in names:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected[name]
    control = repo / 'deployment/security/unrelated-proof.json'
    control.write_bytes(b'control\r\n')
    nested_control = 'deployment/security/unreviewed/grafana-source-candidate4-review-20261107.json'
    (repo / nested_control).parent.mkdir(parents=True)
    (repo / nested_control).write_bytes(b'control\r\n')
    git('add', '.')
    for name in names:
        assert hashlib.sha256(git('cat-file', 'blob', ':' + name)).hexdigest() == expected[name]
    for name in exact_binary_text:
        assert git('check-attr', 'text', '--', name).decode().rstrip().endswith(': text: unset')
    assert git('check-attr', 'text', '--', nested_control).decode().rstrip().endswith(': text: unspecified')
    assert git('cat-file', 'blob', ':' + nested_control) == b'control\n'
    assert git('cat-file', 'blob', ':deployment/security/unrelated-proof.json') == b'control\n'
    checkout = tmp_path / 'lf-checkout'
    checkout.mkdir()
    git('-c', 'core.autocrlf=false', 'checkout-index', '--all', '--prefix=' + checkout.as_posix() + '/')
    for name in names:
        assert hashlib.sha256((checkout / name).read_bytes()).hexdigest() == expected[name]
    assert (checkout / 'deployment/security/unrelated-proof.json').read_bytes() == b'control\n'
    assert (checkout / nested_control).read_bytes() == b'control\n'
    loaded, builds = addon.load_review(gate, checkout)
    assert loaded == review and builds['main']['complete_effective_native_dependencies_equal']
    assert builds['main']['tempo_packages'] == [] and len(builds['tempo']['tempo_packages']) == 4
