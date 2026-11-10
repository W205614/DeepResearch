"""Startup cannot rebuild, fetch, or reuse an unverified Grafana implicitly."""
import json
import pytest

from scripts import build_grafana_source_20261110 as builder


def forbid_subprocess(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('A failed precondition must not invoke Docker, Git, or a network loader')
    monkeypatch.setattr(builder.subprocess, 'run', forbidden)
    monkeypatch.setattr(builder.subprocess, 'check_output', forbidden)


def test_preparation_cannot_authorize_no_build(tmp_path, monkeypatch, capsys):
    forbid_subprocess(monkeypatch)
    output = tmp_path / 'never-created'
    assert builder.main(['--prepare', '--no-build', '--output-dir', str(output)]) == 1
    assert not output.exists()
    assert json.loads(capsys.readouterr().out)['passed'] is False


def test_missing_review_stops_before_fetch_or_start(tmp_path, monkeypatch, capsys):
    forbid_subprocess(monkeypatch)
    def missing():
        raise FileNotFoundError('No verified new source profile')
    monkeypatch.setattr(builder.gate, 'load_profile', missing)
    output = tmp_path / 'never-created'
    assert builder.main(['--no-build', '--output-dir', str(output)]) == 1
    assert not output.exists()
    assert json.loads(capsys.readouterr().out)['error_type'] == 'FileNotFoundError'


def test_build_evidence_cannot_escape_project_cache(tmp_path, monkeypatch, capsys):
    forbid_subprocess(monkeypatch)
    output = tmp_path / 'outside-project'
    assert builder.main(['--prepare', '--output-dir', str(output)]) == 1
    assert not output.exists()
    assert json.loads(capsys.readouterr().out)['passed'] is False


def test_no_build_rejects_changed_source_before_docker(tmp_path, monkeypatch):
    forbid_subprocess(monkeypatch)
    monkeypatch.setattr(builder, 'source_inputs', lambda: {'recipe': 'changed'})
    with pytest.raises(ValueError, match='Current build inputs differ'):
        builder.verify_existing(tmp_path, {'source_inputs': {'recipe': 'reviewed'}})


def test_cached_retained_input_cannot_bypass_live_publication(tmp_path, monkeypatch):
    from scripts import fetch_grafana_candidate as transport
    binding = {'index_digest': 'sha256:index', 'platform_manifest_digest': 'sha256:manifest',
               'config_digest': 'sha256:config', 'diff_ids': ['sha256:layer']}
    monkeypatch.setattr(transport, 'authorized_request', lambda *a, **k: {'binding': binding})
    monkeypatch.setattr(transport, 'inspect', lambda name: {'Id': 'sha256:index', 'Os': 'linux',
        'Architecture': 'amd64', 'RootFS': {'Layers': ['sha256:layer']}})
    monkeypatch.setattr(transport, 'load_transport', lambda *a, **k: {})
    def unpublished(*args, **kwargs):
        raise ValueError('Live release is not published')
    monkeypatch.setattr(transport, 'verify_publication', unpublished)
    with pytest.raises(ValueError, match='Live release is not published'):
        builder.reuse_exact_retained_input(tmp_path)
    assert not (tmp_path / 'retained-input.json').exists()


def test_cached_image_with_wrong_layers_is_not_reused(tmp_path, monkeypatch):
    from scripts import fetch_grafana_candidate as transport
    binding = {'index_digest': 'sha256:index', 'platform_manifest_digest': 'sha256:manifest',
               'config_digest': 'sha256:config', 'diff_ids': ['sha256:reviewed']}
    monkeypatch.setattr(transport, 'authorized_request', lambda *a, **k: {'binding': binding})
    monkeypatch.setattr(transport, 'inspect', lambda name: {'Id': 'sha256:index', 'Os': 'linux',
        'Architecture': 'amd64', 'RootFS': {'Layers': ['sha256:changed']}})
    monkeypatch.setattr(transport, 'verify_publication', lambda *a, **k: pytest.fail('Unbound cached image must not be used'))
    assert builder.reuse_exact_retained_input(tmp_path) is False
    assert not (tmp_path / 'retained-input.json').exists()
