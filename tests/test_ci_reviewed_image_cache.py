import gzip
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from scripts import ci_reviewed_image_cache as cache


def test_exact_key_binds_all_compilation_inputs_helper_and_platform_but_not_app_only_profile():
    key = cache.cache_key('grafana', {'source': 'a', 'lock': 'b'}, b'helper', b'action')
    assert key == cache.cache_key('grafana', {'lock': 'b', 'source': 'a'}, b'helper', b'action')
    assert 'grafana-linux-amd64' in key
    for args in [('grafana', {'source': 'changed', 'lock': 'b'}, b'helper', b'action'),
                 ('grafana', {'source': 'a', 'lock': 'b'}, b'changed', b'action'),
                 ('grafana', {'source': 'a', 'lock': 'b'}, b'helper', b'changed'),
                 ('minio', {'source': 'a', 'lock': 'b'}, b'helper', b'action')]:
        assert key != cache.cache_key(*args)


@pytest.mark.parametrize('artifact', ['minio', 'grafana'])
def test_cold_and_hit_always_use_the_same_original_strict_builder(artifact, tmp_path):
    cold = cache.builder_command(artifact, tmp_path, hit=False)
    hit = cache.builder_command(artifact, tmp_path, hit=True)
    assert hit == cold + ['--no-build']
    assert cold[0] == cache.sys.executable and '--prepare' not in cold
    assert Path(cold[2]).name == ('build_minio_os_20261113.py' if artifact == 'minio'
                                  else 'build_go_stdlib_security_20261112.py')


def test_failed_cache_load_does_not_fall_back_to_a_cold_build(tmp_path, monkeypatch):
    archive = tmp_path / 'image.tar.gz'
    archive.write_bytes(b'corrupt')
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(cache.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        cache.execute('minio', tmp_path / 'new-build', archive, hit=True)
    assert len(calls) == 1 and calls[0][:3] == ['docker', 'image', 'load']


def test_hit_runs_current_builder_before_accepting_receipt_and_preserves_old_source_label(tmp_path, monkeypatch):
    archive = tmp_path / 'image.tar.gz'
    archive.write_bytes(b'restored-archive')
    output = tmp_path / 'new-build'
    calls = []
    inputs = {'source': 'actual'}

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 2:
            output.mkdir()
            receipt = {'preparation_only': False, 'image_id': 'actual-image', 'config_digest': 'portable',
                       'diff_ids': ['actual-layer'], 'source_inputs': inputs}
            inspection = {'Id': 'actual-image', 'Os': 'linux', 'Architecture': 'amd64',
                          'Config': {'Labels': {'org.opencontainers.image.revision': 'original-source'}}}
            (output / 'build-receipt.json').write_text(json.dumps(receipt))
            (output / 'image-inspect.json').write_text(json.dumps(inspection))

    monkeypatch.setattr(cache.subprocess, 'run', run)
    monkeypatch.setattr(cache, 'source_inputs', lambda artifact: inputs)
    cache.execute('minio', output, archive, hit=True)
    assert calls[1] == cache.builder_command('minio', output, hit=True)
    result = json.loads((tmp_path / 'new-build-cache.json').read_bytes())
    assert result['source_revision_label_preserved'] == 'original-source'
    assert result['new_native_compilation_claimed'] is False


def test_pack_contains_only_completed_image_bytes(tmp_path):
    output = tmp_path / 'verified'
    (output / 'actual-payload').mkdir(parents=True)
    (output / 'actual-payload/image.tar').write_bytes(b'closed verified image bytes')
    (output / 'private.env').write_bytes(b'not cached')
    archive = tmp_path / 'image.tar.gz'
    cache.pack(output, archive)
    assert gzip.decompress(archive.read_bytes()) == b'closed verified image bytes'


def test_action_has_exact_restore_and_only_successful_main_push_cache_writes():
    root = Path(__file__).resolve().parents[1]
    action = yaml.safe_load((root / cache.ACTION).read_bytes())
    steps = action['runs']['steps']
    restore = next(s for s in steps if s.get('id') == 'cache')
    assert set(restore['with']) == {'path', 'key'}
    for step in steps:
        assert not step.get('continue-on-error')
        if step.get('uses', '').startswith('actions/cache/save@') or ' pack ' in step.get('run', ''):
            assert step['if'] == ("steps.cache.outputs.cache-hit != 'true' && "
                                  "github.event_name == 'push' && github.ref == 'refs/heads/main'")
    calls = [s for name in ('ci.yml', 'security.yml')
             for job in yaml.safe_load((root / '.github/workflows' / name).read_bytes())['jobs'].values()
             for s in job['steps'] if s.get('uses') == './.github/actions/reviewed-source-image']
    assert [s['with']['artifact'] for s in calls].count('minio') == 3
    assert [s['with']['artifact'] for s in calls].count('grafana') == 2
