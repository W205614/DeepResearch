"""Apply exact Go and fixed BusyBox dispositions to the complete Alpine image scans."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_minio_alpine_20261121 as runtime  # noqa: E402
from scripts import check_minio_otel_20261115 as minio  # noqa: E402
from scripts import check_minio_os_20261113 as prior  # noqa: E402
from scripts.check_native_jvm import validate_source  # noqa: E402

APK_PACKAGES = {
    'alpine-baselayout': '3.7.2-r1', 'alpine-baselayout-data': '3.7.2-r1', 'alpine-keys': '2.6-r0',
    'alpine-release': '3.24.3-r0', 'busybox': '1.37.0-r31', 'busybox-binsh': '1.37.0-r31',
    'ca-certificates-bundle': '20261020-r0', 'libcap-ng': '0.8.5-r2', 'libeconf': '0.8.3-r0',
    'musl': '1.2.6-r3', 'musl-utils': '1.2.6-r3', 'scanelf': '1.3.9-r1', 'setpriv': '2.42.3-r1',
    'tzdata': '2026f-r0',
}
TRIVY_INVENTORY = {
    'os:alpine': '75d052acb4fa9eae4cfea802e456dbd73740b48956986fc0de77e399ec6a83d9',
    'usr/bin/mcli': '57d24a0efc0f3f3426ec6dba0a11e7baa97c43f319025424a152a86bcb126338',
    'usr/bin/silo': 'badfc005bf3504a6c0beb341cabbabe30869d00e8a48f8928515d27d59cdc085',
}


def verify_graphs(archive):
    with tarfile.open(archive) as exported:
        for binary, expected in runtime.GRAPH_SHA256.items():
            prefix = f'usr/share/deepresearch-build/{binary}'
            raw = exported.extractfile(prefix + '-graph.json').read()
            prior.common.require(hashlib.sha256(raw).hexdigest() == expected, 'MinIO compiled graph changed')
            graph = prior.common.decode_graph(raw)
            native_path = prefix + '-native.json'
            native_raw = exported.extractfile(native_path).read()
            native = json.loads(native_raw)
            prior.common.require(native['GoVersion'] == 'go1.27.4' and
                                 hashlib.sha256(native_raw).hexdigest() == runtime.GO_PAYLOAD_SHA256[native_path],
                                 'MinIO native Go module closure changed')
            minio.verify_graph_native(graph, native)
            affected = ('golang.org/x/net/html', 'golang.org/x/crypto/openpgp', 'github.com/nats-io/nats-server/v2')
            prior.common.require(not any(p == name or p.startswith(name + '/')
                                         for p in graph for name in affected),
                                 'MinIO compiled graph contains an excluded parser, OpenPGP package or NATS Server')
            if binary == 'silo':
                prior.common.require('reader.go' in graph['github.com/minio/minio/internal/s3select/csv']['GoFiles'],
                                     'The reviewed source-fixed CSV reader is absent from the executable')


def assess(args):
    receipt = prior.read(args.build_dir / 'build-receipt.json')
    inspection = prior.read(args.build_dir / 'image-inspect.json')
    trivy, grype, sbom = (prior.read(path) for path in (args.trivy, args.grype, args.sbom))
    prior.common.require(receipt['profile'] == runtime.PROFILE and receipt['image'] == runtime.IMAGE,
                         'Wrong MinIO build subject')
    prior.common.require(receipt['image_id'] == inspection['Id']
                         and receipt['diff_ids'] == inspection['RootFS']['Layers']
                         and receipt['runtime_config'] == prior.shared.runtime_config(inspection['Config']),
                         'MinIO build receipt and inspected image differ')
    prior.common.require(trivy.get('SchemaVersion') == 2 and not trivy.get('Errors')
                         and trivy.get('Trivy') == {'Version': '0.75.0'}
                         and trivy.get('ArtifactType') == 'container_image'
                         and prior.assess_secrets(trivy)['passed'], 'MinIO Trivy or secret gate failed')
    prior.common.verify_trivy_source(trivy, inspection, receipt)
    validate_source(sbom, inspection)
    config = base64.b64decode(sbom['source']['metadata']['config'], validate=True)
    prior.common.require(grype['source']['target']['imageID'] == receipt['config_digest']
                         and 'sha256:' + hashlib.sha256(config).hexdigest() == receipt['config_digest']
                         and prior.shared.runtime_config(json.loads(config)['config']) == receipt['runtime_config']
                         and grype['descriptor']['name'] == 'grype'
                         and grype['descriptor']['version'] == '0.121.0'
                         and grype['descriptor']['db']['status']['valid'] is True
                         and not grype.get('ignoredMatches') and not grype.get('errors'),
                         'Grype result is filtered or bound to another image')
    old = prior.load_profile()
    prior.common.require(prior.scanner_controls(grype, sbom) == old['expected']['scanner_controls'],
                         'MinIO native scanner controls changed')
    prior.bind_matches(grype, sbom)
    verify_graphs(args.build_dir / 'actual-payload/rootfs.tar')

    packages = [(p['name'], p['version']) for p in sbom['artifacts'] if p['type'] == 'apk']
    prior.common.require(len(packages) == len(APK_PACKAGES) and dict(packages) == APK_PACKAGES
                         and not any(p['type'] == 'rpm' for p in sbom['artifacts']),
                         'Expected complete Alpine APK inventory differs')
    reviewed = old['reviewed_findings']
    expected_trivy = [r for r in reviewed['Trivy'] if r['target'] in {'usr/bin/silo', 'usr/bin/mcli'}]
    trivy_rows, targets = prior.trivy_rows(trivy)
    prior.common.require(targets == TRIVY_INVENTORY, 'MinIO complete Trivy inventories differ')
    expected_grype = [r for r in reviewed['Grype'] if r['type'] == 'go-module']
    expected_grype += [{'id': 'CVE-2025-60876', 'namespace': 'nvd:cpe', 'package': p,
                        'version': APK_PACKAGES[p], 'type': 'apk', 'severity': 'Medium',
                        'locations': ['/lib/apk/db/installed']} for p in ('busybox', 'busybox-binsh')]
    grype_rows = prior.grype_rows(grype)
    for scanner, actual, expected in [('Trivy', trivy_rows, expected_trivy),
                                      ('Grype', grype_rows, expected_grype)]:
        before, after = prior.row_counter(expected), prior.row_counter(actual)
        prior.common.require(before == after, f'{scanner} raw findings differ from the exact technical review')
    return {'passed': True, 'actual_image_id': receipt['image_id'], 'raw_findings': {
        'Trivy': len(trivy_rows), 'Grype': len(grype_rows),
    }, 'unresolved': 0, 'raw_reports_modified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build-dir', 'trivy', 'grype', 'sbom', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
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
