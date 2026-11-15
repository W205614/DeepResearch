"""Derive the actual MinIO otel subject; independent reviews must be supplied separately."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_minio_otel_20261115 as gate  # noqa: E402

EVIDENCE = 'deployment/security/minio-otel-source-20261115-evidence'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def create(args):
    old = gate.prior.load_profile()
    receipt = gate.prior.read(args.build_dir / 'build-receipt.json')
    if receipt['source_inputs'] != gate.builder.source_inputs():
        raise ValueError('Candidate build inputs differ from current source')
    inspection = gate.prior.read(args.build_dir / 'image-inspect.json')
    profile = {'schema': 'minio-otel-source-profile-20261115-v1', 'status': 'prepared',
               'profile': gate.builder.PROFILE, 'image': gate.builder.IMAGE,
               'context': '.', 'dockerfile': gate.builder.DOCKERFILE,
               'source_inputs': receipt['source_inputs'], 'binary_redistribution_approved': False,
               'prior_profile': gate.prior.PROFILE_PATH,
               'prior_profile_sha256': gate.digest(ROOT / gate.prior.PROFILE_PATH),
               'runtime_evidence': EVIDENCE + '/runtime-manifest.json.gz',
               'independent_reviews': [EVIDENCE + '/review-runtime.json', EVIDENCE + '/review-root.json'],
               'evidence_sha256': {},
               'expected': {name: receipt[name] for name in ('native_sha256', 'runtime_config',
                                                            'runtime_manifest_sha256')}}
    if profile['expected']['runtime_config'] != old['expected']['runtime_config']:
        raise ValueError('MinIO runtime configuration changed')
    evidence = ROOT / EVIDENCE
    evidence.mkdir(parents=True, exist_ok=True)

    def copy(source, name, compressed=False):
        destination = evidence / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        raw = source.read_bytes()
        destination.write_bytes(gzip.compress(raw, mtime=0) if compressed else raw)
        profile['evidence_sha256'][destination.relative_to(ROOT).as_posix()] = gate.digest(destination)

    copy(args.build_dir / 'build-receipt.json', 'build-receipt.json')
    copy(args.build_dir / 'image-inspect.json', 'image-inspect.json')
    copy(args.build_dir / 'actual-payload/runtime-manifest.json.gz', 'runtime-manifest.json.gz')
    copy(args.trivy, 'raw-trivy.json.gz', True)
    copy(args.native_dir / 'full-grype.json', 'raw-grype.json.gz', True)
    copy(args.native_dir / 'full-syft.json', 'full-syft.json.gz', True)
    collection = gate.prior.read(args.native_dir / 'collector.json')
    if (collection['operational_collection'] is not True
            or collection['image_id'] != receipt['image_id']
            or collection['config_digest'] != receipt['config_digest']
            or collection['sbom_sha256'] != gate.digest(args.native_dir / 'full-syft.json')
            or collection['report_sha256'] != gate.digest(args.native_dir / 'full-grype.json')):
        raise ValueError('Actual controlled Grype/Syft collection differs from the candidate')
    for name in ('collector.json', 'database-preparation.json', 'publisher-snapshot.json'):
        copy(args.native_dir / name, name)
    copy(args.build_dir / 'build.log', 'build.log')
    regression_log = args.regression_log.read_text(encoding='utf-8')
    for name in ('TestNormalThrift025ParquetRoundTrip', 'TestNormalNativeRepairPreservesValidCSV',
                 'TestPinnedLegacyPart2024PreservesCompleteMetadata'):
        if '=== RUN   ' + name not in regression_log or '--- PASS: ' + name not in regression_log:
            raise ValueError('A required actual benign MinIO regression did not pass: ' + name)
    copy(args.regression_log, 'normal-regressions.log')
    profile['regression_log'] = EVIDENCE + '/normal-regressions.log'
    native = {}
    with tarfile.open(args.build_dir / 'actual-payload/rootfs.tar') as archive:
        for name in ('silo', 'mcli'):
            data = archive.extractfile('usr/share/deepresearch-build/' + name + '-native.json').read()
            native[name] = json.loads(data)
            destination = evidence / (name + '-native.json')
            destination.write_bytes(data)
            profile['evidence_sha256'][destination.relative_to(ROOT).as_posix()] = gate.digest(destination)
    profile['native_sha256'] = {name: gate.prior.shared.manifest_hash(data) for name, data in native.items()}
    profile['native_module_migration'] = {}
    for name, current in native.items():
        previous = gate.prior.read(ROOT / old['go_source_bridge'][name]['native'])
        before, after = gate.prior.common.native_modules(previous), gate.prior.common.native_modules(current)
        changes = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
        profile['native_module_migration'][name] = {
            key: {'before': before.get(key), 'after': after.get(key)} for key in sorted(changes)}
    trivy = gate.prior.read(args.trivy)
    sbom = gate.prior.read(args.native_dir / 'full-syft.json')
    profile['scan_expected'] = {
        'trivy_inventory_sha256': gate.prior.trivy_rows(trivy)[1],
        'syft_inventory_sha256': gate.prior.sbom_inventory(sbom, receipt['diff_ids']),
    }
    profile['grype_advisory_migration'] = {
        'raw_reports_modified': False,
        'reason': 'The current official Grype database removes the same three glibc BIG5 rows already independently reviewed by the retained exact Trivy migration proof; package/runtime inputs remain unchanged.',
        'removed_exact_rows': [{'id': 'CVE-2026-122054', 'package': name, 'version': '2.34-290.el9_9',
                                'severity': 'Medium', 'type': 'rpm', 'namespace': 'redhat:distro:redhat:9',
                                'locations': ['/var/lib/rpm/rpmdb.sqlite']}
                               for name in ('glibc', 'glibc-common', 'glibc-minimal-langpack')],
    }
    for name in ('scripts/check_minio_otel_20261115.py', 'scripts/prepare_minio_otel_profile_20261115.py',
                 'tests/test_minio_otel_20261115.py'):
        profile['evidence_sha256'][name] = gate.digest(ROOT / name)
    profile['subject_sha256'] = gate.review_subject(profile)
    actual = {'image_id': receipt['image_id'], 'config_digest': receipt['config_digest'],
              'inspection': inspection,
              'diff_ids': receipt['diff_ids'], 'runtime_manifest_sha256': receipt['runtime_manifest_sha256'],
              'runtime_manifest': gate.prior.read_gzip(args.build_dir / 'actual-payload/runtime-manifest.json.gz')}
    gate.verify_build(actual, receipt, profile, allow_preparation=receipt['preparation_only'])
    gate.verify_native(args.build_dir, profile, receipt['image_id'])
    gate.assess(trivy, gate.prior.read(args.native_dir / 'full-grype.json'), sbom, inspection, receipt, profile)
    write(args.output, profile)
    return profile


def assemble(args):
    profile = gate.prior.read(args.draft)
    for name in profile['independent_reviews']:
        profile['evidence_sha256'][name] = gate.digest(ROOT / name)
    profile['status'] = 'verified'
    write(ROOT / gate.PROFILE_PATH, profile)
    gate.load_profile()
    return profile


def refresh_checker(args):
    profile = gate.prior.read(ROOT / gate.PROFILE_PATH)
    if profile['source_inputs'] != gate.builder.source_inputs():
        raise ValueError('Metadata refresh cannot change the reviewed compilation inputs')
    for name in ('scripts/check_minio_otel_20261115.py', 'scripts/prepare_minio_otel_profile_20261115.py',
                 'tests/test_minio_otel_20261115.py'):
        profile['evidence_sha256'][name] = gate.digest(ROOT / name)
    for name in profile['independent_reviews']:
        profile['evidence_sha256'].pop(name, None)
    profile['status'] = 'prepared'
    profile['subject_sha256'] = gate.review_subject(profile)
    write(args.output, profile)
    return profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    draft = sub.add_parser('create')
    for name in ('build-dir', 'trivy', 'native-dir', 'regression-log', 'output'):
        draft.add_argument('--' + name, type=Path, required=True)
    final = sub.add_parser('assemble-reviewed')
    final.add_argument('--draft', type=Path, required=True)
    refresh = sub.add_parser('refresh-checker-evidence')
    refresh.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    profile = {'create': create, 'assemble-reviewed': assemble,
               'refresh-checker-evidence': refresh_checker}[args.operation](args)
    print(json.dumps({'subject_sha256': profile['subject_sha256'], 'status': profile['status']}))


if __name__ == '__main__':
    main()
