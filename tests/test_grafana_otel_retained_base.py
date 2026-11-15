"""Cold Grafana inputs must reproduce the frozen source rather than current locks."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from scripts import build_go_otel_security_20261114 as builder


def test_retained_revision_contains_the_complete_frozen_build_inputs():
    profile = builder.common.prior_profile(builder.ROOT)
    archive = subprocess.check_output([
        'git', 'archive', '--format=zip', builder.RETAINED_20261112_SOURCE,
        '--', *profile['source_inputs'],
    ], cwd=builder.ROOT)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        for name, expected in profile['source_inputs'].items():
            assert hashlib.sha256(source.read(name)).hexdigest() == expected
        script = source.read('scripts/build_go_stdlib_security_20261112.py')
        assert b'build_historical_grafana_input(output, snapshot(ROOT))' in script


@pytest.mark.parametrize('failed', [False, True])
def test_retained_build_uses_clean_ancestor_and_keeps_failure_diagnostics(tmp_path, monkeypatch, failed):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path)

    git('init', '-q')
    git('config', 'core.autocrlf', 'false')
    (tmp_path / '.gitignore').write_text('.cache/\n')
    (tmp_path / 'frozen-input').write_bytes(b'exact source\n')
    git('add', '.gitignore', 'frozen-input')
    git('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')
    revision = git('rev-parse', 'HEAD').decode().strip()
    monkeypatch.setattr(builder, 'ROOT', tmp_path)
    monkeypatch.setattr(builder, 'RETAINED_20261112_SOURCE', revision)
    monkeypatch.setattr(builder.common, 'prior_profile', lambda root: {
        'source_inputs': {'frozen-input': hashlib.sha256(b'exact source\n').hexdigest()},
    })
    calls = []

    def logged(command, output, name):
        calls.append(command)
        checkout = tmp_path / '.cache/retained-20261112-build-source'
        assert command[2] == checkout / 'scripts/build_go_stdlib_security_20261112.py'
        assert command[3:6] == ['--artifact', 'grafana', '--prepare']
        evidence = Path(command[-1])
        assert evidence.is_relative_to(checkout / '.cache')
        evidence.mkdir(parents=True)
        (evidence / 'build.log').write_bytes(b'actual compiler output\n')
        (evidence / 'large.tar').write_bytes(b'not copied')
        if failed:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(builder, 'logged', logged)
    output = tmp_path / '.cache/evidence'
    output.mkdir(parents=True)
    if failed:
        with pytest.raises(subprocess.CalledProcessError):
            builder.rebuild_retained_20261112_base(output)
    else:
        builder.rebuild_retained_20261112_base(output)
    record = json.loads((output / 'retained-20261112-build/historical-source.json').read_bytes())
    assert record['source_commit'] == revision
    assert record['input_only'] and record['new_runtime_approved'] is False
    assert (output / 'retained-20261112-build/build.log').read_bytes() == b'actual compiler output\n'
    assert not (output / 'retained-20261112-build/large.tar').exists()
    checkout = tmp_path / '.cache/retained-20261112-build-source'
    (checkout / 'frozen-input').write_bytes(b'unreviewed change\n')
    with pytest.raises(ValueError, match='checkout changed'):
        builder.rebuild_retained_20261112_base(output)
    assert len(calls) == 1
