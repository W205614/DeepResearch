"""Historical build/source inputs stay exact while current scans keep current identity."""
import json
from pathlib import Path
import subprocess

import pytest

from scripts import build_go_stdlib_security_20261112 as builder
from scripts import check_grafana_source_build_20261110 as old_build
from scripts import check_grafana_source_scan_20261110 as source
from scripts.retained_source_review_20261112 import SOURCE, snapshot


@pytest.mark.parametrize('failed', [False, True])
def test_cold_retained_input_uses_actual_historical_source_and_preserves_diagnostics(tmp_path, monkeypatch, failed):
    historical = tmp_path / 'historical'
    historical.mkdir()
    output = tmp_path / 'current-evidence'
    output.mkdir()
    calls = []

    def logged(command, actual_output, name):
        calls.append(command)
        assert command[2] == historical / 'scripts/build_grafana_source_20261110.py'
        assert command[3:5] == ['--prepare', '--output-dir']
        retained_output = Path(command[5])
        assert retained_output.is_relative_to(historical / '.cache')
        retained_output.mkdir(parents=True)
        (retained_output / 'build.log').write_bytes(b'original build stdout\n')
        (retained_output / 'build-start.json').write_bytes(b'{"preparation_only":true}\n')
        (retained_output / 'large-image.tar').write_bytes(b'not copied')
        if failed:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(builder, 'logged', logged)
    if failed:
        with pytest.raises(subprocess.CalledProcessError):
            builder.build_historical_grafana_input(output, historical)
    else:
        builder.build_historical_grafana_input(output, historical)
    assert len(calls) == 1
    receipt = json.loads((output / 'previous-runtime-build/historical-source.json').read_bytes())
    assert receipt['source_commit'] == SOURCE and receipt['input_only'] and not receipt['new_runtime_approved']
    assert (output / 'previous-runtime-build/build.log').read_bytes() == b'original build stdout\n'
    assert not (output / 'previous-runtime-build/large-image.tar').exists()


def test_original_full_compilation_closure_is_verified_only_against_its_actual_source():
    historical = snapshot(builder.ROOT)
    profile = old_build.load_profile(historical)
    facts = source.verify_compiled(historical, profile)
    assert len(facts) == 14
    assert {row['target']: row['elf_sha256'] for row in facts.values()} == profile['expected']['native_sha256']
    with pytest.raises(ValueError, match=r'bytes changed: \.dockerignore'):
        source.verify_compiled(builder.ROOT, profile)
