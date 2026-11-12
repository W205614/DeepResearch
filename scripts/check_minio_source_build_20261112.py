"""Check the Go-fixed MinIO source build with the existing HIGH/CRITICAL/UNKNOWN boundary."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import go_stdlib_rebuild_20261112 as common  # noqa: E402
from scripts import check_grafana_source_build_20261110 as shared  # noqa: E402
from scripts.check_image_secrets import assess as assess_secrets  # noqa: E402
from scripts import check_minio_html_exact4 as legacy  # noqa: E402

PROFILE = 'minio-source-20261112'
IMAGE = 'deepresearch-minio-source:20261112'
DOCKERFILE = 'deployment/docker/Dockerfile.minio-20261112'


def load_profile(root=ROOT):
    return validate_profile(common.load_profile('minio', root), root)


def validate_profile(profile, root=ROOT):
    common.require(profile.get('threshold_severities') == ['HIGH', 'CRITICAL', 'UNKNOWN']
                   and set(profile['expected']['native_sha256']) == {'usr/bin/mcli', 'usr/bin/silo'},
                   'The unchanged MinIO gate boundary or target scope changed')
    common.verify_migrations(profile, 'minio', root)
    common.verify_runtime_migration(profile, 'minio', root)
    common.require(common.verify_scan_migration(profile, root) == profile['scan_migration_facts'],
                   'Recorded complete raw scan migration facts differ')
    binding = legacy.original.load_binding(root)
    config_path = 'deployment/security/minio-source-20261112-evidence/prior-image-config.json'
    common.require(config_path in profile['evidence_sha256']
                   and 'sha256:' + shared.digest(shared.checked(root, config_path)) == binding['config_digest'],
                   'Prior runtime contract is not the original exact portable image config')
    config = shared.read(shared.checked(root, config_path))
    common.require(config['rootfs']['diff_ids'] == binding['diff_ids']
                   and shared.runtime_config(config['config']) == profile['expected']['runtime_config'],
                   'Runtime entrypoint, environment or retained settings changed')
    legacy.exact2.load_technical_review(binding)
    html = legacy.load_review(binding)
    legacy.load_classification_review(binding, html)
    expected = Counter()
    for target in ('usr/bin/mcli', 'usr/bin/silo'):
        expected[(legacy.exact2.ADVISORY, target, legacy.exact2.PACKAGE,
                  legacy.exact2.VERSION, legacy.exact2.SEVERITY)] += 1
        for advisory, severity in legacy.CLASSIFIED_SEVERITIES.items():
            expected[(advisory, target, legacy.PACKAGE, legacy.VERSION, severity)] += 1
    row = binding['finding']
    expected[(binding['vulnerability'], row['target'], row['package'], row['version'], row['severity'])] += 1
    common.require(Counter(shared.finding_key(row) for row in profile['reviewed_findings']['Trivy']) == expected,
                   'Only the original fixed CSV and exact absent OpenPGP/HTML scope may be carried forward')
    required = {'csv': ('TestPinnedCVE202639414StopsUnterminatedRecord',
                        'TestPinnedCVE202639414PreservesLegalLongRecord'),
                'legacy': ('TestPinnedLegacyPart2024PreservesCompleteMetadata',)}
    for kind, names in required.items():
        path = profile['regressions'][kind]
        common.require(path in profile['evidence_sha256'], 'Actual regression output is not bound')
        text = shared.checked(root, path).read_text(encoding='utf-8')
        common.require('no tests to run' not in text.lower() and 'FAIL' not in text
                       and all('=== RUN   ' + name in text and '--- PASS: ' + name in text for name in names),
                       'Every required CSV/compatibility regression must actually run and pass')
    return profile


def verify_receipt(receipt, inspection, profile, *, root=ROOT):
    return common.verify_receipt(receipt, inspection, profile, 'minio', root=root)


def assess(report, inspection, receipt, profile, scanner_exit_code):
    common.require(scanner_exit_code == 0 and report.get('SchemaVersion') == 2
                   and report.get('ArtifactType') == 'container_image'
                   and report.get('Trivy') == {'Version': '0.75.0'},
                   'The complete scan belongs to another image, scanner or failed attempt')
    identity_kind = common.verify_trivy_source(report, inspection, receipt)
    common.require(not report.get('Errors'), 'Scanner errors remain blocked')
    shared.retained.shared.empty_filter_markers(report)
    common.require(assess_secrets(report)['passed'], 'Every secret finding remains independently blocked')
    rows, inventory = [], {}
    for result in report['Results']:
        shared.retained.shared.empty_filter_markers(result)
        common.require(not result.get('Errors'), 'Scanner errors remain blocked')
        key = result['Type'] if result.get('Class') == 'os-pkgs' else result['Target']
        common.require(key not in inventory, 'Duplicate complete scan target')
        packages = shared.retained.shared.package_counter(result['Packages'])
        inventory[key] = shared.manifest_hash(sorted(packages.items()))
        if result['Target'] in profile['expected']['native_sha256']:
            common.require([p['Version'] for p in result['Packages'] if p['Name'] == 'stdlib'] == ['v1.27.4'],
                           'The actual scanned Go executable is not compiled with fixed Go')
        target = 'os:' + result['Type'] if result.get('Class') == 'os-pkgs' else result['Target']
        rows.extend({'target': target, 'id': item['VulnerabilityID'],
                     'package': item['PkgName'], 'version': item['InstalledVersion'], 'severity': item['Severity']}
                    for item in result.get('Vulnerabilities', []))
    common.require(inventory == profile['expected']['trivy_inventory_sha256'], 'Complete package inventory differs')
    observed = Counter(shared.finding_key(row) for row in rows)
    expected = Counter(shared.finding_key(row) for row in profile['reviewed_findings']['Trivy'])
    retained = Counter(shared.finding_key(row) for row in profile['retained_unreviewed_os_findings'])
    common.require(observed == expected + retained, 'New, missing, duplicate or changed findings remain blocked')
    common.require(all(row['severity'] in {'LOW', 'MEDIUM'} for row in profile['retained_unreviewed_os_findings'])
                   and not any(row['id'] == common.CVE for row in rows),
                   'New fixed advisory or unreviewed threshold finding remains blocked')
    return {'passed': True, 'profile': PROFILE, 'unresolved_threshold_count': 0,
            'trivy_identity_kind': identity_kind,
            'threshold_severities': ['HIGH', 'CRITICAL', 'UNKNOWN'], 'raw_report_modified': False,
            'raw_counts': dict(Counter(row['severity'] for row in rows)),
            'retained_unreviewed_low_medium_count': sum(retained.values()), 'all_severity_technical_clear': False,
            'fixed_new_stdlib_targets': 2, 'binary_redistribution_approved': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build-receipt', 'scan', 'inspect', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--scanner-exit-code', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        profile = load_profile()
        inspection = shared.read(args.inspect)
        if isinstance(inspection, list):
            inspection, = inspection
        receipt = verify_receipt(shared.read(args.build_receipt), inspection, profile)
        from scripts.grafana_source_build_facts_20261110 import capture
        import uuid
        actual = capture(inspection['Id'], args.output.parent / ('minio-go1274-payload-' + uuid.uuid4().hex[:12]))
        common.require(actual['runtime_manifest_sha256'] == profile['expected']['runtime_manifest_sha256'],
                       'Actual complete MinIO runtime differs from the new review')
        result = assess(shared.read(args.scan), inspection, receipt, profile, args.scanner_exit_code)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        result = {'passed': False, 'profile': PROFILE, 'error_type': type(error).__name__,
                  'error': str(error)[:240] if isinstance(error, ValueError) else 'Required build or scan evidence unavailable'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2) + '\n').encode())
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
