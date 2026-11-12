"""The new producer preserves official metadata and the complete frozen capture."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deployment/vendors/grafana-20261107'))
SPEC = importlib.util.spec_from_file_location(
    'stable_grafana_producer', ROOT / 'deployment/vendors/grafana-20261110/prepare_stable_graphs_20261112.py')
producer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(producer)
ROW = json.loads((ROOT / 'deployment/vendors/grafana-20261107/plugin-modules.json').read_bytes())[0]


def test_inventory_is_exactly_the_thirteen_pinned_plugins():
    assert len(producer.inventory(ROOT / 'deployment/vendors/grafana-20261107/plugin-modules.json')) == 13


@pytest.mark.parametrize('field,value', [('module', '../escape'), ('version', '../../escape'), ('commit', 'other')])
def test_invalid_module_identity_fails_before_transport(tmp_path, field, value):
    rows = json.loads((ROOT / 'deployment/vendors/grafana-20261107/plugin-modules.json').read_bytes())
    rows[0][field] = value
    path = tmp_path / 'inventory.json'
    path.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match='pinned module identity'):
        producer.inventory(path)


def download_fixture(tmp_path):
    cache, evidence = tmp_path / 'cache', tmp_path / 'evidence'
    cache.mkdir()
    evidence.mkdir()
    info = cache / 'actual.info'
    info.write_bytes(b'{"Version":"actual-go-output"}\n')
    result = {'Path': ROW['module'], 'Version': ROW['version'], 'Sum': ROW['source_sum'],
              'GoModSum': ROW['source_mod_sum'], 'Info': str(info),
              'Origin': {'VCS': 'git', 'Hash': ROW['commit'], 'URL': 'https://' + ROW['module']}}
    return cache, evidence, result


@pytest.mark.parametrize('mutation', ['sum', 'modsum', 'commit', 'url', 'path', 'version', 'outside-info'])
def test_official_download_rejects_changed_authoritative_identity(tmp_path, monkeypatch, mutation):
    cache, evidence, result = download_fixture(tmp_path)
    if mutation == 'commit':
        result['Origin']['Hash'] = '0' * 40
    elif mutation == 'url':
        result['Origin']['URL'] = 'https://example.invalid/unreviewed'
    elif mutation == 'outside-info':
        outside = tmp_path / 'outside.info'
        outside.write_bytes(b'{}')
        result['Info'] = str(outside)
    else:
        result[{'sum': 'Sum', 'modsum': 'GoModSum', 'path': 'Path', 'version': 'Version'}[mutation]] = 'changed'
    monkeypatch.setattr(producer.subprocess, 'check_output', lambda *a, **k: b'sum.golang.org\n')

    def run(command, **kwargs):
        kwargs['stdout'].write(json.dumps(result).encode())

    monkeypatch.setattr(producer.subprocess, 'run', run)
    with pytest.raises(ValueError):
        producer.download(ROW, cache, evidence, 'download')


def test_standard_download_preserves_entire_go_response_and_enables_sumdb(tmp_path, monkeypatch):
    cache, evidence, result = download_fixture(tmp_path)
    original = json.dumps(result, indent=2).encode() + b'\n'
    calls = []
    monkeypatch.setattr(producer.subprocess, 'check_output', lambda *a, **k: b'sum.golang.org\n')

    def run(command, **kwargs):
        calls.append((command, kwargs))
        kwargs['stdout'].write(original)

    monkeypatch.setattr(producer.subprocess, 'run', run)
    assert producer.download(ROW, cache, evidence, 'download') == result
    assert (evidence / 'download.json').read_bytes() == original
    assert calls[0][0] == ['go', 'mod', 'download', '-json', ROW['module'] + '@' + ROW['version']]
    assert calls[0][1]['env']['GOPROXY'] == 'https://proxy.golang.org'
    assert calls[0][1]['check'] is True


def test_disabled_checksum_database_fails_before_download(tmp_path, monkeypatch):
    cache, evidence, _ = download_fixture(tmp_path)
    monkeypatch.setattr(producer.subprocess, 'check_output', lambda *a, **k: b'off\n')
    with pytest.raises(ValueError, match='checksum database'):
        producer.download(ROW, cache, evidence, 'download')


def test_shared_recheck_keeps_pinned_prefetched_transport(tmp_path, monkeypatch):
    cache, evidence, result = download_fixture(tmp_path)
    proxy = 'file:///prefetched-proxy,https://proxy.golang.org'
    monkeypatch.setenv('GOPROXY', proxy)
    monkeypatch.setattr(producer.subprocess, 'check_output', lambda *a, **k: b'sum.golang.org\n')
    observed = []

    def run(command, **kwargs):
        observed.append(kwargs['env']['GOPROXY'])
        kwargs['stdout'].write(json.dumps(result).encode())

    monkeypatch.setattr(producer.subprocess, 'run', run)
    producer.download(ROW, cache, evidence, 'shared', official=False)
    assert observed == [proxy]


@pytest.mark.parametrize('plugin', [True, False])
def test_export_uses_original_tags_environment_and_unmodified_capture(tmp_path, monkeypatch, plugin):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'go.mod').write_bytes(b'module example.invalid/pinned\n')
    (source / 'go.sum').write_bytes(b'actual-lock\n')
    observed = []
    monkeypatch.setattr(producer.subprocess, 'run', lambda command, **kwargs: observed.append((command, kwargs)))
    captures = []
    monkeypatch.setattr(producer, 'capture', lambda *a, **kw: captures.append((a, kw)))
    before = producer.locks(source, [])
    producer.prime_capture(source, './pkg', 'test', tmp_path / 'graphs', tmp_path / 'evidence',
                           plugin=plugin, nested=[])
    assert producer.locks(source, []) == before
    expected = ['go', 'list', '-deps', '-export', '-mod=readonly']
    if plugin:
        expected.append('-tags=arrow_json_stdlib')
        assert observed[0][1]['env']['GOWORK'] == 'off'
    assert observed[0][0] == expected + ['./pkg']
    assert captures == [(('go', source, './pkg', 'test', tmp_path / 'graphs'),
                         {'gowork_off': plugin, 'build_tags': 'arrow_json_stdlib' if plugin else None,
                          'nested_modules': []})]


def test_readonly_export_cannot_change_nested_lock_or_reach_capture(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    (source / 'nested').mkdir(parents=True)
    lock = source / 'nested/go.sum'
    lock.write_bytes(b'original')
    calls = []
    monkeypatch.setattr(producer.subprocess, 'run', lambda *a, **kw: lock.write_bytes(b'changed'))
    monkeypatch.setattr(producer, 'capture', lambda *a, **kw: calls.append(a))
    with pytest.raises(ValueError, match='changed source lock'):
        producer.prime_capture(source, './pkg', 'test', tmp_path / 'graphs', tmp_path / 'evidence',
                               plugin=True, nested=['nested'])
    assert calls == []


def test_standard_tool_failure_is_not_relabelled_as_success(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(producer.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        producer.prime_capture(tmp_path, './pkg', 'test', tmp_path / 'graphs', tmp_path / 'evidence',
                               plugin=False, nested=[])


def test_recipe_keeps_prep_logs_external_and_frozen_producer_source_unchanged():
    recipe = (ROOT / 'deployment/docker/Dockerfile.grafana-20261112').read_text()
    assert 'prepare_stable_graphs_20261112.py metadata' in recipe
    assert 'FROM scratch AS stable-graph-evidence' in recipe
    assert '/external-evidence/' not in recipe.split(' AS source-runtime\n', 1)[1]
    helper = ROOT / 'deployment/vendors/grafana-20261107/capture_graphs.py'
    assert helper.read_bytes() == subprocess.check_output(['git', 'show', 'HEAD:' + helper.relative_to(ROOT).as_posix()], cwd=ROOT)
