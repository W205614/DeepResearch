from copy import deepcopy
from pathlib import Path
import shlex

import pytest
import yaml

BASE = '.cache/eval/'
SOURCE_READY = "always() && steps.source_scan.outcome == 'success'"


def source_job_steps(document):
    job = document['jobs']['dependency-and-image-scan']
    assert job.get('if') is None and not job.get('continue-on-error')
    return job['steps']


def source_steps():
    source = Path(__file__).parents[1] / '.github/workflows/security.yml'
    return source_job_steps(yaml.safe_load(source.read_text(encoding='utf-8')))


def command(steps, script):
    found = [(index, step) for index, step in enumerate(steps) if 'scripts/' + script in step.get('run', '')]
    assert len(found) == 1, 'The source producer or required technical gate is missing or duplicated'
    index, step = found[0]
    run = step['run']
    assert not any(token in run for token in ('||', '&&', ';', '|', '`'))
    tokens = shlex.split(run)
    assert tokens[:2] == ['python', 'scripts/' + script] and len(tokens[2:]) % 2 == 0
    arguments = dict(zip(tokens[2::2], tokens[3::2]))
    assert len(arguments) == len(tokens[2:]) // 2
    return index, step, arguments


def assert_source_gate_contract(steps):
    producers = [i for i, step in enumerate(steps) if 'scripts/run_fs_source_scan.py' in step.get('run', '')]
    assert len(producers) == 1
    producer_index = producers[0]
    producer = steps[producer_index]
    assert producer['id'] == 'source_scan'
    assert producer.get('if') is None and not producer.get('continue-on-error')
    old_index, old, old_args = command(steps, 'check_fs_source_applicability.py')
    new_index, new, new_args = command(steps, 'check_grafana_candidate_source_exact2.py')
    inventory_index, current, current_args = command(steps, 'runtime_ci_inventory.py')
    final_index, final, final_args = command(steps, 'check_grafana_active_source_retirement.py')
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
    assert final_args == raw | {'--original-verdict': BASE + 'source-fs-original-verdict.json',
        '--candidate-verdict': BASE + 'source-fs-candidate-verdict.json',
        '--runtime-inventory': BASE + 'runtime-image-inventory.json', '--output': BASE + 'source-fs-verdict.json'}
    setup = next(step for step in steps if step.get('uses', '').startswith('aquasecurity/setup-trivy@'))
    assert setup['uses'] == 'aquasecurity/setup-trivy@3fb12ec12f41e471780db15c232d5dd185dcb514'
    assert setup['with']['version'] == 'v0.75.0'


def test_raw_source_scan_completion_cannot_replace_the_independent_technical_gate():
    assert_source_gate_contract(source_steps())


@pytest.mark.parametrize('mutation', ['missing_final', 'disabled_final', 'ignored_final', 'conditional_skip',
    'shell_bypass', 'duplicate_final', 'wrong_order', 'ignored_producer', 'disabled_producer',
    'duplicate_producer', 'overwritten_old', 'missing_candidate_input'])
def test_missing_disabled_or_bypassed_final_gate_breaks_the_workflow_contract(mutation):
    steps = deepcopy(source_steps())
    final = next(step for step in steps if 'scripts/check_grafana_active_source_retirement.py' in step.get('run', ''))
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
    else:
        final['run'] = final['run'].replace('--candidate-verdict ' + BASE + 'source-fs-candidate-verdict.json', '')
    with pytest.raises(AssertionError):
        assert_source_gate_contract(steps)


def test_raw_source_evidence_and_both_intermediate_failures_survive_final_rejection():
    steps = source_steps()
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
