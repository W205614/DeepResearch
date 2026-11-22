"""Enforce complete, unfiltered security checks for the Grafana 13.2.6 image."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_grafana_20261122 as runtime  # noqa: E402
from scripts import check_grafana_source_build_20261114 as retained  # noqa: E402
from scripts import check_minio_os_20261113 as scans  # noqa: E402
from scripts.check_native_jvm import validate_source  # noqa: E402
from scripts.go_stdlib_rebuild_20261112 import require  # noqa: E402

EVIDENCE = ROOT / 'deployment/security/grafana-1326-20261122-security.json'
BUSYBOX_SHA256 = '68dba7ae251db2ecca77e6ef405bf781111dc41f87c93c30205745fdbe2c697e'
OPENPGP = 'golang.org/x/crypto/openpgp'
MAIN_NATIVE_SHA256 = '878f83f1804e86697a86233cb086011b4b43f9b291a3f46e92488bfb8381efb0'
MAIN_GRAPH_CONTENT_SHA256 = '8410fecd85b65ee53dcd743fee18c7e58846fe6d9acce53a19782efa7f3b5b65'


def verify_native(build_dir, receipt, findings, proof):
    old = retained.load_profile()
    scope = old['native_evidence']
    plugin_hashes = {name: row['elf_sha256'] for name, row in scope.items() if name != runtime.MAIN}
    require(receipt['native_sha256'] == {runtime.MAIN: runtime.MAIN_SHA256, **plugin_hashes},
            'Grafana main or retained plugin identity changed')
    with tarfile.open(build_dir / 'actual-payload/rootfs.tar') as archive:
        busybox = archive.extractfile('bin/busybox').read()
        require(hashlib.sha256(busybox).hexdigest() == BUSYBOX_SHA256, 'Fixed Grafana BusyBox bytes differ')
        native = archive.extractfile(runtime.EVIDENCE_PATH + 'native.json').read()
        main_graph = archive.extractfile(runtime.EVIDENCE_PATH + 'main-graph.json').read()
        packages = scans.common.decode_graph(main_graph)
        # Go reports cache staleness separately from the production package metadata.
        recorded = {name: {key: value for key, value in info.items() if key not in {'Stale', 'StaleReason'}}
                    for name, info in packages.items()}
        content = json.dumps(recorded, sort_keys=True, separators=(',', ':')).encode()
        require(hashlib.sha256(native).hexdigest() == MAIN_NATIVE_SHA256
                and hashlib.sha256(content).hexdigest() == MAIN_GRAPH_CONTENT_SHA256
                and json.loads(native)['GoVersion'] == 'go1.27.4',
                'Grafana new main Go version or complete source graph differs')
    graphs = {runtime.MAIN: packages}
    for row in findings:
        target = row['target'].lstrip('/')
        if row['id'] == 'GO-2026-5932':
            if target not in graphs:
                with gzip.open(ROOT / scope[target]['graph'], 'rb') as graph:
                    graphs[target] = scans.common.decode_graph(graph.read())
            require(not any(name == OPENPGP or name.startswith(OPENPGP + '/') for name in graphs[target]),
                    'An affected OpenPGP package entered the Grafana production closure')
        else:
            comparison = proof['tempo_fixes'][row['id']]
            modules = scans.read(ROOT / scope[target]['native'])['Deps']
            dependency, = [item for item in modules if item['Path'] == 'github.com/grafana/tempo']
            require(dependency['Version'] == row['version']
                    and proof['tempo_commit'].startswith(dependency['Version'].rsplit('-', 1)[-1])
                    and comparison['ahead'] > 0 and comparison['behind'] == 0
                    and comparison['comparison_url'] == 'https://github.com/grafana/tempo/compare/'
                    + comparison['commit'] + '...' + proof['tempo_commit'],
                    'Grafana Tempo backend no longer includes the exact upstream fixes')
    return hashlib.sha256(main_graph).hexdigest()


def assess(args):
    proof = scans.read(EVIDENCE)
    require(proof['schema'] == 'grafana-1326-exact-security-v1', 'Unexpected Grafana security review')
    receipt = scans.read(args.build_dir / 'build-receipt.json')
    inspection = scans.read(args.build_dir / 'image-inspect.json')
    trivy, grype, sbom = (scans.read(path) for path in (args.trivy, args.grype, args.sbom))
    require(receipt['profile'] == runtime.PROFILE and receipt['image'] == runtime.IMAGE
            and receipt['image_id'] == inspection['Id'] and receipt['diff_ids'] == inspection['RootFS']['Layers']
            and receipt['runtime_config'] == retained.runtime_config(inspection['Config']),
            'Actual Grafana configuration or build subject differs')
    require(trivy.get('SchemaVersion') == 2 and trivy.get('Trivy') == {'Version': '0.75.0'}
            and not trivy.get('Errors') and trivy.get('ArtifactType') == 'container_image'
            and scans.assess_secrets(trivy)['passed'], 'Grafana Trivy or secret gate failed')
    scans.common.verify_trivy_source(trivy, inspection, receipt)
    trivy_rows, trivy_inventory = scans.trivy_rows(trivy)
    require(trivy_inventory == proof['trivy_inventory_sha256']
            and scans.row_counter(trivy_rows) == scans.row_counter(proof['trivy_findings']),
            'Complete Grafana Trivy inventories or exact raw findings changed')
    validate_source(sbom, inspection)
    config = base64.b64decode(sbom['source']['metadata']['config'], validate=True)
    require('sha256:' + hashlib.sha256(config).hexdigest() == receipt['config_digest']
            and retained.runtime_config(json.loads(config)['config']) == receipt['runtime_config']
            and grype['source']['target']['imageID'] == receipt['config_digest']
            and grype['descriptor']['name'] == 'grype' and grype['descriptor']['version'] == '0.121.0'
            and grype['descriptor']['db']['status']['valid'] is True
            and not grype.get('ignoredMatches') and not grype.get('errors'),
            'Grafana Grype scan is filtered or bound to another image')
    require(scans.scanner_controls(grype, sbom) == scans.load_profile()['expected']['scanner_controls'],
            'Grafana native scanner controls changed')
    scans.bind_matches(grype, sbom)
    grype_rows = scans.grype_rows(grype)
    require(scans.row_counter(grype_rows) == scans.row_counter(proof['grype_findings']),
            'The exact Grafana Grype findings differ')
    require({row['id'] for row in grype_rows if row['package'] == 'github.com/grafana/tempo'}
            == {fix['ghsa'] for fix in proof['tempo_fixes'].values()},
            'The Tempo advisory aliases differ from the upstream fix evidence')
    graph_sha256 = verify_native(args.build_dir, receipt, trivy_rows, proof)
    return {'passed': True, 'actual_image_id': receipt['image_id'],
            'raw_findings': {'Trivy': len(trivy_rows), 'Grype': len(grype_rows)},
            'main_graph_raw_sha256': graph_sha256, 'unresolved': 0, 'raw_reports_modified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build-dir', 'trivy', 'grype', 'sbom', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = assess(args)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'passed': False, 'error': str(error)}
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
