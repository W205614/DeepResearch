"""Retain exactly two actual MinIO graph documents; never authorize a build or scan."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {'usr/share/deepresearch-build/mcli-graph.json': 'mcli-graph.json',
           'usr/share/deepresearch-build/silo-graph.json': 'silo-graph.json'}
MAX_GRAPH_BYTES = 16 * 1024 * 1024


def collect(build_dir, *, root=ROOT):
    root = root.resolve()
    build_dir = Path(build_dir).resolve()
    if not build_dir.is_relative_to(root / '.cache'):
        raise ValueError('Use the existing build evidence under this checkout cache')
    receipt_path = build_dir / 'build-receipt.json'
    manifest_path = build_dir / 'actual-payload/runtime-manifest.json.gz'
    archive_path = build_dir / 'actual-payload/rootfs.tar'
    for path in (receipt_path, manifest_path, archive_path):
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(build_dir):
            raise ValueError('Actual build receipt, runtime manifest and rootfs are required')
    output = build_dir / 'diagnostic-graphs'
    if output.exists() or output.is_symlink():
        raise ValueError('The diagnostic output must be a new directory')
    receipt_raw, manifest_raw = receipt_path.read_bytes(), manifest_path.read_bytes()
    receipt = json.loads(receipt_raw)
    rows = json.loads(gzip.decompress(manifest_raw))
    canonical = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
    if (receipt.get('profile') != 'minio-source-20261112'
            or receipt.get('runtime_manifest_sha256') != hashlib.sha256(canonical).hexdigest()
            or len({row['path'] for row in rows}) != len(rows)):
        raise ValueError('The actual receipt and complete runtime manifest disagree')
    manifest = {row['path']: row for row in rows}
    payloads = {}
    with tarfile.open(archive_path) as archive:
        selected = {}
        for member in archive:
            name = member.name.removeprefix('./')
            if name not in TARGETS:
                continue
            if name in selected or not member.isfile() or not 0 < member.size <= MAX_GRAPH_BYTES:
                raise ValueError('Each fixed graph must be one bounded regular file')
            selected[name] = member
            expected = manifest.get(name, {})
            with archive.extractfile(member) as stream:
                raw = stream.read(MAX_GRAPH_BYTES + 1)
            if (expected.get('type') != 'file' or len(raw) != member.size
                    or len(raw) != expected.get('bytes')
                    or hashlib.sha256(raw).hexdigest() != expected.get('sha256')):
                raise ValueError('Actual graph bytes differ from their full runtime manifest')
            payloads[TARGETS[name]] = raw
    if set(payloads) != set(TARGETS.values()):
        raise ValueError('Both fixed raw graph documents are required')
    output.mkdir()
    records = {}
    for name, raw in payloads.items():
        (output / name).write_bytes(raw)
        records[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    result = {'operational_collection': True, 'diagnostic_only': True,
              'build_acceptance_claim': False, 'security_gate_claim': False,
              'raw_graphs_modified': False, 'actual_image_id': receipt['image_id'],
              'build_receipt_sha256': hashlib.sha256(receipt_raw).hexdigest(),
              'compressed_runtime_manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
              'runtime_manifest_sha256': receipt['runtime_manifest_sha256'], 'raw_graphs': records}
    (output / 'receipt.json').write_bytes((json.dumps(result, indent=2) + '\n').encode())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    result = collect(parser.parse_args(argv).build_dir)
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
