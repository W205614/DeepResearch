"""Validate historical evidence against its actual immutable source, never a modified current file."""
from pathlib import Path
import subprocess

SOURCE = '78a19df9dfa6f072af6ebe40ff99cf164d0c4ff8'


def snapshot(root):
    root = Path(root).resolve()
    path = root / '.cache/retained-source-review-20261112/v0.1.3'
    if path.is_symlink() or not path.resolve().is_relative_to((root / '.cache').resolve()):
        raise ValueError('Historical source review must stay in the checkout cache')
    subprocess.run(['git', 'merge-base', '--is-ancestor', SOURCE, 'HEAD'], cwd=root, check=True)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', 'worktree', 'add', '--detach', str(path), SOURCE],
                                cwd=root, capture_output=True, check=True)
        (path.parent / 'creation.log').write_bytes(result.stdout + result.stderr)
    observed = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=path).decode().strip()
    changed = subprocess.check_output(['git', 'status', '--porcelain=v1', '--untracked-files=no'], cwd=path)
    if observed != SOURCE or changed.strip():
        raise ValueError('The historical review checkout changed; no current-source substitution is allowed')
    return path
