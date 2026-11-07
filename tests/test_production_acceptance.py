import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import production_acceptance as acceptance


def test_prepare_is_offline_does_not_read_private_env_and_preserves_fixture(tmp_path, monkeypatch):
    private = tmp_path / '.env'
    private.write_text('OPENAI_API_KEY=private-sentinel-never-read\n', encoding='utf-8')
    def forbidden(*args, **kwargs):
        raise AssertionError('prepare must not run Docker, Git or any child command')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    args = acceptance.parser().parse_args(['prepare'])
    first = acceptance.execute(args, root=tmp_path)
    second = acceptance.execute(args, root=tmp_path)
    assert first == second and first['docker_commands_executed'] == first['supplier_http_requests'] == 0
    content = (tmp_path / acceptance.ENV_NAME).read_text(encoding='utf-8')
    assert content.startswith(acceptance.MARKER) and 'private-sentinel' not in content
    assert 'ALLOW_INTERNAL_MODEL_PROCESSING=true' in content
    assert 'LLM_BASE_URL=http://fixture.invalid/v1' in content
    assert private.read_text(encoding='utf-8') == 'OPENAI_API_KEY=private-sentinel-never-read\n'


def test_prepare_refuses_unmanaged_fixture_without_overwriting(tmp_path):
    fixture = tmp_path / acceptance.ENV_NAME
    fixture.write_text('# existing operator-owned file\nKEEP=original\n', encoding='utf-8')
    before = fixture.read_bytes()
    with pytest.raises(ValueError, match='unmanaged'):
        acceptance.prepare_environment(tmp_path)
    assert fixture.read_bytes() == before


def test_child_environment_cannot_override_fixture_with_real_credentials(monkeypatch):
    for key in ('LLM_API_KEY', 'OPENAI_API_KEY', 'AWS_ACCESS_KEY_ID', 'DATABASE_URL', 'COMPOSE_FILE', 'COMPOSE_PROFILES'):
        monkeypatch.setenv(key, 'private-sentinel')
    monkeypatch.setenv('PATH', 'tooling-path')
    monkeypatch.setenv('ProgramFiles', 'docker-plugin-search-root')
    environment = acceptance.child_environment()
    assert environment['PATH'] == 'tooling-path'
    assert {key.upper(): value for key, value in environment.items()}['PROGRAMFILES'] == 'docker-plugin-search-root'
    assert 'private-sentinel' not in environment.values()


def test_commands_pin_the_isolated_project_and_exact_four_overlays(tmp_path):
    command = acceptance.compose_command(tmp_path)
    assert command[command.index('-p') + 1] == 'dr-production-test'
    assert Path(command[command.index('--env-file') + 1]).name == '.env.production-test'
    assert [command[index + 1] for index, part in enumerate(command) if part == '-f'] == [
        str(tmp_path / file) for file in acceptance.COMPOSE_FILES]
    assert len(acceptance.COMPOSE_FILES) == 4


def fixture_config():
    fixture = dict(LLM_API_KEY='fixture-no-external-calls', EMBEDDING_API_KEY='fixture-no-external-calls',
                   LLM_BASE_URL='http://fixture.invalid/v1', EMBEDDING_BASE_URL='http://fixture.invalid/v1',
                   ALLOW_INTERNAL_MODEL_PROCESSING='true', INTEGRATION='1', RERANKER_ENABLED='false')
    services = {role: {'environment': fixture.copy(), 'deploy': {'replicas': 2}} for role in acceptance.ACTIVE_SERVICES}
    services['acceptance'] = {'environment': fixture.copy()}
    return {'name': acceptance.PROJECT, 'services': services,
            'volumes': {'postgres-data': {'name': 'dr-production-test_postgres-data'}},
            'networks': {'database': {'name': 'dr-production-test_database'}}}


def test_fixture_validation_rejects_real_provider_or_daily_volume():
    config = fixture_config()
    acceptance.validate_configuration(config)
    config['services']['agent']['environment']['LLM_BASE_URL'] = 'https://api.openai.com/v1'
    with pytest.raises(ValueError, match='non-paid'):
        acceptance.validate_configuration(config)
    config = fixture_config()
    config['volumes']['postgres-data']['name'] = 'deepresearch_postgres-data'
    with pytest.raises(ValueError, match='project-owned'):
        acceptance.validate_configuration(config)
    config = fixture_config()
    config['services']['backend']['environment']['ALLOW_INTERNAL_MODEL_PROCESSING'] = 'false'
    with pytest.raises(ValueError, match='Java fixture'):
        acceptance.validate_configuration(config)


def test_image_reuse_requires_explicit_baseline_before_runner_or_docker(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid invocation must fail before constructing a runner')
    monkeypatch.setattr(acceptance, 'Runner', forbidden)
    args = acceptance.parser().parse_args(['smoke', '--isolated', '--reuse-images'])
    with pytest.raises(ValueError, match='image-baseline'):
        acceptance.execute(args, root=tmp_path)


def test_isolated_help_entrypoint_from_other_directory_has_no_runtime_side_effect(tmp_path):
    script = Path(acceptance.__file__).resolve()
    result = subprocess.run([sys.executable, '-I', str(script), '--help'], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0 and '--reuse-images' in result.stdout
    assert not list(tmp_path.iterdir())
    assert 'private-sentinel' not in json.dumps({'stdout': result.stdout, 'stderr': result.stderr})
