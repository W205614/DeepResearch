from copy import deepcopy
from pathlib import Path
import shlex

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = 'scripts/build_go_stdlib_security_20261112.py'
JOBS = [('ci.yml', 'real-components'), ('ci.yml', 'enterprise-compose-smoke'),
        ('security.yml', 'runtime-image-scan'), ('rag-quality.yml', 'live-reranker-evaluation')]


def steps_for(workflow, job):
    return yaml.safe_load((ROOT / '.github/workflows' / workflow).read_text(encoding='utf-8'))['jobs'][job]['steps']


def assert_locked_bootstrap(steps):
    builders = [(i, step) for i, step in enumerate(steps) if SCRIPT in step.get('run', '')]
    assert builders
    for index, builder in builders:
        assert shlex.split(builder['run'])[:6] == ['uv', 'run', '--frozen', '--no-dev', 'python', SCRIPT]
        assert not builder.get('continue-on-error')
        setup = [(i, step) for i, step in enumerate(steps)
                 if step.get('uses', '').startswith('astral-sh/setup-uv@')]
        sync = [(i, step) for i, step in enumerate(steps)
                if step.get('run') == 'uv sync --frozen --no-dev']
        assert len(setup) == len(sync) == 1
        assert setup[0][0] < sync[0][0] < index
        assert setup[0][1].get('if') == sync[0][1].get('if') == builder.get('if')
        uploads = [step for i, step in enumerate(steps) if i > index
                   and step.get('uses', '').startswith('actions/upload-artifact@')
                   and step.get('if') == 'always()'
                   and '.cache/eval/*-source-build/*.log' in step.get('with', {}).get('path', '').splitlines()
                   and '.cache/eval/*-source-build/*.command.json' in step.get('with', {}).get('path', '').splitlines()]
        assert uploads


@pytest.mark.parametrize(('workflow', 'job'), JOBS)
def test_every_go_source_job_bootstraps_locked_python_before_the_actual_builder(workflow, job):
    assert_locked_bootstrap(steps_for(workflow, job))


@pytest.mark.parametrize('mutation', ['missing-setup', 'late-sync', 'unlocked-sync', 'bare-python', 'no-failed-logs'])
def test_missing_or_unlocked_bootstrap_and_discarded_failure_logs_are_rejected(mutation):
    steps = deepcopy(steps_for('ci.yml', 'real-components'))
    builder = next(step for step in steps if SCRIPT in step.get('run', ''))
    setup = next(step for step in steps if step.get('uses', '').startswith('astral-sh/setup-uv@'))
    sync = next(step for step in steps if step.get('run') == 'uv sync --frozen --no-dev')
    if mutation == 'missing-setup':
        steps.remove(setup)
    elif mutation == 'late-sync':
        steps.remove(sync)
        steps.append(sync)
    elif mutation == 'unlocked-sync':
        sync['run'] = 'uv sync --no-dev'
    elif mutation == 'bare-python':
        builder['run'] = builder['run'].removeprefix('uv run --frozen --no-dev ')
    else:
        for step in steps:
            if '*.log' in step.get('with', {}).get('path', ''):
                step['if'] = 'success()'
    with pytest.raises(AssertionError):
        assert_locked_bootstrap(steps)
