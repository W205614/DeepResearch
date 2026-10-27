import hashlib
from pathlib import Path
import subprocess

import pytest

from scripts.check_artifact_source_reuse import git, verify_snapshot
from scripts.image_disposition import DispositionError


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, 'init')
    (tmp_path / 'frozen.bin').write_bytes(b'original\x00\r\nbytes')
    git(tmp_path, 'add', 'frozen.bin')
    git(tmp_path, '-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'frozen')
    ancestor = git(tmp_path, 'rev-parse', 'HEAD').decode().strip()
    git(tmp_path, 'tag', 'fixed-artifact', ancestor)
    (tmp_path / 'app.py').write_text('changed application\n')
    git(tmp_path, 'add', 'app.py')
    git(tmp_path, '-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'application fix')
    return tmp_path, ancestor, {'frozen.bin': hashlib.sha256(b'original\x00\r\nbytes').hexdigest()}


def verify(repository):
    root, ancestor, expected = repository
    return verify_snapshot(root, expected, built_from=ancestor, tags=('fixed-artifact',))


def test_unchanged_artifact_source_is_reused_without_claiming_rebuild(repository):
    result = verify(repository)
    assert result['passed'] and result['unchanged_frozen_files'] == 1
    assert result['artifact_publication_source'] != result['consumed_by_application_commit']
    assert result['artifacts_rebuilt_at_application_commit'] is False


@pytest.mark.parametrize('staged', [False, True])
def test_changed_raw_working_or_index_bytes_block(repository, staged):
    root, _, _ = repository
    (root / 'frozen.bin').write_bytes(b'original\x00\nbytes')
    if staged:
        git(root, 'add', 'frozen.bin')
    with pytest.raises(DispositionError):
        verify(repository)


def test_changed_commit_blocks_even_after_working_bytes_restored(repository):
    root, _, _ = repository
    (root / 'frozen.bin').write_bytes(b'changed')
    git(root, 'add', 'frozen.bin')
    git(root, '-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'source drift')
    (root / 'frozen.bin').write_bytes(b'original\x00\r\nbytes')
    with pytest.raises(DispositionError):
        verify(repository)


def test_moved_tag_or_missing_ancestor_blocks(repository):
    root, _, expected = repository
    git(root, 'tag', '-f', 'fixed-artifact', 'HEAD')
    with pytest.raises(DispositionError):
        verify(repository)
    with pytest.raises(subprocess.CalledProcessError):
        verify_snapshot(root, expected, built_from='0' * 40, tags=())


def test_approved_artifact_jobs_fetch_ancestor_history():
    import yaml
    root = Path(__file__).parents[1]
    for name in ('ci.yml', 'security.yml'):
        jobs = yaml.safe_load((root / '.github/workflows' / name).read_text(encoding='utf-8'))['jobs']
        for job in jobs.values():
            if not any('scripts/fetch_approved_image.py' in step.get('run', '') for step in job['steps']):
                continue
            checkout = next(step for step in job['steps'] if step.get('uses', '').startswith('actions/checkout@'))
            assert checkout['with']['fetch-depth'] == 0
            assert checkout['with']['fetch-tags'] is True
            assert any('scripts/check_artifact_source_reuse.py' in step.get('run', '') for step in job['steps'])
