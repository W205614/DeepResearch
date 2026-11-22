"""Bind the updated MinIO and Loki locks and their original review checkout."""
from pathlib import Path
import hashlib
import subprocess

from scripts.go_stdlib_rebuild_20261112 import require


PREVIOUS = '083fe5272cd0670e13d198bf5d774bfa5267248c'
LOCKS = {
    'deployment/vendors/loki/go.mod': '4134c323575b638cd97080145da7605b75acab79d382c3d96add05ab7b84cfc5',
    'deployment/vendors/loki/go.sum': 'b3e413303cad64eabc611094a3fda9712564e2b3d9c545191d7635281cab3e7e',
    'deployment/vendors/minio-otel-20261115/go.mod': '93751ebfc1cc537862e752f055a16a3f0ca20cb43b96b4a73ce020454a7d54cd',
    'deployment/vendors/minio-otel-20261115/go.sum': '7e3a99096eb387342edca75770a949c2c29a31fd81981efadb105a69f84b6680',
}
SCAN_INVENTORIES = {
    'deployment/vendors/loki/go.mod': 'c70c8336f2b8d07029e6dd1a6d4afa233f7ada818adfa134f259e5e1323b746e',
    'deployment/vendors/minio-otel-20261115/go.mod': '7f4bb908e1feabf0471bc4205bee57e0406e369ea5f24f39b1de67f008308620',
}


def verify_locks(root):
    for name, digest in LOCKS.items():
        require(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest,
                'The reviewed Go dependency lock changed: ' + name)


def previous_source(root):
    root = Path(root).resolve()
    cache = root / '.cache'
    path = cache / 'go-lock-upgrade-20261122' / PREVIOUS
    require(not path.is_symlink() and path.resolve().is_relative_to(cache.resolve()),
            'The immutable dependency review checkout must stay under the workspace cache')
    subprocess.run(['git', 'merge-base', '--is-ancestor', PREVIOUS, 'HEAD'], cwd=root, check=True)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', '-c', 'core.longpaths=true', '-c', 'core.hooksPath=/dev/null',
                        'worktree', 'add', '--detach', str(path), PREVIOUS],
                       cwd=root, capture_output=True, check=True)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=path).decode().strip()
    status = subprocess.check_output(['git', '-c', 'core.longpaths=true', 'status',
                                      '--porcelain=v1', '--untracked-files=no'], cwd=path)
    require(head == PREVIOUS and not status.strip(), 'The original dependency review checkout changed')
    return path
