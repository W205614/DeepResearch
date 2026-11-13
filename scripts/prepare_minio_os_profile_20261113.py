"""Derive a separate MinIO native review subject; never approve its own draft."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_minio_os_20261113 as gate  # noqa: E402
from scripts.capture_go_native_20261112 import capture  # noqa: E402

EVIDENCE = 'deployment/security/minio-os-source-20261113-evidence'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def create(args):
    receipt = gate.read(args.build_dir / 'build-receipt.json')
    gate.common.require(receipt['source_inputs'] == gate.builder.source_inputs(),
                        'Actual candidate was built from different current input bytes')
    evidence = ROOT / EVIDENCE
    evidence.mkdir(parents=True, exist_ok=True)
    profile = {'schema': 'minio-os-source-profile-20261113-v1', 'status': 'prepared',
               'profile': gate.PROFILE, 'image': gate.IMAGE, 'dockerfile': gate.DOCKERFILE, 'context': '.',
               'source_inputs': receipt['source_inputs'], 'threshold_severities': gate.SEVERITIES,
               'binary_redistribution_approved': False, 'old_binary_approval_inherited': False,
               'retained_unreviewed_findings': [], 'evidence_sha256': {},
               'independent_reviews': [EVIDENCE + '/review-root.json', EVIDENCE + '/review-release-prepare.json']}
    def copy(source, name, compressed=False):
        destination = evidence / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(gzip.compress(source.read_bytes(), mtime=0) if compressed else source.read_bytes())
        return destination.relative_to(ROOT).as_posix()
    profile['runtime_evidence'] = {'current': copy(args.build_dir / 'actual-payload/runtime-manifest.json.gz',
                                                   'runtime-manifest.json.gz')}
    trivy, grype, sbom = map(gate.read, (args.trivy, args.grype, args.sbom))
    rows, inventory = gate.trivy_rows(trivy)
    profile['expected'] = {key: receipt[key] for key in ('runtime_config', 'native_sha256')}
    profile['expected'].update(rpm_database_stable_sha256=receipt['rpm_database']['stable_sha256'],
                               trivy_inventory_sha256=inventory,
                               syft_inventory_sha256=gate.sbom_inventory(sbom),
                               scanner_controls=gate.scanner_controls(grype, sbom))
    profile['reviewed_findings'] = {'Trivy': rows, 'Grype': gate.grype_rows(grype)}
    for source, name in ((args.trivy, 'raw-trivy.json.gz'), (args.grype, 'raw-grype.json.gz'),
                         (args.sbom, 'full-syft.json.gz')):
        copy(source, name, True)
    matrix = gate.read(args.os_review)
    gate.common.require(matrix['candidate_engine'] == receipt['image_id']
                        and matrix['candidate_portable_config'] == receipt['config_digest'],
                        'OS dispositions were not derived from this actual candidate')
    for binding in matrix['primary_sources'].values():
        gate.common.require(gate.digest(gate.shared.checked(ROOT, binding['path'])) == binding['sha256'],
                            'Original OS disposition source bytes changed')
    profile['os_resolution'] = matrix['dispositions']
    copy(args.os_review, 'os34-dispositions.json')
    profile['os_resolution_evidence'] = EVIDENCE + '/os34-dispositions.json'
    old = gate.read(ROOT / 'deployment/security/minio-source-20261112-profile.json')
    natives = capture(receipt['image_id'], {name: receipt['native_sha256'][name]
                      for name in ('usr/bin/mcli', 'usr/bin/silo')}, args.build_dir / ('profile-actual-native-' + uuid.uuid4().hex))
    profile['go_source_bridge'] = {}
    with tarfile.open(args.build_dir / 'actual-payload/rootfs.tar') as archive:
        for name in ('mcli', 'silo'):
            prior = old['native_evidence']['usr/bin/' + name]
            native = copy(Path(natives['usr/bin/' + name]['native_file']), name + '-native.json')
            graph = evidence / (name + '-graph.json.gz')
            graph.write_bytes(gzip.compress(archive.extractfile('usr/share/deepresearch-build/' + name + '-graph.json').read(), mtime=0))
            profile['go_source_bridge'][name] = {'native': native, 'graph': graph.relative_to(ROOT).as_posix(),
                                                  'prior_native': prior['native'], 'prior_graph': prior['graph']}
    profile['go_source_bridge']['facts'] = gate.go_bridge(profile)
    crypto = gate.read(args.crypto_review)
    gate.common.require(crypto['curl_sha256'] == receipt['native_sha256']['usr/bin/curl']
                        and crypto['openssl_source_package_affected'] is True
                        and crypto['openssl_binary_fixed_claim'] is False,
                        'Embedded known-affected library/current CLI proof identity differs')
    profile['embedded_crypto_scope'] = {
        'review': copy(args.crypto_review, 'curl-openssl-current-path-review.json'),
        'openssl_version': '3.5.9', 'upstream_library_affected': True, 'binary_fixed_claim': False,
        'scope': 'Exact curl CLI TCP TLS/SSH paths; no general library or future-use approval',
        'compose_healthcheck': 'silo healthcheck; separate curl utility HTTP probe'}
    profile['embedded_crypto_support'] = {}
    for name, expected in crypto['supporting_files_sha256'].items():
        source = args.crypto_review.parent.parent / name
        gate.common.require(gate.digest(source) == expected, 'Original embedded crypto support changed')
        profile['embedded_crypto_support'][name] = copy(source, 'crypto-support/' + Path(name).as_posix())
    for source in sorted((args.build_dir / 'build-evidence').rglob('*')):
        if source.is_file() and (source.suffix in ('.json', '.txt', '.log', '.diff', '.sha256', '.sum', '.patch')
                                 or source.name in ('go.mod.before', 'go.mod.after', 'go.sum.before', 'go.sum.after')):
            copy(source, 'build-evidence/' + source.relative_to(args.build_dir / 'build-evidence').as_posix())
    prefix = EVIDENCE + '/build-evidence/go-repair/'
    profile['go_lock_migration'] = {key: prefix + name for key, name in {
        'mod_before': 'go.mod.before', 'mod_after': 'go.mod.after',
        'sum_before': 'go.sum.before', 'sum_after': 'go.sum.after',
        'mvs_before': 'modules-before.json', 'mvs_after': 'modules-after.json',
        'parquet': 'normal-parquet.txt', 'csv': 'normal-csv.txt', 'legacy': 'normal-legacy-multipart.txt'}.items()}
    for source in args.support:
        copy(source, 'support/' + source.parent.name + '/' + source.name)
    # Prior compiler/source facts are evidence, not inherited binary approval.
    names = {path.relative_to(ROOT).as_posix() for path in evidence.rglob('*') if path.is_file()}
    names -= set(profile['independent_reviews'])
    names.update(binding['path'] for binding in matrix['primary_sources'].values())
    for row in profile['go_source_bridge'].values():
        if isinstance(row, dict) and 'prior_native' in row:
            names.update((row['prior_native'], row['prior_graph']))
    names.update(('scripts/check_minio_os_20261113.py', 'scripts/prepare_minio_os_profile_20261113.py',
                  'scripts/go_stdlib_rebuild_20261112.py', 'scripts/check_native_jvm.py',
                  'tests/test_minio_os_20261113.py', 'tests/test_minio_rpm_facts_20261113.py',
                  'tests/fixtures/minio-native-20261113/scanner-controls.json'))
    histories = ['deployment/security/minio-fixed-disposition.json',
                 'deployment/security/minio-openpgp2-technical-review.json',
                 'deployment/security/minio-html4-technical-review.json',
                 'deployment/security/minio-html4-classification-review-20261107.json']
    for name in histories:
        history = gate.read(ROOT / name)
        history = history.get('binding', history)
        for filename, expected in history.get('evidence_files', {}).items():
            gate.common.require(gate.digest(ROOT / filename) == expected, 'Historical Go technical source evidence changed')
            names.add(filename)
    names.update(histories)
    names.add(old['regressions']['csv'])
    csv_source = 'deployment/security/minio-fixed-evidence/upstream-internal-s3select-csv-reader.go.txt'
    names.add(csv_source)
    profile['go7_technical_bridge'] = {
        'source_commit': '0cd388c666f8211f552604ae73448a066e9c25ce',
        'historical_technical_evidence': histories,
        'historical_same_go1274_csv_regression': old['regressions']['csv'],
        'abnormal_csv_regression_reexecuted': False,
        'csv': {'product_source_reader': csv_source,
                'compiled_package': 'github.com/minio/minio/internal/s3select/csv',
                'fixed_boundary': 'Source reader enforces the 1 MiB logical record bound while consuming blocks',
                'source_selection': copy(args.build_dir / 'go-source-inputs/source-prefetch.log', 'source-selection.log'),
                'current_benign_regression': prefix + 'normal-csv.txt'},
        'current_checks': 'Actual compiler/settings/full linked modules and complete import closure; benign CSV/Parquet/legacy regressions',
        'scope': 'New Thrift0.25 closure; previous binary approval is not inherited'}
    names.add(profile['go7_technical_bridge']['csv']['source_selection'])
    profile['evidence_sha256'] = {name: gate.digest(ROOT / name) for name in sorted(names)}
    profile['review_subject_sha256'] = gate.review_subject(profile)
    gate.validate_profile(profile, reviewed=False)
    gate.verify_receipt(receipt, gate.read(args.build_dir / 'image-inspect.json'), profile)
    gate.assess(trivy, grype, sbom, gate.read(args.build_dir / 'image-inspect.json'), receipt, profile, 0, 0)
    write(args.output, profile)
    return profile


def assemble(args):
    profile = gate.read(args.draft)
    for name in profile['independent_reviews']:
        profile['evidence_sha256'][name] = gate.digest(ROOT / name)
    profile['status'] = 'verified'
    gate.validate_profile(profile)
    write(ROOT / gate.PROFILE_PATH, profile)
    return profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    draft = sub.add_parser('create')
    for name in ('build-dir', 'trivy', 'grype', 'sbom', 'os-review', 'crypto-review', 'output'):
        draft.add_argument('--' + name, type=Path, required=True)
    draft.add_argument('--support', type=Path, action='append', default=[])
    final = sub.add_parser('assemble-reviewed')
    final.add_argument('--draft', type=Path, required=True)
    args = parser.parse_args()
    profile = create(args) if args.operation == 'create' else assemble(args)
    print(json.dumps({'subject': profile['review_subject_sha256'], 'status': profile['status']}))


if __name__ == '__main__':
    main()
