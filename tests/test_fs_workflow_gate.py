import ast
from copy import deepcopy
from pathlib import Path
import shlex
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SELECTOR_FIXTURES = ROOT / 'tests/fixtures/grafana-fixed-20261107'
PROFILE_CONTRACTS = {
    'maintenance': ('check_grafana_active_source_retirement.py', {'--profile': 'maintenance'}, 'check_grafana_maintenance'),
    'fixed20261107': ('check_grafana_source_fixed_20261107.py', {}, 'check_grafana_release_20261107'),
    'grafana-source-20261110': ('check_grafana_source_scan_20261110.py', {}, 'check_grafana_source_build_20261110'),
    'grafana-source-20261112': ('check_grafana_source_scan_20261112.py', {}, 'check_grafana_source_build_20261112'),
}
BASE = '.cache/eval/'
SOURCE_READY = "always() && steps.source_scan.outcome == 'success'"


def source_job_steps(document):
    job = document['jobs']['dependency-and-image-scan']
    assert job.get('if') is None and not job.get('continue-on-error')
    return job['steps']


def source_steps(root=ROOT):
    source = root / '.github/workflows/security.yml'
    return source_job_steps(yaml.safe_load(source.read_text(encoding='utf-8')))


def selected_profile(root):
    # Read the owning inventory declaration without executing its authorization
    # or Docker boundaries before a proposed selector has been approved.
    tree = ast.parse((root / 'scripts/runtime_ci_inventory.py').read_text(encoding='utf8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'grafana_selection')
    profiles = [ast.literal_eval(value) for node in ast.walk(function) if isinstance(node, ast.Dict)
                for key, value in zip(node.keys, node.values) if isinstance(key, ast.Constant) and key.value == 'profile']
    assert len(profiles) == 1 and profiles[0] in PROFILE_CONTRACTS
    profile = profiles[0]
    consumer_alias = 'source_candidate' if profile in {'grafana-source-20261110', 'grafana-source-20261112'} else 'candidate'
    consumers = [alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == 'scripts'
                 for alias in node.names if alias.asname == consumer_alias]
    assert consumers == [PROFILE_CONTRACTS[profile][2]], 'Inventory profile and its exact consumer must agree'
    return profile


@pytest.fixture(scope='module', params=['maintenance', 'fixed20261107'])
def selected_workflow_root(request, tmp_path_factory):
    root = tmp_path_factory.mktemp('workflow-' + request.param)
    for name in ['compose.yaml', 'scripts/runtime_ci_inventory.py', '.github/workflows/ci.yml', '.github/workflows/security.yml']:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((SELECTOR_FIXTURES / 'selectors-before' / name).read_bytes())
    subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True)
    if request.param == 'fixed20261107':
        subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', str(SELECTOR_FIXTURES / 'activation.patch')],
                       cwd=root, check=True, capture_output=True)
    assert selected_profile(root) == request.param
    return root


def command(steps, script, *, artifact=None):
    cached_artifact = {'build_go_stdlib_security_20261112.py': 'grafana',
                       'build_minio_os_20261113.py': 'minio'}.get(script)
    if cached_artifact is not None:
        cached = [(i, step) for i, step in enumerate(steps)
                  if step.get('uses') == './.github/actions/reviewed-source-image'
                  and step['with']['artifact'] == cached_artifact]
        if cached:
            assert len(cached) == 1
            index, step = cached[0]
            assert set(step['with']) == {'artifact', 'output-dir'} and 'run' not in step
            args = {'--output-dir': step['with']['output-dir']}
            if cached_artifact == 'grafana':
                args = {'--artifact': 'grafana', **args}
            return index, step, args
    found = [(index, step) for index, step in enumerate(steps) if 'scripts/' + script in step.get('run', '')
             and (artifact is None or '--artifact ' + artifact + ' ' in step.get('run', ''))]
    assert len(found) == 1, 'The source producer or required technical gate is missing or duplicated'
    index, step = found[0]
    run = step['run']
    assert not any(token in run for token in ('||', '&&', ';', '|', '`'))
    tokens = shlex.split(run)
    if script in {'build_go_stdlib_security_20261112.py', 'build_minio_os_20261113.py'}:
        assert tokens[:4] == ['uv', 'run', '--frozen', '--no-dev']
        tokens = tokens[4:]
    assert tokens[:2] == ['python', 'scripts/' + script] and len(tokens[2:]) % 2 == 0
    arguments = dict(zip(tokens[2::2], tokens[3::2]))
    assert len(arguments) == len(tokens[2:]) // 2
    return index, step, arguments


def assert_source_gate_contract(steps, profile):
    assert profile in PROFILE_CONTRACTS, 'Only the exact declared inventory profiles are supported'
    final_script, profile_args, _ = PROFILE_CONTRACTS[profile]
    producers = [i for i, step in enumerate(steps) if 'scripts/run_fs_source_scan.py' in step.get('run', '')]
    assert len(producers) == 1
    producer_index = producers[0]
    producer = steps[producer_index]
    assert producer['id'] == 'source_scan'
    assert producer.get('if') is None and not producer.get('continue-on-error')
    old_index, old, old_args = command(steps, 'check_fs_source_applicability.py')
    new_index, new, new_args = command(steps, 'check_grafana_candidate_source_exact2.py')
    inventory_index, current, current_args = command(steps, 'runtime_ci_inventory.py')
    final_index, final, final_args = command(steps, final_script)
    other_scripts = {contract[0] for name, contract in PROFILE_CONTRACTS.items() if name != profile}
    assert not any('scripts/' + other in step.get('run', '') for step in steps for other in other_scripts)
    assert producer_index < old_index < new_index < final_index
    assert inventory_index < final_index and not current_args
    assert current.get('if') in (None, SOURCE_READY) and not current.get('continue-on-error')
    assert old.get('continue-on-error') is True and new.get('continue-on-error') is True
    assert old.get('if') in (None, SOURCE_READY) and new.get('if') == SOURCE_READY
    # The intermediate reports deliberately retain failures. This final step must
    # still run after those failures whenever the complete producer succeeded.
    assert final.get('if') == SOURCE_READY and not final.get('continue-on-error')
    assert all(not step.get('continue-on-error') for index, step in enumerate(steps) if index not in {old_index, new_index})
    raw = {'--scan': BASE + 'source-fs/raw.json', '--scan-receipt': BASE + 'source-fs/completion.json'}
    assert old_args == raw | {'--output': BASE + 'source-fs-original-verdict.json'}
    assert new_args == raw | {'--original-verdict': BASE + 'source-fs-original-verdict.json',
        '--output': BASE + 'source-fs-candidate-verdict.json'}
    assert final_args == raw | profile_args | {
        '--original-verdict': BASE + 'source-fs-original-verdict.json',
        '--candidate-verdict': BASE + 'source-fs-candidate-verdict.json',
        '--runtime-inventory': BASE + 'runtime-image-inventory.json', '--output': BASE + 'source-fs-verdict.json'}
    setup = next(step for step in steps if step.get('uses', '').startswith('aquasecurity/setup-trivy@'))
    assert setup['uses'] == 'aquasecurity/setup-trivy@3fb12ec12f41e471780db15c232d5dd185dcb514'
    assert setup['with']['version'] == 'v0.75.0'


def test_raw_source_scan_completion_cannot_replace_the_independent_technical_gate():
    assert_source_gate_contract(source_steps(), selected_profile(ROOT))


def test_both_exact_profiles_keep_the_required_gate_in_a_real_temporary_selector_patch(selected_workflow_root):
    assert_source_gate_contract(source_steps(selected_workflow_root), selected_profile(selected_workflow_root))


@pytest.mark.parametrize('mutation', ['missing_final', 'disabled_final', 'ignored_final', 'conditional_skip',
    'shell_bypass', 'duplicate_final', 'wrong_order', 'ignored_producer', 'disabled_producer',
    'duplicate_producer', 'overwritten_old', 'missing_candidate_input', 'old_profile'])
def test_missing_disabled_or_bypassed_final_gate_breaks_the_workflow_contract(mutation, selected_workflow_root):
    profile = selected_profile(selected_workflow_root)
    steps = deepcopy(source_steps(selected_workflow_root))
    final_script = PROFILE_CONTRACTS[profile][0]
    final = next(step for step in steps if 'scripts/' + final_script in step.get('run', ''))
    producer = next(step for step in steps if 'scripts/run_fs_source_scan.py' in step.get('run', ''))
    new = next(step for step in steps if 'scripts/check_grafana_candidate_source_exact2.py' in step.get('run', ''))
    if mutation == 'missing_final':
        steps.remove(final)
    elif mutation == 'disabled_final':
        final['if'] = False
    elif mutation == 'ignored_final':
        final['continue-on-error'] = True
    elif mutation == 'conditional_skip':
        final['if'] = "steps.source_original.outcome == 'success'"
    elif mutation == 'shell_bypass':
        final['run'] += ' || true'
    elif mutation == 'duplicate_final':
        steps.append(deepcopy(final))
    elif mutation == 'wrong_order':
        steps.remove(final)
        steps.insert(0, final)
    elif mutation == 'ignored_producer':
        producer['continue-on-error'] = True
    elif mutation == 'disabled_producer':
        producer['if'] = False
    elif mutation == 'duplicate_producer':
        steps.append(deepcopy(producer))
    elif mutation == 'overwritten_old':
        new['run'] = new['run'].replace('--output ' + BASE + 'source-fs-candidate-verdict.json',
                                       '--output ' + BASE + 'source-fs-original-verdict.json')
    elif mutation == 'old_profile':
        final['run'] = (final['run'].replace('--profile maintenance', '--profile exact28')
                        if profile == 'maintenance' else final['run'] + ' --profile exact28')
    else:
        final['run'] = final['run'].replace('--candidate-verdict ' + BASE + 'source-fs-candidate-verdict.json', '')
    with pytest.raises(AssertionError):
        assert_source_gate_contract(steps, profile)


def test_raw_source_evidence_and_both_intermediate_failures_survive_final_rejection(selected_workflow_root):
    steps = source_steps(selected_workflow_root)
    converter = next(step for step in steps if 'trivy convert' in step.get('run', ''))
    assert 'always()' in converter['if']
    assert '.cache/eval/source-fs/raw.json' in converter['run']
    artifact = next(step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@'))
    assert artifact['if'] == 'always()' and artifact['with']['if-no-files-found'] == 'error'
    paths = set(artifact['with']['path'].splitlines())
    assert {BASE + name for name in ('source-fs/raw.json', 'source-fs/completion.json',
        'source-fs-original-verdict.json', 'source-fs-candidate-verdict.json', 'runtime-image-inventory.json',
        'source-fs-verdict.json')}.issubset(paths)
    assert 'trivy-fs.sarif' in paths


def test_existing_high_critical_threshold_and_exact_technical_route_are_explicit():
    source = Path(__file__).parents[1] / '.github/workflows/security.yml'
    text = source.read_text(encoding='utf-8')
    assert 'original HIGH,CRITICAL source threshold' in text
    assert 'source16' not in text and 'runtime5' not in text
    assert text.count('severity: HIGH,CRITICAL') == 2


@pytest.mark.parametrize('change', [{'if': False}, {'continue-on-error': True}])
def test_source_job_itself_cannot_be_disabled_or_ignore_failure(change):
    document = {'jobs': {'dependency-and-image-scan': {'steps': [], **change}}}
    with pytest.raises(AssertionError):
        source_job_steps(document)


def test_a_valid_gate_for_another_profile_cannot_be_used_as_a_fallback(selected_workflow_root):
    profile = selected_profile(selected_workflow_root)
    other = next(name for name in PROFILE_CONTRACTS if name != profile)
    with pytest.raises(AssertionError):
        assert_source_gate_contract(source_steps(selected_workflow_root), other)
    with pytest.raises(AssertionError):
        assert_source_gate_contract(source_steps(selected_workflow_root), 'unreviewed')


def test_inventory_cannot_label_a_different_consumer_as_the_selected_profile(selected_workflow_root, tmp_path):
    profile = selected_profile(selected_workflow_root)
    other = next(name for name in PROFILE_CONTRACTS if name != profile)
    source = (selected_workflow_root / 'scripts/runtime_ci_inventory.py').read_text(encoding='utf8')
    path = tmp_path / 'scripts/runtime_ci_inventory.py'
    path.parent.mkdir(parents=True)
    path.write_text(source.replace("'profile': '" + profile + "'", "'profile': '" + other + "'"), encoding='utf8')
    with pytest.raises(AssertionError, match='Inventory profile and its exact consumer'):
        selected_profile(tmp_path)


def test_source_built_grafana_keeps_full_native_all_severity_gate_before_acceptance():
    document = yaml.safe_load((ROOT / '.github/workflows/security.yml').read_text(encoding='utf-8'))
    job = document['jobs']['runtime-image-scan']
    assert job['strategy']['matrix'] == '${{ fromJSON(needs.inventory-runtime.outputs.matrix) }}'
    steps = job['steps']
    build_index, build, build_args = command(steps, 'build_go_stdlib_security_20261112.py')
    native_index, native, native_args = command(steps, 'scan_grafana_source_20261112.py')
    gate_index, gate, gate_args = command(steps, 'check_grafana_source_build_20261112.py')
    scan_index = next(i for i, step in enumerate(steps) if step.get('id') == 'approved_scan')
    secret_index, secret, _ = command(steps, 'check_image_secrets.py')
    scan = steps[scan_index]
    condition = "matrix.source_build_profile == 'grafana-source-20261112'"
    assert build_index < scan_index < secret_index < native_index < gate_index
    assert build['if'] == condition
    assert native['if'] == gate['if'] == condition + " && steps.approved_scan.outcome == 'success'"
    assert "matrix.source_build_profile != ''" in scan['if'] and "matrix.source_build_profile != ''" in secret['if']
    assert 'matrix.image' in scan['with']['image-ref']
    assert not any(step.get('continue-on-error') for step in (build, scan, secret, native, gate))
    assert set(scan['with']['severity'].split(',')) == {'UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'}
    assert scan['with']['scanners'] == 'vuln,secret' and scan['with']['list-all-pkgs'] == 'true'
    assert scan['with']['exit-code'] == '0'
    receipt = BASE + 'grafana-source-build/build-receipt.json'
    assert build_args == {'--artifact': 'grafana', '--output-dir': BASE + 'grafana-source-build'}
    assert native_args == {'--image': 'deepresearch-grafana-source:20261112',
                           '--build-receipt': receipt, '--output-dir': BASE + 'grafana-native'}
    assert gate_args == {'--build-receipt': receipt, '--scan': 'runtime-image.json',
                         '--grype-scan': BASE + 'grafana-native/full-grype.json',
                         '--sbom': BASE + 'grafana-native/full-syft.json',
                         '--inspect': BASE + 'grafana-source-build/image-inspect.json',
                         '--scanner-exit-code': '0', '--grype-exit-code': '0',
                         '--output': BASE + 'grafana-ci-disposition.json'}
    ordinary = next(step for step in steps if step.get('uses', '').startswith('aquasecurity/trivy-action@')
                    and step.get('id') != 'approved_scan')
    assert "matrix.source_build_profile == ''" in ordinary['if']
    artifacts = next(step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@'))
    assert artifacts['if'] == 'always()'
    assert {receipt, BASE + 'grafana-source-build/image-inspect.json',
            BASE + 'grafana-ci-disposition.json', BASE + 'grafana-native/*.json'}.issubset(
                artifacts['with']['path'].splitlines())


def test_minio_native_repair_requires_new_producer_complete_scans_and_strict_consumer():
    document = yaml.safe_load((ROOT / '.github/workflows/security.yml').read_text(encoding='utf-8'))
    steps = document['jobs']['runtime-image-scan']['steps']
    build_index, build, build_args = command(steps, 'build_minio_os_20261113.py')
    native_index, native, native_args = command(steps, 'collect_minio_os_native_20261113.py')
    gate_index, gate, gate_args = command(steps, 'check_minio_os_20261113.py')
    scan_index = next(i for i, step in enumerate(steps) if step.get('id') == 'approved_scan')
    secret_index, _, _ = command(steps, 'check_image_secrets.py')
    condition = "matrix.source_build_profile == 'minio-os-source-20261113'"
    assert build['if'] == condition
    assert native['if'] == gate['if'] == condition + " && steps.approved_scan.outcome == 'success'"
    assert build_index < scan_index < secret_index < native_index < gate_index
    assert build_args == {'--output-dir': BASE + 'minio-source-build'}
    assert native_args == {'--image': 'deepresearch-minio-source:20261113',
        '--build-receipt': BASE + 'minio-source-build/build-receipt.json',
        '--output-dir': BASE + 'minio-native', '--db-cache': BASE + 'minio-grype-db'}
    assert gate_args == {'--build-receipt': BASE + 'minio-source-build/build-receipt.json',
        '--scan': 'runtime-image.json', '--inspect': BASE + 'minio-source-build/image-inspect.json',
        '--grype-scan': BASE + 'minio-native/full-grype.json', '--sbom': BASE + 'minio-native/full-syft.json',
        '--scanner-exit-code': '0', '--grype-exit-code': '0', '--output': BASE + 'minio-ci-disposition.json'}
    assert not any(step.get('continue-on-error') for step in (build, native, gate))
    assert not any('scripts/check_minio_source_build_20261112.py' in step.get('run', '') for step in steps)
    upload = next(step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@'))
    assert upload['if'] == 'always()'
    assert {BASE + 'minio-native/*.json', BASE + 'minio-source-build/build-receipt.json',
            BASE + 'minio-source-build/image-inspect.json', BASE + 'minio-ci-disposition.json',
            BASE + 'minio-source-build/build-evidence/go-repair/*.json'}.issubset(upload['with']['path'].splitlines())
    for filename in ('compose.yaml', 'deployment/compose/compose.verify.yaml'):
        minio = yaml.safe_load((ROOT / filename).read_bytes())['services']['minio']
        assert minio['image'] == 'deepresearch-minio-source:20261113' and 'build' not in minio


def test_enterprise_and_compose_consume_source_image_without_a_second_uncontrolled_build():
    ci = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8'))
    steps = ci['jobs']['enterprise-compose-smoke']['steps']
    build_index, build, args = command(steps, 'build_go_stdlib_security_20261112.py', artifact='grafana')
    compile_index = next(i for i, step in enumerate(steps)
                         if step.get('run') == 'python scripts/ci_compose_build.py')
    cache_index = next(i for i, step in enumerate(steps)
                       if step.get('run') == 'python scripts/ci_compose_build.py --prune-only')
    start_index = next(i for i, step in enumerate(steps) if step.get('run') ==
                       'docker compose up -d --no-build --force-recreate --wait --wait-timeout 300')
    smoke_index = next(i for i, step in enumerate(steps) if step.get('run') == 'python scripts/smoke-enterprise.py')
    assert cache_index < build_index < compile_index < start_index < smoke_index
    assert steps[cache_index]['if'] == "runner.environment == 'github-hosted'"
    # The helper enforces hosted-runner guards and prunes between completed recipes;
    # behavioral safety/order coverage lives in test_ci_compose_build.py.
    assert (ROOT / 'scripts/ci_compose_build.py').is_file()
    for index in (build_index, compile_index, cache_index, start_index, smoke_index):
        assert not steps[index].get('continue-on-error')
        if index != cache_index:
            assert steps[index].get('if') is None
    assert args == {'--artifact': 'grafana', '--output-dir': BASE + 'grafana-source-build'}
    assert not any('scripts/fetch_grafana_fixed_20261107.py' in step.get('run', '') for step in steps)
    receipt_upload = next(step for step in steps if step.get('with', {}).get('name', '').startswith('enterprise-grafana-source-'))
    assert 'always()' in receipt_upload['if']
    assert set(receipt_upload['with']['path'].splitlines()) == {
        BASE + 'grafana-source-build/build-receipt.json', BASE + 'grafana-source-build/image-inspect.json',
        BASE + 'grafana-source-build/actual-payload/runtime-manifest.json.gz'}
    grafana = yaml.safe_load((ROOT / 'compose.yaml').read_text(encoding='utf-8'))['services']['grafana']
    assert grafana['image'] == 'deepresearch-grafana-source:20261112' and 'build' not in grafana
    assert grafana['x-deepresearch-source-build'] == 'grafana-source-20261112' and grafana['pull_policy'] == 'never'
