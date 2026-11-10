import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'deployment/security/code-scanning-review-20261110-evidence/telemetry-example'


@pytest.fixture
def original_example(tmp_path):
    review = json.loads((EVIDENCE / 'review.json').read_text(encoding='utf-8'))
    for name, expected in review['original_files'].items():
        source = (ROOT / 'tests/fixtures/milvus-telemetry-example-20261110' / (name + '.txt')
                  if name.endswith('.go') else ROOT / 'deployment/vendors/milvus/modules/examples/telemetry_demo' / name)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
        shutil.copyfile(source, tmp_path / name)
    return tmp_path, review


def test_maintenance_patch_applies_to_original_sources_and_generates_recorded_fixed_files(original_example):
    directory, review = original_example
    patch = EVIDENCE / 'telemetry-maintained.patch'
    assert hashlib.sha256(patch.read_bytes()).hexdigest() == review['patch']['sha256']
    subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', '--check', str(patch)],
                   cwd=directory, check=True, capture_output=True)
    subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', str(patch)],
                   cwd=directory, check=True, capture_output=True)
    for name, expected in review['maintained_files'].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected
    assert review['full_module_graph']['selected_xcrypto'] == 'v0.58.1-0.20261009181718-2f1f834330b2'
    for name in review['source_files_sha256']:
        assert b'github.com/milvus-io/milvus/client/v2/' not in (directory / name).read_bytes()


def test_maintenance_patch_does_not_silently_apply_to_a_changed_dependency_snapshot(original_example):
    directory, _ = original_example
    path = directory / 'go.mod'
    content = path.read_bytes()
    assert b'golang.org/x/crypto v0.58.0' in content
    path.write_bytes(content.replace(b'golang.org/x/crypto v0.58.0', b'golang.org/x/crypto v0.57.0'))
    result = subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', '--check', str(EVIDENCE / 'telemetry-maintained.patch')],
                            cwd=directory, capture_output=True)
    assert result.returncode != 0
