"""Verify the complete locally built Grafana subject without inheriting OCI approval."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_release_20261107 as retained  # noqa: E402
from scripts.check_image_secrets import assess as assess_secrets  # noqa: E402
from scripts.check_native_jvm import validate_source, NativeGateError  # noqa: E402

PROFILE_PATH = 'deployment/security/grafana-source-20261110-profile.json'
PROFILE = 'grafana-source-20261110'
IMAGE = 'deepresearch-grafana-source:20261110'
DOCKERFILE = 'deployment/docker/Dockerfile.grafana-20261110'
MAIN = 'usr/share/grafana/bin/grafana'
HEX = re.compile(r'[a-f0-9]{64}')
SEVERITIES = {'UNKNOWN', 'NEGLIGIBLE', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'}
AUTO_FILES = {'etc/hostname', 'etc/hosts', 'etc/resolv.conf', '.dockerenv'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def checked(root, name):
    if not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('Evidence path must stay within this checkout')
    path = root / name
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('A required exact source or evidence file is unavailable')
    return path


def load_profile(root=ROOT):
    profile = read(checked(root, PROFILE_PATH))
    if (profile.get('schema') != 'grafana-source-build-20261110-v1' or profile.get('status') != 'verified'
            or profile.get('profile') != PROFILE or profile.get('image') != IMAGE
            or profile.get('dockerfile') != DOCKERFILE or profile.get('context') != '.'):
        raise ValueError('The exact new Grafana source-build review is not ready')
    sources, evidence = profile.get('source_inputs'), profile.get('evidence_sha256')
    if not isinstance(sources, dict) or DOCKERFILE not in sources or not isinstance(evidence, dict) or not evidence:
        raise ValueError('Complete source inputs and technical evidence are required')
    if any(sources[name] != evidence[name] for name in sources.keys() & evidence.keys()):
        raise ValueError('Source and evidence identities disagree')
    for name, expected in (sources | evidence).items():
        if not isinstance(expected, str) or not HEX.fullmatch(expected) or digest(checked(root, name)) != expected:
            raise ValueError('Reviewed source/evidence bytes changed: ' + name)
    original = retained.load_request(root)['binding']
    native = profile['expected']['native_sha256']
    if (set(native) != set(original['binary_sha256']) or len(native) != 14
            or any(not HEX.fullmatch(value) for value in native.values())
            or native[MAIN] == original['binary_sha256'][MAIN]
            or any(native[name] != old for name, old in original['binary_sha256'].items() if name != MAIN)):
        raise ValueError('Exactly the new main and thirteen unchanged plugin ELFs are required')
    if set(profile['expected']['trivy_inventory_sha256']) != {'alpine', 'Node.js', *native}:
        raise ValueError('Complete sixteen-target scan coverage is required')
    for scanner in ('Trivy', 'Grype'):
        rows = profile['reviewed_findings'][scanner]
        keys = [finding_key(row) for row in rows]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate reviewed scanner tuples are forbidden')
        for row in rows:
            if (row.get('disposition') not in {'FIXED', 'NOT_AFFECTED'} or not row.get('technical_basis')
                    or not row.get('evidence') or any(name not in evidence for name in row['evidence'])):
                raise ValueError('Each finding requires exact technical evidence; no risk waiver')
    if any(name not in evidence for name in profile['independent_reviews']):
        raise ValueError('Independent reviews must be bound by exact evidence hashes')
    reviews = [read(checked(root, name)) for name in profile['independent_reviews']]
    if (len(reviews) < 2 or len({r.get('actor') for r in reviews}) != len(reviews)
            or any(r.get('facts_passed') is not True or r.get('risk_acceptance') is not False
                   or r.get('binary_redistribution_approved') is not False
                   or r.get('subject') != review_subject(profile) for r in reviews)):
        raise ValueError('Independent technical reviews are incomplete')
    return profile


def review_subject(profile):
    return {'profile': PROFILE, 'source_inputs_sha256': manifest_hash(profile['source_inputs']),
        'native_sha256': profile['expected']['native_sha256'],
        'runtime_manifest_sha256': profile['expected']['runtime_manifest_sha256'],
        'complete_expected_sha256': manifest_hash(profile['expected']),
        'technical_evidence_sha256': manifest_hash({name: value for name, value in profile['evidence_sha256'].items()
            if name not in profile['independent_reviews']}),
        'reviewed_findings_sha256': manifest_hash(profile['reviewed_findings'])}


def finding_key(row):
    key = tuple(row[name] for name in ('id', 'target', 'package', 'version', 'severity'))
    if key[-1] not in SEVERITIES or not all(isinstance(v, str) and v for v in key):
        raise ValueError('A scanner tuple lacks its exact severity, target or package identity')
    return key


def verify_receipt(receipt, inspection, profile, *, root=ROOT):
    if (receipt.get('schema') != 'grafana-source-build-receipt-v1' or receipt.get('profile') != PROFILE
            or receipt.get('image') != IMAGE or receipt.get('source_inputs') != profile['source_inputs']
            or receipt.get('native_sha256') != profile['expected']['native_sha256']
            or receipt.get('runtime_manifest_sha256') != profile['expected']['runtime_manifest_sha256']
            or receipt.get('runtime_config') != profile['expected']['runtime_config']
            or receipt.get('image_id') != inspection.get('Id') or receipt.get('diff_ids') != inspection.get('RootFS', {}).get('Layers')
            or inspection.get('Os') != 'linux' or inspection.get('Architecture') != 'amd64'):
        raise ValueError('Actual build, full runtime files, native bytes or configuration differ from the review')
    actual = inspection.get('Config', {})
    if runtime_config(actual) != receipt['runtime_config']:
        raise ValueError('Actual runtime entrypoint/environment differs from the verified build')
    for name, expected in profile['source_inputs'].items():
        if digest(checked(root, name)) != expected:
            raise ValueError('Source changed after the verified build')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', receipt.get('config_digest', '')):
        raise ValueError('A portable image config identity is required')
    return receipt


def runtime_config(config):
    return {name: config.get(name) for name in (
        'User', 'Env', 'Entrypoint', 'Cmd', 'WorkingDir', 'ExposedPorts', 'Volumes', 'Healthcheck', 'StopSignal')}


def manifest_hash(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def full_scan_rows(trivy, grype):
    rows = {'Trivy': [], 'Grype': []}
    for result in trivy['Results']:
        for finding in result.get('Vulnerabilities', []):
            rows['Trivy'].append({'id': finding['VulnerabilityID'], 'target': result['Target'].lstrip('/'),
                'package': finding['PkgName'], 'version': finding['InstalledVersion'], 'severity': finding['Severity']})
    if grype.get('ignoredMatches'):
        raise ValueError('Filtered native matches cannot authorize the source build')
    for match in grype['matches']:
        locations = {v['path'].lstrip('/') for v in match['artifact']['locations']}
        if not locations:
            raise ValueError('Native finding has no exact target')
        for target in sorted(locations):
            rows['Grype'].append({'id': match['vulnerability']['id'], 'target': target,
                'package': match['artifact']['name'], 'version': match['artifact']['version'],
                'severity': match['vulnerability']['severity'].upper()})
    return rows


def assess_scans(trivy, grype, sbom, inspection, receipt, profile, *, trivy_exit_code, grype_exit_code):
    if trivy_exit_code != 0 or grype_exit_code != 0:
        raise ValueError('Both complete scanners must finish successfully')
    secret = assess_secrets(trivy)
    if not secret['passed']:
        raise ValueError('Secret findings block the source build; values remain private')
    retained.shared.empty_filter_markers(trivy)
    if trivy.get('Errors') or grype.get('errors') or grype.get('Errors'):
        raise ValueError('Scanner errors cannot be treated as complete reports')
    if (trivy.get('SchemaVersion') != 2 or trivy.get('ArtifactType') != 'container_image'
            or trivy.get('Trivy') != {'Version': '0.75.0'}
            or trivy['Metadata'].get('ImageID') != inspection['Id']
            or trivy['Metadata'].get('DiffIDs') != receipt['diff_ids']):
        raise ValueError('Complete Trivy scan belongs to another image or scanner')
    now = datetime.now(timezone.utc)
    retained.old_maintenance.fresh_timestamp(trivy.get('CreatedAt'), timedelta(hours=24), now=now)
    descriptor = grype.get('descriptor', {})
    if (descriptor.get('name') != 'grype' or descriptor.get('version') != '0.121.0'
            or descriptor.get('db', {}).get('status', {}).get('valid') is not True):
        raise ValueError('Pinned complete native scanner identity differs')
    retained.old_maintenance.fresh_timestamp(descriptor.get('timestamp'), timedelta(hours=24), now=now)
    retained.old_maintenance.fresh_timestamp(descriptor.get('db', {}).get('status', {}).get('built'), timedelta(days=7), now=now)
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
        retained.shared.empty_filter_markers(result)
        key = result['Type'] if result.get('Class') == 'os-pkgs' else result['Target']
        if key in targets:
            raise ValueError('Duplicate source-build scan target')
        packages = retained.shared.package_counter(result['Packages'])
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
        'verified_all_severity_findings': verified, 'raw_counts': {k: dict(Counter(r['severity'] for r in v)) for k, v in observed.items()},
        'raw_reports_modified': False, 'binary_redistribution_approved': False,
        'scope': 'Exactly reviewed locally built runtime; original OCI approval and historical scans unchanged.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build-receipt', 'scan', 'grype-scan', 'sbom', 'inspect', 'output'):
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
        import uuid
        actual = capture(inspection['Id'], args.output.parent / ('grafana-payload-check-' + uuid.uuid4().hex[:12]))
        if actual['runtime_manifest_sha256'] != profile['expected']['runtime_manifest_sha256']:
            raise ValueError('Actual complete image payload differs from the reviewed runtime')
        result = assess_scans(read(args.scan), read(args.grype_scan), read(args.sbom), inspection, receipt, profile,
            trivy_exit_code=args.scanner_exit_code, grype_exit_code=args.grype_exit_code)
    except (ValueError, KeyError, TypeError, OSError, NativeGateError, subprocess.SubprocessError) as error:
        result = {'passed': False, 'mode': 'source_build_failed_closed', 'error_type': type(error).__name__,
            'error': str(error)[:240] if isinstance(error, ValueError) else 'Required complete build or scan evidence unavailable',
            'binary_redistribution_approved': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2) + '\n').encode())
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
