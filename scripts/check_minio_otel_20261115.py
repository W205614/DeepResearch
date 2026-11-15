"""Verify the exact Go upgrade over the retained MinIO native-repair subject."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_minio_os_20261113 as prior  # noqa: E402
from scripts import build_minio_otel_20261115 as builder  # noqa: E402
from scripts.capture_go_native_20261112 import capture as capture_native  # noqa: E402

PROFILE_PATH = 'deployment/security/minio-otel-source-20261115-profile.json'
DOCKERFILE = builder.DOCKERFILE
CHANGED_RUNTIME_FILES = {'usr/bin/silo', 'usr/bin/mcli'} | {
    'usr/share/deepresearch-build/' + name + suffix
    for name in ('silo', 'mcli') for suffix in ('-buildinfo.txt', '-native.json', '-graph.json')}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_profile(root=ROOT):
    profile = prior.read(root / PROFILE_PATH)
    prior.common.require(profile['schema'] == 'minio-otel-source-profile-20261115-v1'
                         and profile['binary_redistribution_approved'] is False,
                         'MinIO otel profile scope differs')
    prior.load_profile(root)
    prior.common.require(profile['prior_profile'] == prior.PROFILE_PATH
                         and profile['prior_profile_sha256'] == digest(root / prior.PROFILE_PATH),
                         'Exact retained MinIO native-repair subject changed')
    prior.common.require(profile['source_inputs'] == builder.source_inputs(root),
                         'MinIO otel source inputs changed')
    for name, expected in profile['evidence_sha256'].items():
        prior.common.require(digest(prior.shared.checked(root, name)) == expected,
                             'MinIO otel evidence changed: ' + name)
    regression_log = (root / profile['regression_log']).read_text(encoding='utf-8')
    for name in ('TestNormalThrift025ParquetRoundTrip', 'TestNormalNativeRepairPreservesValidCSV',
                 'TestPinnedLegacyPart2024PreservesCompleteMetadata'):
        prior.common.require('=== RUN   ' + name in regression_log and '--- PASS: ' + name in regression_log,
                             'An actual benign MinIO regression did not run and pass')
    prior.common.require(profile['subject_sha256'] == review_subject(profile),
                         'MinIO otel review subject changed')
    reviews = [prior.read(root / name) for name in profile['independent_reviews']]
    prior.common.require(profile['status'] == 'verified' and len(reviews) == 2
                         and len({r['actor'] for r in reviews}) == 2
                         and all(r['actor_kind'] == 'automated_independent_agent'
                                 and r['facts_passed'] is True and r['risk_acceptance'] is False
                                 and r['subject_sha256'] == profile['subject_sha256'] for r in reviews),
                         'Two distinct actual MinIO otel technical reviews are required')
    return profile


def review_subject(profile):
    subject = {k: v for k, v in profile.items()
               if k not in ('status', 'subject_sha256', 'independent_reviews', 'evidence_sha256')}
    subject['evidence_sha256'] = {name: value for name, value in profile['evidence_sha256'].items()
                                  if name not in profile['independent_reviews']}
    return prior.shared.manifest_hash(subject)


def verify_build(facts, receipt, profile, *, allow_preparation=False):
    inspection = facts['inspection']
    labels = inspection['Config']['Labels']
    revision = labels['org.opencontainers.image.revision']
    prior.common.require(receipt['schema'] == 'minio-otel-source-build-receipt-20261115-v1'
                         and receipt['profile'] == builder.PROFILE and receipt['image'] == builder.IMAGE
                         and receipt['binary_redistribution_approved'] is False
                         and inspection['Os'] == 'linux' and inspection['Architecture'] == 'amd64'
                         and receipt['preparation_only'] is allow_preparation
                         and re.fullmatch('[0-9a-f]{40}' +
                                          (r'\+uncommitted-validation-inputs' if allow_preparation else ''), revision)
                         and receipt['project_source_revision'] == revision
                         and receipt['source_inputs_sha256'] == labels['org.deepresearch.project.inputs_sha256']
                         == prior.shared.manifest_hash(profile['source_inputs'])
                         and prior.shared.runtime_config(inspection['Config']) == receipt['runtime_config'],
                         'MinIO source labels, platform, runtime configuration or preparation state differs')
    prior.common.require(receipt['source_inputs'] == profile['source_inputs'] == builder.source_inputs()
                         and receipt['image_id'] == facts['image_id']
                         and receipt['diff_ids'] == facts['diff_ids']
                         and receipt['config_digest'] == facts['config_digest']
                         and prior.common.reviewed_runtime_config_equivalent(
                             receipt['runtime_config'], profile['expected']['runtime_config'])
                         and receipt['native_sha256'] == profile['expected']['native_sha256'],
                         'Actual MinIO otel runtime or receipt differs')
    old = prior.load_profile()
    old_rows = prior.read_gzip(ROOT / old['runtime_evidence']['current'])
    before = {r['path']: r for r in old_rows}
    after = {r['path']: r for r in facts['runtime_manifest']}
    prior.common.require(before.keys() == after.keys(), 'MinIO runtime file set changed')
    def stable_row(path, row):
        return {key: value for key, value in row.items()
                if path != prior.DATABASE_PATH or key != 'sha256'}

    for name in before.keys() - CHANGED_RUNTIME_FILES:
        prior.common.require(stable_row(name, before[name]) == stable_row(name, after[name]),
                             'Unrelated MinIO payload changed: ' + name)
    reviewed_rows = prior.read_gzip(ROOT / profile['runtime_evidence'])
    reviewed = {row['path']: row for row in reviewed_rows}
    prior.common.require(reviewed.keys() == after.keys()
                         and prior.shared.manifest_hash(reviewed_rows) == profile['expected']['runtime_manifest_sha256']
                         and facts['runtime_manifest_sha256'] == receipt['runtime_manifest_sha256']
                         and all(stable_row(name, reviewed[name]) == stable_row(name, row)
                                 for name, row in after.items()),
                         'Complete MinIO otel runtime differs from the reviewed payload')


def verify_graph_native(graph, native):
    prior.common.require(native['Path'] in graph and graph[native['Path']]['Name'] == 'main',
                         'Production graph entry is not the actual MinIO executable entry')
    modules = prior.common.native_modules(native)
    observed = {}

    def identity(module):
        value = {key: module[key] for key in ('Path', 'Version', 'Sum') if key in module}
        if 'Replace' in module:
            value['Replace'] = identity(module['Replace'])
        return value

    for row in graph.values():
        if 'Module' not in row:
            continue
        module = row['Module']
        path = module['Path']
        if module.get('Main') is True:
            prior.common.require(path == native['Main']['Path'], 'Production main source module differs')
            observed[path] = native['Main']
        else:
            value = identity(module)
            prior.common.require(path in modules and value == modules[path],
                                 'Production graph module version/sum/replacement differs from actual ELF')
            if path in observed:
                prior.common.require(observed[path] == value, 'Production graph module identity is inconsistent')
            observed[path] = value
    prior.common.require(observed == modules, 'Production graph and actual ELF module closures differ')
    reached, pending = set(), [native['Path']]
    while pending:
        path = pending.pop()
        if path in reached:
            continue
        reached.add(path)
        pending.extend(graph[path].get('Imports', []))
    prior.common.require(reached == graph.keys(), 'Production graph includes packages outside the actual entry closure')


def verify_native(directory, profile, image):
    native = {}
    old = prior.load_profile()
    actual = capture_native(image, profile['expected']['native_sha256'],
                            directory / ('native-verify-' + uuid.uuid4().hex[:12]))
    with tarfile.open(directory / 'actual-payload/rootfs.tar') as archive:
        database = directory / ('rpm-native-verify-' + uuid.uuid4().hex + '.sqlite')
        database.write_bytes(archive.extractfile(prior.DATABASE_PATH).read())
        rpm = prior.database_facts(database)
        prior.common.require(rpm['stable_sha256'] == old['expected']['rpm_database_stable_sha256'],
                             'Complete MinIO RPM database schema, rows or package headers changed')
        for name in ('silo', 'mcli'):
            raw = archive.extractfile('usr/share/deepresearch-build/' + name + '-native.json').read()
            native[name] = json.loads(raw)
            prior.common.require(actual['usr/bin/' + name]['native'] == native[name],
                                 'Actual MinIO ELF parser differs from stored native facts')
            prior.common.require(native[name]['GoVersion'] == 'go1.27.4', 'MinIO compiler is not fixed')
            prior.common.require(prior.shared.manifest_hash(native[name]) == profile['native_sha256'][name],
                                 'Actual MinIO native module closure differs')
            graph = prior.common.decode_graph(archive.extractfile(
                'usr/share/deepresearch-build/' + name + '-graph.json').read())
            verify_graph_native(graph, native[name])
            prior.common.require(not any(path == 'golang.org/x/net/html'
                                         or path.startswith('golang.org/x/net/html/')
                                         or path == 'golang.org/x/crypto/openpgp'
                                         or path.startswith('golang.org/x/crypto/openpgp/') for path in graph),
                                 'Affected HTML/OpenPGP package entered the compiled MinIO closure')
            if name == 'silo':
                prior.common.require('reader.go' in graph['github.com/minio/minio/internal/s3select/csv']['GoFiles'],
                                     'The source-fixed CSV reader is absent from the actual compiled package')
            previous = prior.read(ROOT / old['go_source_bridge'][name]['native'])
            prior.common.require(all(previous[key] == native[name][key] for key in ('Path', 'Main', 'Settings')),
                                 'MinIO source identity or build settings changed')
            before, after = prior.common.native_modules(previous), prior.common.native_modules(native[name])
            changes = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
            otel = {'go.opentelemetry.io/otel' + suffix for suffix in
                    ('', '/sdk', '/metric', '/trace', '/sdk/metric', '/log')}
            prior.common.require(changes == (otel if name == 'silo' else set()),
                                 'Only the exact OpenTelemetry family may migrate in MinIO')
            for module in changes:
                expected_before = None if module.endswith('/log') else 'v1.45.0'
                prior.common.require((before.get(module) or {}).get('Version') == expected_before
                                     and after[module]['Version'] == 'v1.47.0'
                                     and set(after[module]) == {'Path', 'Version', 'Sum'},
                                     'MinIO OpenTelemetry migration version or replacement differs')
            prior.common.require(changes == set(profile['native_module_migration'][name]),
                                 'Actual MinIO native module migration differs from the reviewed closure')
            for module in changes:
                prior.common.require({'before': before.get(module), 'after': after.get(module)}
                                     == profile['native_module_migration'][name][module],
                                     'Exact MinIO changed module version/sum differs')
    modules = prior.common.native_modules(native['silo'])
    prior.common.require(all(modules[name]['Version'] == 'v1.47.0' for name in
                             ('go.opentelemetry.io/otel', 'go.opentelemetry.io/otel/sdk',
                              'go.opentelemetry.io/otel/trace')),
                         'Actual MinIO executable still links affected OpenTelemetry')
    return native


def assess(trivy, grype, sbom, inspection, receipt, profile):
    old = prior.load_profile()
    # The original 21/23 exact findings retain their independent source/OS proofs.
    # Only inventory identities are replaced by those of the newly reviewed build.
    current = {**old, 'expected': {**old['expected'], **profile['scan_expected']}}
    removed = [{'id': 'CVE-2026-122054', 'package': name, 'version': '2.34-290.el9_9',
                'severity': 'Medium', 'type': 'rpm', 'namespace': 'redhat:distro:redhat:9',
                'locations': ['/var/lib/rpm/rpmdb.sqlite']}
               for name in ('glibc', 'glibc-common', 'glibc-minimal-langpack')]
    prior.common.require(profile['grype_advisory_migration']['removed_exact_rows'] == removed,
                         'Only the three already-reviewed glibc advisory rows may leave Grype')
    prior.verify_trivy_advisory_migration(old)
    before, after = prior.row_counter(old['reviewed_findings']['Grype']), prior.row_counter(prior.grype_rows(grype))
    prior.common.require(before - after == prior.row_counter(removed) and not after - before,
                         'Grype findings changed beyond the exact known glibc advisory correction')
    current['reviewed_findings'] = {**old['reviewed_findings'], 'Grype': prior.grype_rows(grype)}
    result = prior.assess(trivy, grype, sbom, inspection, receipt, current, 0, 0)
    result['profile'] = builder.PROFILE
    result['otel_147_verified'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build-receipt', 'inspect', 'scan', 'grype-scan', 'sbom', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--scanner-exit-code', type=int, required=True)
    parser.add_argument('--grype-exit-code', type=int, required=True)
    args = parser.parse_args()
    try:
        prior.common.require(args.scanner_exit_code == args.grype_exit_code == 0, 'Full scanner failed')
        profile = load_profile()
        receipt = prior.read(args.build_receipt)
        inspection = prior.read(args.inspect)
        if isinstance(inspection, list):
            inspection, = inspection
        from scripts.grafana_source_build_facts_20261110 import capture
        actual_dir = args.output.parent / ('minio-otel-actual-' + uuid.uuid4().hex)
        actual = capture(inspection['Id'], actual_dir / 'actual-payload', save_config=True)
        verify_build(actual, receipt, profile)
        verify_native(actual_dir, profile, inspection['Id'])
        result = assess(prior.read(args.scan), prior.read(args.grype_scan),
                        prior.read(args.sbom), inspection, receipt, profile)
    except (ValueError, KeyError, OSError) as error:
        result = {'passed': False, 'error': str(error)[:240]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2) + '\n').encode())
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
