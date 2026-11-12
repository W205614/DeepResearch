"""Complete the observed truncated official module transports; Go still verifies h1/SumDB."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import zipfile

import httpx

MODULES = (
    ('github.com/prometheus/common', 'v0.71.0'),
    ('github.com/apache/arrow-go/v18', 'v18.7.0'),
    ('github.com/grafana/loki/v3', 'v3.7.7'),
    ('github.com/minio/minio-go/v7', 'v7.4.1'),
    ('github.com/pgsty/silo-console', 'v0.0.0-20261017112417-ef2dd2c41d21'),
    ('github.com/hashicorp/go-metrics', 'v0.6.1'),
    ('k8s.io/client-go', 'v0.37.0'),
    ('github.com/google/shlex', 'v0.0.0-20191202100458-e7afc7fbc510'),
    ('github.com/jedib0t/go-pretty/v6', 'v6.8.3'),
    ('google.golang.org/api', 'v0.293.0'),
    ('google.golang.org/grpc', 'v1.86.0-dev'),
    ('github.com/grpc-ecosystem/go-grpc-middleware/providers/prometheus', 'v1.1.1'),
    ('github.com/grafana/tempo', 'v1.5.1-0.20260910130453-bcfe9f230c1d'),
    ('google.golang.org/grpc', 'v1.85.0-dev.0.20260825072537-93e31b48545e'),
)


def prefetch(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError('Module transport cannot be a symlink')
    with httpx.Client(timeout=180, trust_env=False, follow_redirects=True) as client:
        def fetch(item):
            module, version = item
            rows = []
            for suffix in ('mod', 'info', 'zip'):
                relative = module + '/@v/' + version + '.' + suffix
                path = output / relative
                url = 'https://proxy.golang.org/' + relative
                if path.exists():
                    data, attempts = path.read_bytes(), 0
                else:
                    # Both BuildKit downloads failed with EOF on these exact modules.
                    for attempts in range(1, 4):
                        try:
                            response = client.get(url)
                            response.raise_for_status()
                            data = response.content
                            if suffix == 'zip':
                                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                                    if archive.testzip() is not None:
                                        raise ValueError('Official source ZIP CRC differs')
                            break
                        except (httpx.TransportError, zipfile.BadZipFile):
                            if attempts == 3:
                                raise
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                rows.append({'relative': relative, 'url': url, 'bytes': len(data),
                             'sha256': hashlib.sha256(data).hexdigest(), 'attempts': attempts})
            return rows
        records = [row for rows in ThreadPoolExecutor(max_workers=4).map(fetch, MODULES) for row in rows]
    return {'official_transports': records, 'sumdb_verification_deferred_to_go': True,
            'security_approved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args()
    result = prefetch(args.output)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_bytes((json.dumps(result, indent=2) + '\n').encode())


if __name__ == '__main__':
    main()
