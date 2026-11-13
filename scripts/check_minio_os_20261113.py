"""Verify the separate MinIO OS/curl/Thrift source subject and complete raw reports."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_minio_os_20261113 as builder  # noqa: E402
from scripts import go_stdlib_rebuild_20261112 as common  # noqa: E402
from scripts import check_grafana_source_build_20261110 as shared  # noqa: E402
from scripts.check_image_secrets import assess as assess_secrets  # noqa: E402
from scripts.check_native_jvm import validate_source  # noqa: E402
from scripts.minio_rpm_facts_20261113 import database_facts  # noqa: E402

PROFILE = builder.PROFILE
IMAGE = builder.IMAGE
DOCKERFILE = builder.DOCKERFILE
PROFILE_PATH = 'deployment/security/minio-os-source-20261113-profile.json'
SEVERITIES = ['UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
DATABASE_PATH = 'var/lib/rpm/rpmdb.sqlite'


def read(path):
    return json.loads(Path(path).read_bytes())


def digest(path):
    return shared.digest(path)


def review_subject(profile):
    return common.review_subject(profile)


def validate_profile(profile, root=ROOT, *, reviewed=True):
    common.require(profile.get('schema') == 'minio-os-source-profile-20261113-v1'
                   and profile.get('profile') == PROFILE and profile.get('image') == IMAGE
                   and profile.get('context') == '.' and profile.get('dockerfile') == DOCKERFILE
                   and profile.get('threshold_severities') == SEVERITIES,
                   'MinIO native repair namespace or complete severity boundary differs')
    common.require(profile.get('binary_redistribution_approved') is False
                   and profile.get('old_binary_approval_inherited') is False
                   and profile.get('retained_unreviewed_findings') == [],
                   'New source approval cannot inherit old binaries or unreviewed rows')
    common.require(profile['source_inputs'] == builder.source_inputs(root)
                   and all(profile['source_inputs'][name] == profile['evidence_sha256'][name]
                           for name in profile['source_inputs'].keys() & profile['evidence_sha256'].keys()),
                   'Complete current build inputs or overlapping evidence bindings differ')
    for name, expected in (profile['source_inputs'] | profile['evidence_sha256']).items():
        common.require(shared.digest(shared.checked(root, name)) == expected,
                       'Exact MinIO source or evidence bytes changed: ' + name)
    common.require(profile['review_subject_sha256'] == review_subject(profile), 'MinIO review subject changed')
    common.require(len(profile['os_resolution']) == 34
                   and Counter(row['resolution'] for row in profile['os_resolution']) ==
                   Counter({'signed_vendor_update': 17, 'verified_source_fix': 4, 'exact_component_not_affected': 13}),
                   'Every one of the original 34 OS rows requires its exact bounded disposition')
    matrix = read(shared.checked(root, profile['os_resolution_evidence']))
    common.require(matrix['dispositions'] == profile['os_resolution']
                   and all(binding['path'] in profile['evidence_sha256']
                           and binding['sha256'] == profile['evidence_sha256'][binding['path']]
                           for binding in matrix['primary_sources'].values()),
                   'Complete OS disposition source evidence differs')
    verify_os_matrix(profile, matrix, root)
    crypto = read(shared.checked(root, profile['embedded_crypto_scope']['review']))
    common.require(crypto['openssl_source_package_affected'] is True
                   and crypto['openssl_binary_fixed_claim'] is False
                   and crypto['curl_sha256'] == profile['expected']['native_sha256']['usr/bin/curl']
                   and profile['embedded_crypto_scope']['binary_fixed_claim'] is False
                   and crypto['supporting_files_sha256'].keys() == profile['embedded_crypto_support'].keys()
                   and all(profile['evidence_sha256'][profile['embedded_crypto_support'][name]] == expected
                           for name, expected in crypto['supporting_files_sha256'].items()),
                   'Known affected embedded OpenSSL/current CLI scope source evidence differs')
    common.require(set(profile['expected']['native_sha256']) ==
                   {'usr/bin/mcli', 'usr/bin/silo', 'usr/bin/curl', 'usr/lib64/libc.so.6',
                    'usr/lib64/ld-linux-x86-64.so.2', 'usr/lib64/libtic.so.6.2'},
                   'Complete repaired native target set differs')
    common.require(go_bridge(profile, root) == profile['go_source_bridge']['facts'],
                   'Actual new compiler/module/import source bridge differs')
    verify_go_lock_migration(profile, root)
    prior_go = read(shared.checked(root, 'deployment/security/minio-source-20261112-profile.json'))
    common.require(row_counter([row for row in profile['reviewed_findings']['Trivy']
                                if not row['target'].startswith('os:')])
                   == row_counter(prior_go['reviewed_findings']['Trivy'])
                   and profile['go7_technical_bridge']['abnormal_csv_regression_reexecuted'] is False
                   and profile['go7_technical_bridge']['source_commit'] == '0cd388c666f8211f552604ae73448a066e9c25ce',
                   'Only the exact seven original Go findings with the new source bridge are reviewed')
    csv = profile['go7_technical_bridge']['csv']
    common.require(digest(shared.checked(root, csv['product_source_reader'])) ==
                   '51cece44627f51103893c5e8f84eda642523b415040e89e660d3802da773e496'
                   and csv['compiled_package'] == 'github.com/minio/minio/internal/s3select/csv',
                   'The fixed CSV reader source identity changed')
    if reviewed:
        reviews = [read(shared.checked(root, name)) for name in profile['independent_reviews']]
        common.require(profile.get('status') == 'verified' and len(reviews) >= 2
                       and len({row['actor'] for row in reviews}) == len(reviews)
                       and all(row.get('facts_passed') is True and row.get('risk_acceptance') is False
                               and row.get('actor_kind') == 'automated_independent_agent'
                               and row.get('binary_redistribution_approved') is False
                               and row.get('subject_sha256') == profile['review_subject_sha256'] for row in reviews)
                       and set(profile['independent_reviews']) <= profile['evidence_sha256'].keys(),
                       'Two distinct actual independent technical reviews are required')
    return profile


def verify_os_matrix(profile, matrix, root=ROOT):
    original = read_gzip(shared.checked(root, matrix['primary_sources']['original_raw']['path']))
    expected = [{'original_target': result['Target'], 'original_finding': row}
                for result in original['Results'] if result.get('Class') == 'os-pkgs'
                for row in result.get('Vulnerabilities', []) if row['Severity'] in ('LOW', 'MEDIUM')]
    observed = [{key: row[key] for key in ('original_target', 'original_finding')}
                for row in profile['os_resolution']]
    common.require(row_counter(expected) == row_counter(observed)
                   and len(row_counter(observed)) == len(observed),
                   'Exact original OS findings were replaced, duplicated or omitted')
    current = [row for row in profile['reviewed_findings']['Trivy'] if row['target'].startswith('os:')]
    retained = []
    for row in profile['os_resolution']:
        old = row['original_finding']
        matches = [value for value in current if value['id'] == old['VulnerabilityID']
                   and value['package'] == old['PkgName']]
        if row['resolution'] == 'signed_vendor_update':
            common.require(not matches, 'A vendor-update finding remains in the current raw scan')
        else:
            common.require(len(matches) == 1 and matches[0]['severity'] == old['Severity']
                           and matches[0]['version'] == row['current_installed_version'],
                           'An exact source-fixed/component-scoped row disappeared or changed')
            retained.extend(matches)
    common.require(row_counter(retained) == row_counter(current),
                   'An additional current OS finding has no exact original disposition')


def go_bridge(profile, root=ROOT):
    facts = {}
    for name in ('mcli', 'silo'):
        paths = profile['go_source_bridge'][name]
        current = read(shared.checked(root, paths['native']))
        previous = read(shared.checked(root, paths['prior_native']))
        common.require(current['GoVersion'] == previous['GoVersion'] == 'go1.27.4'
                       and all(current[key] == previous[key] for key in ('Path', 'Main', 'Settings')),
                       'Actual Go compiler/source/build settings changed')
        before, after = common.native_modules(previous), common.native_modules(current)
        common.require(before.keys() == after.keys(), 'Actual native module closure changed')
        changed = {key for key in before if before[key] != after[key]}
        common.require(changed == ({'github.com/apache/thrift'} if name == 'silo' else set()),
                       'Unexpected actual linked module migration')
        if name == 'silo':
            common.require(before['github.com/apache/thrift']['Version'] == 'v0.24.0'
                           and after['github.com/apache/thrift'] == {
                               'Path': 'github.com/apache/thrift', 'Version': 'v0.25.0',
                               'Sum': 'h1:6LLlvoPjy8Cc/0b5TYRcTR3RZa2W0at3WQRcOkDwVGs='},
                           'Actual linked Thrift version or official module sum differs')
        graph = common.decode_graph(gzip.decompress(shared.checked(root, paths['graph']).read_bytes()))
        old = common.decode_graph(gzip.decompress(shared.checked(root, paths['prior_graph']).read_bytes()))
        common.require(graph.keys() == old.keys()
                       and all(row.get('Standard') == old[key].get('Standard') for key, row in graph.items()),
                       'Actual complete compiled package paths changed')
        changed_imports = {key for key, row in graph.items()
                           if row.get('Imports', []) != old[key].get('Imports', [])}
        expected_imports = {'github.com/apache/thrift/lib/go/thrift'} if name == 'silo' else set()
        common.require(changed_imports == expected_imports,
                       'Unexpected compiled package import migration')
        if name == 'silo':
            target = 'github.com/apache/thrift/lib/go/thrift'
            common.require('testing' in old[target]['Imports']
                           and graph[target]['Imports'] == [value for value in old[target]['Imports'] if value != 'testing'],
                           'Thrift import migration differs from the exact removal of testing')
        common.require(not any(key == 'golang.org/x/net/html' or key.startswith('golang.org/x/net/html/')
                               or key == 'golang.org/x/crypto/openpgp'
                               or key.startswith('golang.org/x/crypto/openpgp/') for key in graph),
                       'Affected HTML/OpenPGP package entered the actual compiled closure')
        if name == 'silo':
            common.require('reader.go' in graph['github.com/minio/minio/internal/s3select/csv']['GoFiles'],
                           'The fixed CSV reader is absent from the actual compiled package')
        for key, row in graph.items():
            module, prior = row.get('Module', {}), old[key].get('Module', {})
            if module.get('Path') != 'github.com/apache/thrift':
                def identity(value):
                    return {k: identity(v) if isinstance(v, dict) else v
                            for k, v in value.items() if k not in ('Dir', 'GoMod')}
                common.require(identity(module) == identity(prior), 'Compiled module identity changed: ' + key)
            else:
                common.require(module['Version'] == 'v0.25.0' and prior['Version'] == 'v0.24.0',
                               'Compiled Thrift graph differs')
        facts[name] = {'go_version': current['GoVersion'], 'packages': len(graph),
                       'linked_modules': len(after), 'changed_modules': sorted(changed),
                       'complete_import_closure': True, 'html_openpgp_absent': True,
                       'abnormal_csv_probe_rerun': False}
    return facts


def verify_go_lock_migration(profile, root=ROOT):
    paths = profile['go_lock_migration']
    def read_bytes(name):
        return shared.checked(root, paths[name]).read_bytes()
    before, after = read_bytes('mod_before'), read_bytes('mod_after')
    old = b'github.com/apache/thrift v0.24.0'
    common.require(before.count(old) == 1 and after == before.replace(old, b'github.com/apache/thrift v0.25.0'),
                   'go.mod migration includes an unrelated change')
    sums = [b'github.com/apache/thrift v0.25.0 h1:6LLlvoPjy8Cc/0b5TYRcTR3RZa2W0at3WQRcOkDwVGs=',
            b'github.com/apache/thrift v0.25.0/go.mod h1:4lwyToijD9T70w7GlevFk7FnpY60Wv8I8krMkF6FkAo=']
    old_sum, new_sum = read_bytes('sum_before').splitlines(), read_bytes('sum_after').splitlines()
    common.require([line for line in new_sum if line not in sums] == old_sum
                   and all(new_sum.count(line) == 1 and line not in old_sum for line in sums),
                   'Complete go.sum migration differs from two official Thrift sums')
    def modules(raw):
        decoder, cursor, values = json.JSONDecoder(), 0, {}
        text = raw.decode()
        while cursor < len(text):
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor == len(text):
                break
            row, cursor = decoder.raw_decode(text, cursor)
            common.require(row['Path'] not in values and not row.get('Error'), 'Invalid full MVS module graph')
            values[row['Path']] = row
        return values
    first, second = modules(read_bytes('mvs_before')), modules(read_bytes('mvs_after'))
    common.require(first.keys() == second.keys() and len(first) == 824
                   and {key for key in first if first[key] != second[key]} == {'github.com/apache/thrift'},
                   'Complete MVS migration includes an unrelated module')
    for kind, name in [('parquet', 'TestNormalThrift025ParquetRoundTrip'),
                        ('csv', 'TestNormalNativeRepairPreservesValidCSV'),
                        ('legacy', 'TestPinnedLegacyPart2024PreservesCompleteMetadata')]:
        text = shared.checked(root, paths[kind]).read_text(encoding='utf-8')
        common.require('no tests to run' not in text.lower() and 'FAIL' not in text
                       and '=== RUN   ' + name in text and '--- PASS: ' + name in text,
                       'Every required benign Go regression must actually run and pass')


def load_profile(root=ROOT):
    return validate_profile(read(shared.checked(root, PROFILE_PATH)), root)


def verify_receipt(receipt, inspection, profile, root=ROOT):
    common.require(receipt.get('schema') == 'minio-os-source-build-receipt-20261113-v1'
                   and receipt.get('profile') == PROFILE and receipt.get('image') == IMAGE
                   and receipt.get('binary_redistribution_approved') is False,
                   'Actual MinIO receipt belongs to another build contract')
    common.require(receipt['source_inputs'] == profile['source_inputs'] == builder.source_inputs(root),
                   'Current MinIO build source inputs differ')
    common.require(receipt['image_id'] == inspection['Id']
                   and receipt['diff_ids'] == inspection['RootFS']['Layers']
                   and receipt['runtime_config'] == shared.runtime_config(inspection['Config'])
                   and common.reviewed_runtime_config_equivalent(receipt['runtime_config'],
                                                                 profile['expected']['runtime_config'])
                   and receipt['native_sha256'] == profile['expected']['native_sha256'],
                   'Actual image/runtime/native facts differ from the new review')
    common.require(receipt['rpm_database']['stable_sha256'] == profile['expected']['rpm_database_stable_sha256'],
                   'Complete logical RPM database/header facts differ')
    return receipt


def verify_payload(facts, database, profile, *, receipt, root=ROOT):
    common.require(facts['image_id'] == receipt['image_id']
                   and facts['config_digest'] == receipt['config_digest']
                   and facts['diff_ids'] == receipt['diff_ids'],
                   'Independent actual export/portable config differs from the receipt')
    expected = read_gzip(shared.checked(root, profile['runtime_evidence']['current']))
    actual = facts['runtime_manifest']
    before, after = {r['path']: r for r in expected}, {r['path']: r for r in actual}
    common.require(len(before) == len(expected) and len(after) == len(actual) and before.keys() == after.keys(),
                   'Complete MinIO runtime file set differs')
    for name, row in before.items():
        observed = after[name]
        if name == DATABASE_PATH:
            common.require({k: v for k, v in row.items() if k != 'sha256'} ==
                           {k: v for k, v in observed.items() if k != 'sha256'},
                           'RPM database type/size/ownership/mode differs')
        else:
            common.require(row == observed, 'Actual complete MinIO runtime differs: ' + name)
    common.require(after['var/lib/rpm/rpmdb.sqlite-wal']['bytes'] == 0,
                   'An uncheckpointed RPM WAL cannot be omitted from logical verification')
    rpm = database_facts(database)
    common.require(rpm['raw_sha256'] == after[DATABASE_PATH]['sha256']
                   and rpm['stable_sha256'] == profile['expected']['rpm_database_stable_sha256'],
                   'Actual complete RPM schema/rows/header bytes/application metadata differ')
    return rpm


def read_gzip(path):
    return json.loads(gzip.decompress(Path(path).read_bytes()))


def trivy_rows(report):
    rows, inventory = [], {}
    shared.retained.shared.empty_filter_markers(report)
    for result in report['Results']:
        shared.retained.shared.empty_filter_markers(result)
        common.require(not result.get('Errors'), 'Trivy target errors remain blocked')
        target = 'os:' + result['Type'] if result.get('Class') == 'os-pkgs' else result['Target']
        common.require(target not in inventory, 'Duplicate full Trivy target')
        inventory[target] = shared.manifest_hash(sorted(shared.retained.shared.package_counter(result['Packages']).items()))
        rows.extend({'target': target, 'id': row['VulnerabilityID'], 'package': row['PkgName'],
                     'version': row['InstalledVersion'], 'severity': row['Severity']}
                    for row in result.get('Vulnerabilities', []))
    return rows, inventory


def grype_rows(report):
    rows = []
    for match in report['matches']:
        artifact, vulnerability = match['artifact'], match['vulnerability']
        rows.append({'id': vulnerability['id'], 'namespace': vulnerability['namespace'],
                     'package': artifact['name'], 'version': artifact['version'],
                     'type': artifact['type'], 'severity': vulnerability['severity'],
                     'locations': sorted(row['path'] for row in artifact['locations'])})
    return rows


def row_counter(rows):
    return Counter(json.dumps(row, sort_keys=True, separators=(',', ':')) for row in rows)


def sbom_inventory(sbom, diff_ids):
    artifacts = sbom['artifacts']
    common.require(len({row['id'] for row in artifacts}) == len(artifacts),
                   'Duplicate full SBOM artifact identity')
    common.require(diff_ids and len(set(diff_ids)) == len(diff_ids), 'Actual ordered image layer identity is incomplete')
    def location_path(path):
        return ((len(path) == 2 and path[0] == 'locations' and isinstance(path[1], int))
                or (len(path) == 4 and path[0] == 'licenses' and isinstance(path[1], int)
                    and path[2] == 'locations' and isinstance(path[3], int))
                or (len(path) == 4 and path[:2] == ('metadata', 'matches')
                    and isinstance(path[2], int) and path[3] == 'location'))
    def bound(value, path=()):
        if isinstance(value, list):
            return [bound(row, path + (index,)) for index, row in enumerate(value)]
        if isinstance(value, dict):
            if location_path(path):
                common.require(value.get('layerID') in diff_ids,
                               'A complete SBOM location has an unknown or missing actual image layer')
            if 'layerID' in value:
                common.require(location_path(path), 'An unreviewed SBOM layer-location structure appeared')
            return {key: diff_ids.index(row) if key == 'layerID' else bound(row, path + (key,))
                    for key, row in value.items()}
        return value
    # Three actual Syft Location shapes carry the same ordered image identity:
    # package locations, declared-license locations, binary-signature matches.
    # Retain each object/field and replace only its verified layer with its index.
    rows = [bound({key: value for key, value in row.items() if key != 'id'}) for row in artifacts]
    return shared.manifest_hash(sorted(row_counter(rows).items()))


def bind_matches(grype, sbom):
    artifacts = {row['id']: row for row in sbom['artifacts']}
    common.require(len(artifacts) == len(sbom['artifacts']), 'Duplicate actual SBOM artifact')
    for match in grype['matches']:
        artifact = match['artifact']
        current = artifacts.get(artifact['id'])
        common.require(current is not None
                       and all(artifact[key] == current[key]
                               for key in ('name', 'version', 'type', 'locations')),
                       'Grype finding artifact differs from the complete actual SBOM')


def scanner_controls(grype, sbom):
    descriptor = sbom['descriptor']
    configuration = descriptor['configuration']
    common.require(descriptor['name'] == 'syft' and descriptor['version'] == '1.55.0'
                   and sbom['schema']['version'] == '16.1.11'
                   and configuration['catalogers']['requested'] == {'default': ['image', 'file']}
                   and configuration['search']['scope'] == 'squashed',
                   'Full default Syft cataloguing changed')
    settings = grype['descriptor']['configuration']
    common.require(not any(settings.get(key) for key in
                           ('exclude', 'only-fixed', 'only-notfixed', 'ignore-wontfix',
                            'vex-documents', 'vex-add', 'fail-on-severity')),
                   'Grype suppression, filtering or incomplete selection is not allowed')
    defaults = []
    for name, upstream, kind in [('kernel-headers', 'kernel', 'rpm'),
                                  ('linux(-.*)?-headers-.*', 'linux.*', 'deb'),
                                  ('linux-libc-dev', 'linux', 'deb'),
                                  ('linux-kbuild-.*', 'linux.*', 'deb')]:
        defaults.append({'vulnerability': '', 'include-aliases': False, 'reason': '', 'namespace': '',
                         'fix-state': '', 'package': {'name': name, 'version': '', 'language': '',
                         'type': kind, 'location': '', 'upstream-name': upstream}, 'vex-status': '',
                         'vex-justification': '', 'match-type': 'exact-indirect-match'})
    common.require(settings.get('ignore') == defaults
                   and not any(row['type'] == rule['package']['type']
                               and re.fullmatch(rule['package']['name'], row['name'])
                               for row in sbom['artifacts'] for rule in defaults),
                   'Only the four built-in unused kernel-header match rules are permitted')
    return {'syft_catalogers_sha256': shared.manifest_hash(configuration['catalogers']),
            'grype_builtin_kernel_header_rules_sha256': shared.manifest_hash(defaults),
            'matching_kernel_header_packages': 0}


def assess(trivy, grype, sbom, inspection, receipt, profile, scanner_exit_code, grype_exit_code):
    common.require(scanner_exit_code == grype_exit_code == 0
                   and trivy.get('SchemaVersion') == 2 and not trivy.get('Errors')
                   and trivy.get('Trivy') == {'Version': '0.75.0'}
                   and trivy.get('ArtifactType') == 'container_image', 'A full scanner failed or changed')
    common.verify_trivy_source(trivy, inspection, receipt)
    common.require(assess_secrets(trivy)['passed'], 'All secret findings remain blocked')
    rows, inventory = trivy_rows(trivy)
    common.require(inventory == profile['expected']['trivy_inventory_sha256']
                   and row_counter(rows) == row_counter(profile['reviewed_findings']['Trivy']),
                   'New, missing, duplicate or changed Trivy rows/inventory remain blocked')
    validate_source(sbom, inspection)
    config = base64.b64decode(sbom['source']['metadata']['config'], validate=True)
    common.require('sha256:' + hashlib.sha256(config).hexdigest() == receipt['config_digest']
                   and shared.runtime_config(json.loads(config)['config']) == receipt['runtime_config'],
                   'Full SBOM does not belong to the actual portable config')
    common.require(sbom_inventory(sbom, receipt['diff_ids']) == profile['expected']['syft_inventory_sha256'],
                   'Complete native/Go SBOM inventory changed')
    common.require(scanner_controls(grype, sbom) == profile['expected']['scanner_controls'],
                   'Complete scanner selection differs')
    bind_matches(grype, sbom)
    common.require(not grype.get('ignoredMatches') and not grype.get('errors')
                   and grype['descriptor']['name'] == 'grype'
                   and grype['descriptor']['version'] == '0.121.0'
                   and grype['descriptor']['db']['status']['valid'] is True
                   and grype['source']['target']['imageID'] == receipt['config_digest']
                   and row_counter(grype_rows(grype)) == row_counter(profile['reviewed_findings']['Grype']),
                   'Full Grype report is filtered, failed, changed or bound to another image')
    return {'passed': True, 'profile': PROFILE, 'raw_reports_modified': False,
            'all_scanned_findings_technically_reviewed': True, 'unreviewed_raw_findings': 0,
            'trivy_raw_counts': dict(Counter(row['severity'] for row in rows)),
            'grype_raw_counts': dict(Counter(row['vulnerability']['severity'] for row in grype['matches'])),
            'secret_count': 0, 'signed_vendor_rows_removed': 17, 'additional_curl_thrift_rows_removed': 26,
            'embedded_crypto_scope': profile['embedded_crypto_scope'],
            'binary_redistribution_approved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build-receipt', 'inspect', 'scan', 'grype-scan', 'sbom', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--scanner-exit-code', required=True, type=int)
    parser.add_argument('--grype-exit-code', required=True, type=int)
    args = parser.parse_args()
    try:
        profile = load_profile()
        inspection = read(args.inspect)
        if isinstance(inspection, list):
            inspection, = inspection
        receipt = verify_receipt(read(args.build_receipt), inspection, profile)
        from scripts.grafana_source_build_facts_20261110 import capture
        payload = args.output.parent / ('minio-os-actual-' + uuid.uuid4().hex)
        facts = capture(inspection['Id'], payload, save_config=True)
        database = args.output.parent / ('actual-rpm-' + uuid.uuid4().hex + '.sqlite')
        with tarfile.open(payload / 'rootfs.tar') as archive:
            database.write_bytes(archive.extractfile(DATABASE_PATH).read())
        verify_payload(facts, database, profile, receipt=receipt)
        result = assess(read(args.scan), read(args.grype_scan), read(args.sbom), inspection, receipt,
                        profile, args.scanner_exit_code, args.grype_exit_code)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, tarfile.TarError) as error:
        result = {'passed': False, 'profile': PROFILE, 'error_type': type(error).__name__}
        if isinstance(error, ValueError):
            result['error'] = str(error)[:240]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2) + '\n').encode())
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
