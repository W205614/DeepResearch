"""Retain bounded actual build documents; never authorize a build or scan."""
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
GRAFANA_TARGETS = {
    'usr/share/deepresearch/grafana-candidate/graphs/main/main.json.gz': 'graphs-main-main.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/main/main.receipt.json': 'graphs-main-main.receipt.json',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/elasticsearch.json.gz': 'graphs-plugins-elasticsearch.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/elasticsearch.receipt.json': 'graphs-plugins-elasticsearch.receipt.json',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/grafana-postgresql-datasource.json.gz': 'graphs-plugins-grafana-postgresql-datasource.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/grafana-postgresql-datasource.receipt.json': 'graphs-plugins-grafana-postgresql-datasource.receipt.json',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/grafana-pyroscope-datasource.json.gz': 'graphs-plugins-grafana-pyroscope-datasource.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/grafana-pyroscope-datasource.receipt.json': 'graphs-plugins-grafana-pyroscope-datasource.receipt.json',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/influxdb.json.gz': 'graphs-plugins-influxdb.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/influxdb.receipt.json': 'graphs-plugins-influxdb.receipt.json',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/jaeger.json.gz': 'graphs-plugins-jaeger.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/jaeger.receipt.json': 'graphs-plugins-jaeger.receipt.json',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/loki.json.gz': 'graphs-plugins-loki.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/loki.receipt.json': 'graphs-plugins-loki.receipt.json',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/mssql.json.gz': 'graphs-plugins-mssql.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/mssql.receipt.json': 'graphs-plugins-mssql.receipt.json',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/mysql.json.gz': 'graphs-plugins-mysql.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/mysql.receipt.json': 'graphs-plugins-mysql.receipt.json',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/opentsdb.json.gz': 'graphs-plugins-opentsdb.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/opentsdb.receipt.json': 'graphs-plugins-opentsdb.receipt.json',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/prometheus.json.gz': 'graphs-plugins-prometheus.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/prometheus.receipt.json': 'graphs-plugins-prometheus.receipt.json',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/stackdriver.json.gz': 'graphs-plugins-stackdriver.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/stackdriver.receipt.json': 'graphs-plugins-stackdriver.receipt.json',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/tempo.json.gz': 'graphs-plugins-tempo.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/tempo.receipt.json': 'graphs-plugins-tempo.receipt.json',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/zipkin.json.gz': 'graphs-plugins-zipkin.json.gz',
    'usr/share/deepresearch/grafana-candidate/graphs/plugins/zipkin.receipt.json': 'graphs-plugins-zipkin.receipt.json',
    'usr/share/deepresearch/grafana-candidate/provenance/plugins/elasticsearch.json': 'provenance-plugins-elasticsearch.json',
    'usr/share/deepresearch/grafana-candidate/provenance/plugins/grafana-postgresql-datasource.json': 'provenance-plugins-grafana-postgresql-datasource.json',
    'usr/share/deepresearch/grafana-candidate/provenance/plugins/grafana-pyroscope-datasource.json': 'provenance-plugins-grafana-pyroscope-datasource.json',
    'usr/share/deepresearch/grafana-candidate/provenance/plugins/influxdb.json': 'provenance-plugins-influxdb.json',
    'usr/share/deepresearch/grafana-candidate/provenance/plugins/jaeger.json': 'provenance-plugins-jaeger.json',
    'usr/share/deepresearch/grafana-candidate/provenance/plugins/loki.json': 'provenance-plugins-loki.json',
    'usr/share/deepresearch/grafana-candidate/provenance/plugins/mssql.json': 'provenance-plugins-mssql.json',
    'usr/share/deepresearch/grafana-candidate/provenance/plugins/mysql.json': 'provenance-plugins-mysql.json',
    'usr/share/deepresearch/grafana-candidate/provenance/plugins/opentsdb.json': 'provenance-plugins-opentsdb.json',
    'usr/share/deepresearch/grafana-candidate/provenance/plugins/prometheus.json': 'provenance-plugins-prometheus.json',
    'usr/share/deepresearch/grafana-candidate/provenance/plugins/stackdriver.json': 'provenance-plugins-stackdriver.json',
}
MAX_GRAPH_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


def collect(build_dir, *, root=ROOT, artifact='minio'):
    if artifact not in {'minio', 'grafana'}:
        raise ValueError('Only the two fixed source-build artifacts are supported')
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
    if (receipt.get('profile') != artifact + '-source-20261112'
            or receipt.get('runtime_manifest_sha256') != hashlib.sha256(canonical).hexdigest()
            or len({row['path'] for row in rows}) != len(rows)):
        raise ValueError('The actual receipt and complete runtime manifest disagree')
    manifest = {row['path']: row for row in rows}
    targets = TARGETS
    grafana_context = {}
    if artifact == 'grafana':
        profile_path = root / 'deployment/security/grafana-source-20261112-profile.json'
        expected_path = root / 'deployment/security/grafana-source-20261112-evidence/runtime-manifest.json.gz'
        for path in (profile_path, expected_path):
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                raise ValueError('The fixed public Grafana baseline is required')
        profile_raw = profile_path.read_bytes()
        profile = json.loads(profile_raw)
        expected_rows = json.loads(gzip.decompress(expected_path.read_bytes()))
        expected_hash = hashlib.sha256(json.dumps(expected_rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if (profile.get('profile') != 'grafana-source-20261112'
                or expected_hash != profile['expected']['runtime_manifest_sha256']
                or len({row['path'] for row in expected_rows}) != len(expected_rows)):
            raise ValueError('The public Grafana baseline manifest differs from its fixed profile')
        expected = {row['path']: row for row in expected_rows}
        differences = {name for name in expected.keys() | manifest.keys() if expected.get(name) != manifest.get(name)}
        targets = {name: filename for name, filename in GRAFANA_TARGETS.items() if name in differences}
        if not targets:
            raise ValueError('No whitelisted differing Grafana documents are present')
        grafana_context = {'artifact': 'grafana', 'public_profile_sha256': hashlib.sha256(profile_raw).hexdigest(),
                           'expected_runtime_manifest_sha256': expected_hash,
                           'selected_runtime_paths': sorted(targets),
                           'uncollected_changed_runtime_paths': sorted(differences - targets.keys())}
    total_bytes = 0
    payloads = {}
    with tarfile.open(archive_path) as archive:
        selected = {}
        for member in archive:
            name = member.name.removeprefix('./')
            if name not in targets:
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
            total_bytes += len(raw)
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValueError('The selected diagnostic documents exceed their aggregate bound')
            payloads[targets[name]] = raw
    if set(payloads) != set(targets.values()):
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
    result.update(grafana_context)
    (output / 'receipt.json').write_bytes((json.dumps(result, indent=2) + '\n').encode())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--artifact', choices=('minio', 'grafana'), default='minio')
    args = parser.parse_args(argv)
    result = collect(args.build_dir, artifact=args.artifact)
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
