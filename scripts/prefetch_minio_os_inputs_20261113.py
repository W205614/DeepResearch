"""Fetch and verify the exact public source/RPM inputs for the MinIO native repair."""
from __future__ import annotations

import argparse
import hashlib
from http.client import IncompleteRead
import json
from pathlib import Path
import ssl
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'deployment/security/minio-os-20261113/source.lock.json'


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def verify(directory, lock):
    expected = {row['name']: row for row in lock['inputs']}
    if directory.is_symlink() or {p.name for p in directory.iterdir()} != set(expected):
        raise ValueError('Native input directory contains missing or additional files')
    for name, row in expected.items():
        path = directory / name
        if (path.is_symlink() or not path.is_file() or path.stat().st_size != row['bytes']
                or digest(path) != row['sha256']):
            raise ValueError('Native input bytes differ: ' + name)
    return {'verified_inputs': {name: row['sha256'] for name, row in expected.items()}}


def fetch(directory, lock):
    directory.mkdir(parents=True, exist_ok=True)
    for row in lock['inputs']:
        path = directory / row['name']
        if path.exists():
            if path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
                raise ValueError('Existing source input differs; preserve it and select a new directory')
            continue
        temporary = directory / (row['name'] + '.partial')
        if temporary.is_symlink():
            raise ValueError('Partial native input cannot be a symlink')
        offset = temporary.stat().st_size if temporary.exists() else 0
        if offset > row['bytes']:
            raise ValueError('Partial native input exceeds its pinned length')
        with temporary.open('ab') as target:
            for start in range(offset, row['bytes'], 1024 * 1024):
                end = min(row['bytes'], start + 1024 * 1024) - 1
                request = Request(row['url'], headers={'Range': f'bytes={start}-{end}',
                                                      'Accept-Encoding': 'identity'})
                for attempt in range(3):
                    try:
                        with urlopen(request, timeout=90) as response:
                            original, actual = urlsplit(row['url']), urlsplit(response.geturl())
                            permitted = {original.hostname}
                            if original.hostname == 'proxy.golang.org':
                                permitted.add('storage.googleapis.com')
                            if actual.scheme != 'https' or actual.hostname not in permitted:
                                raise ValueError('Native input redirected outside its official transport')
                            expected_range = f'bytes {start}-{end}/{row["bytes"]}'
                            whole_file = start == 0 and end + 1 == row['bytes']
                            if not ((response.status == 206 and response.headers.get('Content-Range') == expected_range)
                                    or (whole_file and response.status == 200)):
                                raise ValueError('Native input range or complete length differs')
                            data = response.read(end - start + 2)
                        if len(data) != end - start + 1:
                            raise ValueError('Native input chunk ended early')
                        break
                    except (IncompleteRead, TimeoutError, ConnectionError, URLError) as error:
                        if isinstance(error, URLError) and not isinstance(
                                error.reason, (ssl.SSLEOFError, TimeoutError, ConnectionError)):
                            raise
                        if attempt == 2:
                            raise
                target.write(data)
                target.flush()
        if digest(temporary) != row['sha256']:
            raise ValueError('Complete native input checksum differs')
        temporary.rename(path)
    return verify(directory, lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=LOCK)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    lock = json.loads(args.lock.read_bytes())
    result = (verify if args.verify_only else fetch)(args.directory, lock)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
