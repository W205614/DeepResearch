"""Fetch the unchanged MinIO native lock with complete, strictly verified GETs."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import IncompleteRead
import json
from pathlib import Path
import shutil
import ssl
import sys
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import prefetch_minio_os_inputs_20261113 as frozen  # noqa: E402


def fetch_one(row, directory, evidence):
    destination = directory / row['name']
    if destination.exists():
        if destination.stat().st_size != row['bytes'] or frozen.digest(destination) != row['sha256']:
            raise ValueError('Existing native input differs: ' + row['name'])
        return {'name': row['name'], 'verified_existing': True}
    for attempt in range(1, 4):
        body = evidence / (row['name'] + f'.attempt-{attempt}.body')
        receipt_path = evidence / (row['name'] + f'.attempt-{attempt}.json')
        if body.exists() or receipt_path.exists():
            raise ValueError('Preserve previous transport attempts and select a new evidence directory')
        receipt = {'name': row['name'], 'url': row['url'], 'attempt': attempt,
                   'expected_bytes': row['bytes'], 'expected_sha256': row['sha256'],
                   'started_at_utc': datetime.now(timezone.utc).isoformat()}
        transport_error = None
        try:
            with body.open('xb') as target:
                with urlopen(Request(row['url'], headers={'Accept-Encoding': 'identity'}), timeout=90) as response:
                    original, actual = urlsplit(row['url']), urlsplit(response.geturl())
                    hosts = {original.hostname}
                    if original.hostname == 'proxy.golang.org':
                        hosts.add('storage.googleapis.com')
                    if original.scheme != 'https' or actual.scheme != 'https' or actual.hostname not in hosts:
                        raise ValueError('Native input redirected outside its locked official HTTPS transport')
                    receipt.update(http_status=response.status, final_url=response.geturl(),
                                   headers={name: response.headers.get(name) for name in
                                            ('Content-Length', 'Content-Type', 'Content-Range', 'ETag', 'Last-Modified')})
                    if response.status != 200 or response.headers.get('Content-Range') is not None:
                        raise ValueError('Complete native GET must return HTTP 200 without a partial range')
                    while True:
                        try:
                            block = response.read(1024 * 1024)
                        except IncompleteRead as error:
                            target.write(error.partial)
                            raise
                        if not block:
                            break
                        target.write(block)
                    if (target.tell() < row['bytes']
                            and response.headers.get('Content-Length') == str(row['bytes'])):
                        raise IncompleteRead(b'', row['bytes'] - target.tell())
        except (IncompleteRead, TimeoutError, ConnectionError, URLError) as error:
            if isinstance(error, URLError) and not isinstance(
                    error.reason, (ssl.SSLEOFError, TimeoutError, ConnectionError)):
                raise
            transport_error = error
            receipt.update(error_type=type(error).__name__, error=str(error))
        finally:
            if body.exists():
                receipt.update(actual_bytes=body.stat().st_size, actual_sha256=frozen.digest(body))
            receipt['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
            receipt_path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        detail = (f"{row['name']}: expected {row['bytes']} bytes/{row['sha256']}; "
                  f"actual {receipt.get('actual_bytes')} bytes/{receipt.get('actual_sha256')}; {receipt_path}")
        if transport_error is not None:
            body.rename(body.with_suffix('.failed.body'))
            if attempt == 3:
                raise ValueError('Native full GET exhausted three EOF/timeout attempts: ' + detail) from transport_error
            continue
        if receipt['actual_bytes'] != row['bytes'] or receipt['actual_sha256'] != row['sha256']:
            body.rename(body.with_suffix('.failed.body'))
            raise ValueError('Complete native input checksum or size differs: ' + detail)
        shutil.copyfile(body, destination)
        return receipt
    raise AssertionError('Unreachable bounded transport loop')


def fetch(directory, evidence, lock):
    directory.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda row: fetch_one(row, directory, evidence), lock['inputs']))
    verified = frozen.verify(directory, lock)
    result = {'verified_inputs': verified['verified_inputs'], 'transport_receipts': receipts,
              'unchanged_lock_sha256': frozen.digest(frozen.LOCK)}
    (evidence / 'verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--evidence-dir', required=True, type=Path)
    args = parser.parse_args()
    result = fetch(args.directory, args.evidence_dir, json.loads(frozen.LOCK.read_bytes()))
    print(json.dumps({'verified_inputs': len(result['verified_inputs']),
                      'unchanged_lock_sha256': result['unchanged_lock_sha256']}))


if __name__ == '__main__':
    main()
