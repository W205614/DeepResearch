"""Fetch the unchanged Silo source as real Git objects for the Go security rebuild."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

COMMIT = '0cd388c666f8211f552604ae73448a066e9c25ce'
TREE = 'b37899b3e94292683364f535a7e790507a0cd76a'
URL = 'https://github.com/pgsty/silo.git'


def fetch(output: Path):
    output = output.resolve()
    if output.is_symlink():
        raise ValueError('Source transport cannot be a symlink')
    if not output.exists():
        output.mkdir(parents=True)
        subprocess.run(['git', 'init', '--bare', str(output)], check=True)
        subprocess.run(['git', '-C', str(output), 'remote', 'add', 'origin', URL], check=True)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(output), *args]).decode().strip()
    if git('rev-parse', '--is-bare-repository') != 'true' or git('remote', 'get-url', 'origin') != URL:
        raise ValueError('Source transport is not the expected upstream bare repository')
    subprocess.run(['git', '-C', str(output), 'config', 'core.hooksPath', '/dev/null'], check=True)
    if subprocess.run(['git', '-C', str(output), 'cat-file', '-e', COMMIT + '^{commit}'],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        subprocess.run(['git', '-C', str(output), '-c', 'http.version=HTTP/1.1',
                        'fetch', '--depth=1', '--no-tags', 'origin', COMMIT], check=True)
    if git('rev-parse', COMMIT + '^{commit}') != COMMIT or git('rev-parse', COMMIT + '^{tree}') != TREE:
        raise ValueError('Source commit or complete tree differs from the existing fixed Silo input')
    subprocess.run(['git', '-C', str(output), 'update-ref', 'refs/heads/pinned-source', COMMIT], check=True)
    subprocess.run(['git', '-C', str(output), 'symbolic-ref', 'HEAD', 'refs/heads/pinned-source'], check=True)
    subprocess.run(['git', '-C', str(output), 'fsck', '--full'], check=True)
    return {'repository': URL, 'commit': COMMIT, 'tree': TREE, 'git_fsck_passed': True,
            'source_transport_only': True, 'security_approved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(fetch(args.output)))


if __name__ == '__main__':
    main()
