import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from scripts import ci_compose_build as ci


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('RUNNER_ENVIRONMENT', 'github-hosted')
    monkeypatch.setenv('RUNNER_OS', 'Linux')


@pytest.mark.parametrize(('key', 'value'), [
    ('GITHUB_ACTIONS', 'false'), ('RUNNER_ENVIRONMENT', 'self-hosted'),
    ('RUNNER_ENVIRONMENT', ''), ('RUNNER_OS', 'Windows'),
])
@pytest.mark.parametrize('operation', [ci.build, ci.prune_cache])
def test_local_or_self_hosted_execution_refused_before_any_command(hosted, monkeypatch, key, value, operation):
    monkeypatch.setenv(key, value)
    calls = []
    monkeypatch.setattr(ci.subprocess, 'run', lambda *a, **kw: calls.append(a))
    with pytest.raises(RuntimeError, match='GitHub-hosted'):
        operation()
    assert calls == []


def test_groups_preserve_alias_tags_distinct_recipes_and_all_targets():
    config = {'services': {
        'agent': {'build': {'context': '.', 'args': {'A': '1'}}},
        'worker': {'build': {'args': {'A': '1'}, 'context': '.'}},
        'different': {'build': {'context': '.', 'args': {'A': '2'}}},
        'reviewed': {'image': 'already-verified'},
    }}
    assert ci.build_groups(config) == [['agent', 'worker'], ['different']]
    with pytest.raises(ValueError):
        ci.build_groups({'services': {'reviewed': {'image': 'already-verified'}}})


def test_actual_compose_targets_are_all_covered_once():
    config = yaml.safe_load((Path(__file__).resolve().parents[1] / 'compose.yaml').read_bytes())
    groups = ci.build_groups(config)
    assert sorted(s for group in groups for s in group) == sorted(
        s for s, spec in config['services'].items() if 'build' in spec)
    assert next(group for group in groups if 'agent' in group) == [
        'agent', 'worker', 'document-worker', 'migrate', 'bootstrap-db', 'alert-relay', 'ops-exporter']


def test_builds_serially_and_prunes_only_unused_cache_between_groups(hosted, monkeypatch, capsys):
    calls = []
    config = {'services': {'one': {'build': '.'}, 'two': {'build': './two'}},
              'secret': 'do-not-log-resolved-credentials'}

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert kwargs['check'] is True
        return SimpleNamespace(stdout=json.dumps(config))

    monkeypatch.setattr(ci.subprocess, 'run', run)
    ci.build()
    commands = [command for command, _ in calls]
    builds = [i for i, command in enumerate(commands) if 'build' in command]
    prunes = [i for i, command in enumerate(commands) if 'prune' in command]
    assert prunes[0] < builds[0] < prunes[1] < builds[1] < prunes[2]
    assert len(prunes) == 3
    for i in prunes:
        assert commands[i] == ['docker', 'builder', 'prune', '--all', '--force']
    for i, service in zip(builds, ['one', 'two'], strict=True):
        assert commands[i] == ['docker', 'compose', '--parallel', '1', 'build', service]
        assert calls[i][1]['env']['COMPOSE_BAKE'] == 'false'
    assert 'do-not-log' not in capsys.readouterr().out


def test_failed_build_is_not_pruned_or_followed_by_other_builds(hosted, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if 'build' in command:
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(stdout=json.dumps({'services': {
            'one': {'build': '.'}, 'two': {'build': './two'}}}))

    monkeypatch.setattr(ci.subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        ci.build()
    assert calls[-1] == ['docker', 'compose', '--parallel', '1', 'build', 'one']
    assert sum('prune' in command for command in calls) == 1


def test_workflow_keeps_current_source_verifiers_and_full_smoke_gates():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / '.github/workflows/ci.yml').read_bytes())
    steps = workflow['jobs']['enterprise-compose-smoke']['steps']
    source = [i for i, step in enumerate(steps)
              if step.get('uses') == './.github/actions/reviewed-source-image']
    prune = next(i for i, step in enumerate(steps)
                 if step.get('run') == 'python scripts/ci_compose_build.py --prune-only')
    build = next(i for i, step in enumerate(steps)
                 if step.get('run') == 'python scripts/ci_compose_build.py')
    collector = next(i for i, step in enumerate(steps)
                     if 'validate --config=' in step.get('run', ''))
    startup = next(i for i, step in enumerate(steps)
                   if step.get('run') == 'docker compose up -d --no-build --force-recreate --wait --wait-timeout 300')
    assert source[0] < prune < source[1] < build < collector < startup
    assert steps[prune]['if'] == "runner.environment == 'github-hosted'"
    assert not any(step.get('continue-on-error') for step in steps)
    runs = [step.get('run') for step in steps]
    assert 'docker compose build' not in runs
    assert 'python scripts/smoke-enterprise.py' in runs
    assert 'npm run test:e2e:enterprise' in runs
    assert 'curl --fail --retry 15 --retry-connrefused http://127.0.0.1:8080/readyz' in runs


def test_review_bytes_survive_autocrlf_checkout_and_missing_rules_fail(tmp_path):
    from scripts import build_codeql_ci_disk_review_20261114 as renewal
    root = Path(__file__).resolve().parents[1]
    renewal.verify_attributes(root)
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'config', 'core.autocrlf', 'true'], check=True)
    (tmp_path / '.gitattributes').write_bytes((root / '.gitattributes').read_bytes())
    paths = [line.split()[0] for line in renewal.ATTRIBUTE_RULES]
    for name in paths:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.gitattributes', *paths], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'checkout-index', '--all', '--force'], check=True)
    for name in paths:
        assert (tmp_path / name).read_bytes() == (root / name).read_bytes()
    rules = (tmp_path / '.gitattributes').read_text()
    for changed in (rules.replace(renewal.ATTRIBUTE_RULES[-1], ''),
                    rules.replace(renewal.ATTRIBUTE_RULES[-1], 'tests/test_fs_workflow_gate.py text eol=crlf')):
        (tmp_path / '.gitattributes').write_text(changed)
        with pytest.raises(ValueError, match='missing or changed'):
            renewal.verify_attributes(tmp_path)
