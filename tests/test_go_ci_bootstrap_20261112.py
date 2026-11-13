from copy import deepcopy
from pathlib import Path
import shlex

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ('scripts/build_go_stdlib_security_20261112.py', 'scripts/build_minio_os_20261113.py')
CACHE_ACTION = './.github/actions/reviewed-source-image'
JOBS = [('ci.yml', 'real-components'), ('ci.yml', 'enterprise-compose-smoke'),
        ('security.yml', 'runtime-image-scan'), ('rag-quality.yml', 'live-reranker-evaluation')]


def steps_for(workflow, job):
    return yaml.safe_load((ROOT / '.github/workflows' / workflow).read_text(encoding='utf-8'))['jobs'][job]['steps']


def assert_locked_bootstrap(steps):
    builders = [(i, step) for i, step in enumerate(steps)
                if step.get('uses') == CACHE_ACTION or any(script in step.get('run', '') for script in SCRIPTS)]
    assert builders
    for index, builder in builders:
        if builder.get('uses') == CACHE_ACTION:
            assert 'run' not in builder and builder['with']['artifact'] in {'minio', 'grafana'}
            action = yaml.safe_load((ROOT / '.github/actions/reviewed-source-image/action.yml').read_bytes())
            for step in action['runs']['steps']:
                if 'run' in step:
                    assert shlex.split(step['run'])[:5] == ['uv', 'run', '--frozen', '--no-dev', 'python']
        else:
            tokens = shlex.split(builder['run'])
            assert tokens[:5] == ['uv', 'run', '--frozen', '--no-dev', 'python'] and tokens[5] in SCRIPTS
            assert '--prepare' not in tokens
            if tokens[5] == SCRIPTS[0]:
                assert tokens[tokens.index('--artifact') + 1] in {'grafana', '$ARTIFACT'}
        assert not builder.get('continue-on-error')
        setup = [(i, step) for i, step in enumerate(steps)
                 if step.get('uses', '').startswith('astral-sh/setup-uv@')]
        sync = [(i, step) for i, step in enumerate(steps)
                if step.get('run') == 'uv sync --frozen --no-dev']
        assert len(setup) == len(sync) == 1
        assert setup[0][0] < sync[0][0] < index
        assert setup[0][1].get('if') == sync[0][1].get('if')
        if builder.get('if'):
            assert builder['if'] in {"matrix.source_build_profile == 'grafana-source-20261112'",
                                     "matrix.source_build_profile == 'minio-os-source-20261113'"}
            assert setup[0][1]['if'] == ("matrix.source_build_profile == 'grafana-source-20261112'"
                                        " || matrix.source_build_profile == 'minio-os-source-20261113'")
        else:
            assert setup[0][1].get('if') is None
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
    builder = next(step for step in steps if step.get('uses') == CACHE_ACTION)
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
        builder['run'] = 'python scripts/ci_reviewed_image_cache.py run'
    else:
        for step in steps:
            if '*.log' in step.get('with', {}).get('path', ''):
                step['if'] = 'success()'
    with pytest.raises(AssertionError):
        assert_locked_bootstrap(steps)
