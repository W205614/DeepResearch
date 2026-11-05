"""Exercise real vendored bytes and Git's filters before the expensive image build."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'deployment/vendors/etcd/runtime-data'


@pytest.mark.parametrize('package', ['tzdata', 'tzdata-legacy'])
def test_exact_vendored_package_matches_original_authenticated_lock(package):
    original = json.loads((ROOT / 'deployment/vendors/runtime-data/packages.lock.json').read_bytes())
    fixed = original['packages'][package + '-debian13']
    provenance = json.loads((DATA / 'provenance.json').read_bytes())['packages'][package]
    path = DATA / provenance['file']
    raw = path.read_bytes()
    assert raw.startswith(b'!<arch>\n')
    assert len(raw) == provenance['bytes']
    assert hashlib.sha256(raw).hexdigest() == fixed['sha256'] == provenance['sha256']
    assert fixed['version'] == provenance['version'] == '2026e-0+deb13u1'


@pytest.mark.parametrize('package', ['tzdata', 'tzdata-legacy'])
def test_git_stores_debian_archive_without_parent_text_normalization(package):
    path = DATA / (package + '_2026e-0+deb13u1_all.deb')
    relative = path.relative_to(ROOT).as_posix()
    attributes = subprocess.check_output(['git', 'check-attr', 'text', '--', relative], cwd=ROOT, text=True)
    assert attributes.strip().endswith(': text: unset')
    filtered = subprocess.check_output(['git', 'hash-object', '--path', relative, relative], cwd=ROOT)
    original = subprocess.check_output(['git', 'hash-object', '--no-filters', relative], cwd=ROOT)
    assert filtered == original
