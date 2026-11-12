"""Verify the separately reviewed fourteen-executable Go 1.27.4 source subject."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_source_build_20261110 as old  # noqa: E402
from scripts import go_stdlib_rebuild_20261112 as common  # noqa: E402
from scripts.retained_source_review_20261112 import snapshot  # noqa: E402
from scripts.check_image_secrets import assess as assess_secrets  # noqa: E402
from scripts.check_native_jvm import NativeGateError, validate_source  # noqa: E402

require = common.require

PROFILE = 'grafana-source-20261112'
IMAGE = 'deepresearch-grafana-source:20261112'
DOCKERFILE = 'deployment/docker/Dockerfile.grafana-20261112'
PROFILE_PATH = 'deployment/security/grafana-source-20261112-profile.json'
read, digest, manifest_hash, runtime_config = old.read, old.digest, old.manifest_hash, old.runtime_config
full_scan_rows = old.full_scan_rows
finding_key = old.finding_key


def preparation_profile(receipt, root=ROOT):
    prior = old.load_profile(snapshot(root))
    native = receipt.get('native_sha256', {})
    require(set(native) == set(prior['expected']['native_sha256']) and len(native) == 14
            and all(old.HEX.fullmatch(value) for value in native.values())
            and all(native[name] != value for name, value in prior['expected']['native_sha256'].items()),
            'All fourteen actual Go executables must be newly rebuilt')
    require(DOCKERFILE in receipt.get('source_inputs', {}), 'Actual new recipe is not bound')
    for name, expected in receipt['source_inputs'].items():
        require(digest(old.checked(root, name)) == expected, 'Actual build source input changed')
    return {'source_inputs': receipt['source_inputs'], 'expected': {
        name: receipt[name] for name in ('native_sha256', 'runtime_manifest_sha256', 'runtime_config')}}


def verify_receipt(receipt, inspection, profile, *, root=ROOT):
    return common.verify_receipt(receipt, inspection, profile, 'grafana', root=root)


def load_profile(root=ROOT):
    return validate_profile(common.load_profile('grafana', root), root)


def validate_profile(profile, root=ROOT):
    prior = old.load_profile(snapshot(root))
    common.verify_migrations(profile, 'grafana', root)
    common.verify_runtime_migration(profile, 'grafana', root)
    require(common.verify_scan_migration(profile, root) == profile['scan_migration_facts'],
            'Recorded complete raw scan migration facts differ')
    require(profile['expected']['runtime_config'] == prior['expected']['runtime_config'],
            'The retained Grafana runtime contract changed')
    for name in ('alpine', 'Node.js'):
        require(profile['expected']['trivy_inventory_sha256'][name]
                == prior['expected']['trivy_inventory_sha256'][name], 'Retained OS/JavaScript inventory changed')
    require(profile['expected']['syft_types'] == prior['expected']['syft_types'], 'Native package coverage changed')
    for scanner in ('Trivy', 'Grype'):
        require(Counter(old.finding_key(row) for row in profile['reviewed_findings'][scanner])
                == Counter(old.finding_key(row) for row in prior['reviewed_findings'][scanner]),
                'The prior exact all-severity findings are not preserved')
    return profile


def assess_scans(trivy, grype, sbom, inspection, receipt, profile, *, trivy_exit_code, grype_exit_code):
    if trivy_exit_code != 0 or grype_exit_code != 0:
        raise ValueError('Both complete scanners must finish successfully')
    secret = assess_secrets(trivy)
    if not secret['passed']:
        raise ValueError('Secret findings block the source build; values remain private')
    old.retained.shared.empty_filter_markers(trivy)
    if trivy.get('Errors') or grype.get('errors') or grype.get('Errors'):
        raise ValueError('Scanner errors cannot be treated as complete reports')
    if (trivy.get('SchemaVersion') != 2 or trivy.get('ArtifactType') != 'container_image'
            or trivy.get('Trivy') != {'Version': '0.75.0'}):
        raise ValueError('Complete Trivy scan belongs to another scanner or schema')
    identity_kind = common.verify_trivy_source(trivy, inspection, receipt)
    now = datetime.now(timezone.utc)
    old.retained.old_maintenance.fresh_timestamp(trivy.get('CreatedAt'), timedelta(hours=24), now=now)
    descriptor = grype.get('descriptor', {})
    if (descriptor.get('name') != 'grype' or descriptor.get('version') != '0.121.0'
            or descriptor.get('db', {}).get('status', {}).get('valid') is not True):
        raise ValueError('Pinned complete native scanner identity differs')
    old.retained.old_maintenance.fresh_timestamp(descriptor.get('timestamp'), timedelta(hours=24), now=now)
    old.retained.old_maintenance.fresh_timestamp(descriptor.get('db', {}).get('status', {}).get('built'), timedelta(days=7), now=now)
    source = sbom.get('source', {}).get('metadata', {})
    config_raw = base64.b64decode(source.get('config', ''), validate=True)
    config = json.loads(config_raw)
    validate_source(sbom, inspection)
    if ('sha256:' + hashlib.sha256(config_raw).hexdigest() != receipt['config_digest']
            or source.get('imageID') != receipt['config_digest']
            or config.get('rootfs', {}).get('diff_ids') != receipt['diff_ids']
            or runtime_config(config.get('config', {})) != receipt['runtime_config']
            or grype.get('source', {}).get('target', {}).get('imageID') != receipt['config_digest']):
        raise ValueError('Full native SBOM/config/report identity chain differs')
    types = dict(Counter(a['type'] for a in sbom['artifacts']))
    if types != profile['expected']['syft_types']:
        raise ValueError('Full native package-type coverage differs')
    targets = {}
    for result in trivy['Results']:
        old.retained.shared.empty_filter_markers(result)
        key = result['Type'] if result.get('Class') == 'os-pkgs' else result['Target']
        if key in targets:
            raise ValueError('Duplicate source-build scan target')
        packages = old.retained.shared.package_counter(result['Packages'])
        targets[key] = hashlib.sha256(json.dumps(sorted(packages.items()), separators=(',', ':')).encode()).hexdigest()
    if targets != profile['expected']['trivy_inventory_sha256']:
        raise ValueError('Complete OS, JavaScript or fourteen-binary package inventory differs')
    observed = full_scan_rows(trivy, grype)
    verified = {}
    for scanner, rows in observed.items():
        expected = Counter(finding_key(row) for row in profile['reviewed_findings'][scanner])
        actual = Counter(finding_key(row) for row in rows)
        if actual != expected:
            raise ValueError('All-severity findings are missing, extra, duplicate or changed: ' + scanner)
        verified[scanner] = len(rows)
    return {'passed': True, 'profile': PROFILE, 'unresolved_count': 0,
        'trivy_identity_kind': identity_kind,
        'verified_all_severity_findings': verified, 'raw_counts': {k: dict(Counter(r['severity'] for r in v)) for k, v in observed.items()},
        'raw_reports_modified': False, 'binary_redistribution_approved': False,
        'scope': 'Exactly reviewed locally built runtime; original OCI approval and historical scans unchanged.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build-receipt', 'scan', 'grype-scan', 'sbom', 'inspect', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--scanner-exit-code', required=True, type=int)
    parser.add_argument('--grype-exit-code', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        profile = load_profile()
        inspection = read(args.inspect)
        if isinstance(inspection, list):
            inspection, = inspection
        receipt = verify_receipt(read(args.build_receipt), inspection, profile)
        from scripts.grafana_source_build_facts_20261110 import capture
        import uuid
        actual = capture(inspection['Id'], args.output.parent / ('go1274-payload-' + uuid.uuid4().hex[:12]))
        require(actual['runtime_manifest_sha256'] == profile['expected']['runtime_manifest_sha256'],
                'The complete actual source-built runtime differs')
        result = assess_scans(read(args.scan), read(args.grype_scan), read(args.sbom), inspection, receipt, profile,
                                 trivy_exit_code=args.scanner_exit_code, grype_exit_code=args.grype_exit_code)
        result.update(profile=PROFILE, fixed_new_stdlib_targets=14, old_binary_approval_inherited=False)
    except (ValueError, KeyError, TypeError, OSError, NativeGateError, subprocess.SubprocessError) as error:
        result = {'passed': False, 'profile': PROFILE, 'error_type': type(error).__name__,
                  'error': str(error)[:240] if isinstance(error, ValueError) else 'Required build or scan evidence unavailable'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2) + '\n').encode())
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
