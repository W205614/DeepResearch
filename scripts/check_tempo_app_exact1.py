"""Apply one independently verified fixed-source fact; preserve the raw HIGH report.

The workflow supplies a completed, full vuln,secret scan. This helper retains the
ordinary HIGH,CRITICAL threshold and requires actual extracted ELF/native identity
on every run. It does not authorize an OCI artifact or accept human risk.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = Path('deployment/security/tempo-app1-technical-review.json')
REVIEW_SHA256 = 'c319a8662c5142a382ddab4088ea48763fcba5cf952d93621175dffd656823d9'
EVIDENCE = Path('deployment/security/tempo-app1-evidence')
TAG = 'deepresearch-scan-tempo:ci'
TARGET = 'tempo'
PACKAGE = 'github.com/grafana/tempo'
VERSION = 'v0.0.0-20261021135948-f317720e7e51+dirty'
ADVISORY = 'CVE-2026-28377'
SCOPE = (TARGET, ADVISORY, PACKAGE, VERSION, 'HIGH')
LEVELS = frozenset({'UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'})
SHA256 = re.compile(r'sha256:[a-f0-9]{64}\Z')
MATERIAL_NAMES = ('materials.json', 'image-inspect.json', 'native-buildinfo.json',
                  'native-buildinfo.txt', 'runtime-version.txt', 'tempo.elf')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON keys are not evidence')
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError('Nonfinite JSON values are not evidence')

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_file(root, relative, limit=64 * 1024**2):
    path = root / relative
    require(not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
            and path.is_file() and 0 < path.stat().st_size <= limit,
            'Missing, linked, escaping or oversized evidence file')
    return path


def load_review(now=None):
    path = checked_file(ROOT, REVIEW_PATH)
    require(file_hash(path) == REVIEW_SHA256, 'Independent technical review bytes changed')
    review = strict_json(path.read_bytes())
    require(review['schema'] == 'tempo-app1-fixed-source-technical-review-v1'
            and review['actor_kind'] == 'automated_independent_agent'
            and review['review_kind'] == 'technical_fixed_source_applicability'
            and review['human_risk_acceptance'] is False and review['facts_passed'] is True
            and review['decision'] == 'FIXED_IN_EXACT_BOUND_ELF_SOURCE',
            'No exact independent fixed-source technical review')
    scope = review['scope']
    require((scope['target'], scope['advisory'], scope['package'],
             scope['installed_version'], scope['raw_severity']) == SCOPE
            and scope['fixed_version'] == '2.10.3', 'Technical review subject changed')
    require(datetime.fromisoformat(review['expires']) > (now or datetime.now(timezone.utc)),
            'Technical review expired; independent re-review is required')
    for relative, digest in (review['evidence_files'] | review['fixed_subject']['input_sha256']).items():
        require(file_hash(checked_file(ROOT, relative)) == digest,
                'Reviewed source, recipe, collector, lock or technical evidence changed')
    return review


def verify_materials(directory, review, go='go'):
    paths = {name: checked_file(directory, name, 128 * 1024**2 if name == 'tempo.elf'
                                else 4 * 1024**2) for name in MATERIAL_NAMES}
    material = strict_json(paths['materials.json'].read_bytes())
    inspection = strict_json(paths['image-inspect.json'].read_bytes())
    native_bytes = paths['native-buildinfo.json'].read_bytes()
    native = strict_json(native_bytes)
    subject, scope = review['fixed_subject'], review['scope']
    require(material['schema'] == 'tempo-actual-build-materials-v1'
            and material['security_gate_claim'] is False
            and material['technical_disposition'] == 'NOT_DECIDED',
            'Actual collector materials are missing or have inherited an approval')
    require(type(inspection) is dict and SHA256.fullmatch(inspection['Id'])
            and inspection['Id'] == material['actual_image_id']
            and inspection['Architecture'] == subject['architecture']
            and inspection['Os'] == subject['os']
            and inspection['Config'] == subject['config']
            and TAG in inspection['RepoTags'], 'Actual image identity or configuration differs')
    layers = inspection['RootFS']['Layers']
    require(inspection['RootFS']['Type'] == 'layers' and type(layers) is list
            and len(layers) == subject['total_diff_ids']
            and len(set(layers)) == len(layers)
            and all(type(value) is str and SHA256.fullmatch(value) for value in layers)
            and layers == material['actual_diff_ids']
            and layers[:-3] == subject['base_diff_ids'], 'Actual runtime base or DiffIDs differ')
    elf = paths['tempo.elf']
    with elf.open('rb') as stream:
        require(stream.read(4) == b'\x7fELF', 'Actual target is not an ELF executable')
    require(elf.stat().st_size == material['elf_bytes'] == scope['elf_bytes']
            and file_hash(elf) == material['elf_sha256'] == scope['elf_sha256'],
            'Actual extracted ELF differs from the independently reviewed subject')
    require(hashlib.sha256(native_bytes).hexdigest() == material['native_buildinfo_sha256']
            == subject['native_buildinfo_sha256'], 'Native JSON bytes changed')
    # Inspect actual ELF bytes again; supplied JSON cannot substitute for this operation.
    actual_json = subprocess.check_output([go, 'version', '-m', '-json', str(elf)],
                                          stderr=subprocess.PIPE, timeout=60)
    require(strict_json(actual_json) == native, 'Actual ELF native identity differs from supplied JSON')
    text = paths['native-buildinfo.txt'].read_bytes()
    actual_text = subprocess.check_output([go, 'version', '-m', str(elf)],
                                          stderr=subprocess.PIPE, timeout=60)
    require(text.split(b'\n', 1)[0].endswith(b': go1.27.3')
            and text.split(b'\n', 1)[1] == actual_text.split(b'\n', 1)[1]
            and hashlib.sha256(text.split(b'\n', 1)[1]).hexdigest()
            == subject['native_text_body_sha256'], 'Native text inventory differs from actual ELF')
    require(file_hash(paths['runtime-version.txt']) == material['runtime_version_sha256']
            == subject['runtime_version_sha256'], 'Actual isolated runtime -version differs')
    require(native['GoVersion'] == 'go1.27.3'
            and native['Path'] == PACKAGE + '/cmd/tempo'
            and native['Main'] == {'Path': PACKAGE, 'Version': VERSION}
            and len(native['Deps']) == subject['native_dependency_count'],
            'Native application/module identity differs')
    return inspection, material, native


def empty_filter_markers(record):
    for name in ('Errors', 'ModifiedFindings', 'SuppressedFindings'):
        require(name not in record or (type(record[name]) is list and not record[name]),
                'Incomplete, filtered or suppressed scanner evidence')


def assess(report, inspection, material, native, review, scanner_exit_code, now=None):
    require(type(scanner_exit_code) is int and scanner_exit_code == 0,
            'Full vulnerability/secret scanner did not complete successfully')
    require(type(report) is dict and report.get('SchemaVersion') == 2
            and report.get('Trivy') == {'Version': '0.75.0'}
            and report.get('ArtifactType') == 'container_image'
            and report.get('ArtifactName') == TAG and SHA256.fullmatch(report['ArtifactID'])
            and type(report.get('ReportID')) is str and report['ReportID'],
            'Missing or unsupported complete image scan')
    empty_filter_markers(report)
    created = datetime.fromisoformat(report['CreatedAt'].replace('Z', '+00:00'))
    current = now or datetime.now(timezone.utc)
    require(created.tzinfo is not None and current - timedelta(hours=24) <= created
            <= current + timedelta(minutes=5), 'Image scan is stale or dated in the future')
    metadata = report['Metadata']
    layers = inspection['RootFS']['Layers']
    require(metadata['ImageID'] == inspection['Id'] == material['actual_image_id']
            and metadata['DiffIDs'] == layers == material['actual_diff_ids']
            and metadata['Reference'] == TAG and TAG in metadata['RepoTags']
            and metadata['ImageConfig']['rootfs']['diff_ids'] == layers
            and metadata['ImageConfig']['config'] == inspection['Config']
            and metadata['ImageConfig']['architecture'] == inspection['Architecture']
            and metadata['ImageConfig']['os'] == inspection['Os'],
            'Fresh scan does not bind to the actual inspected/materialized image')
    results = report['Results']
    require(type(results) is list and len(results) == 2, 'Image target inventory changed')
    by_target = {record['Target']: record for record in results}
    require(len(by_target) == 2 and set(by_target) == {TARGET, TAG + ' (debian 12.13)'},
            'Missing, duplicate or additional scan target')
    go_result, os_result = by_target[TARGET], by_target[TAG + ' (debian 12.13)']
    require(go_result['Class'] == 'lang-pkgs' and go_result['Type'] == 'gobinary'
            and os_result['Class'] == 'os-pkgs' and os_result['Type'] == 'debian',
            'Scanner target types differ')
    binary_layer = metadata['Layers'][len(review['fixed_subject']['base_diff_ids'])]
    require(len(metadata['Layers']) == len(layers)
            and [layer['DiffID'] for layer in metadata['Layers']] == layers
            and SHA256.fullmatch(binary_layer['Digest']), 'Scanner layer identity differs')
    expected_packages = Counter((dep.get('Replace', dep)['Path'], dep.get('Replace', dep)['Version'])
                                for dep in native['Deps'])
    expected_packages.update([(PACKAGE, VERSION), ('stdlib', 'v' + native['GoVersion'][2:])])
    for record in results:
        empty_filter_markers(record)
        for name in ('Packages', 'Vulnerabilities', 'Secrets'):
            require(name not in record or type(record[name]) is list, 'Malformed scanner collection')
        require(not record.get('Secrets'), 'Every secret remains blocked; values are not emitted')
        require(type(record.get('Packages')) is list and record['Packages'],
                'Complete package inventory is required')
    packages = go_result['Packages']
    require(len(packages) == review['fixed_subject']['go_package_count']
            and Counter((pkg['Name'], pkg['Version']) for pkg in packages) == expected_packages
            and all(pkg['Layer'] == {key: metadata['Layers'][
                review['fixed_subject']['go_package_layer_index'][pkg['Name']]][key]
                for key in ('Digest', 'DiffID')} and pkg['AnalyzedBy'] == 'gobinary' for pkg in packages),
            'Full Go scan inventory differs from exact native effective modules/ELF layer')
    require(Counter((pkg['Name'], pkg['Version']) for pkg in os_result['Packages'])
            == Counter(map(tuple, review['fixed_subject']['os_package_inventory'])),
            'Runtime OS package identity differs')
    blocking, all_rows, counts = Counter(), Counter(), Counter()
    for record in results:
        inventory = {(pkg['Name'], pkg['Version']) for pkg in record['Packages']}
        for finding in record.get('Vulnerabilities', []):
            require(type(finding) is dict and finding.get('Severity') in LEVELS
                    and all(type(finding.get(key)) is str and finding[key]
                            for key in ('VulnerabilityID', 'PkgName', 'InstalledVersion')),
                    'Malformed vulnerability finding')
            row = (record['Target'], finding['VulnerabilityID'], finding['PkgName'],
                   finding['InstalledVersion'], finding['Severity'])
            require((finding['PkgName'], finding['InstalledVersion']) in inventory,
                    'Vulnerability row has no matching actual package')
            all_rows[row] += 1
            counts[finding['Severity']] += 1
            if finding['Severity'] in {'HIGH', 'CRITICAL'}:
                blocking[row] += 1
                require(row == SCOPE and finding.get('FixedVersion') == '2.10.3'
                        and finding.get('Layer') == {'Digest': binary_layer['Digest'],
                                                    'DiffID': binary_layer['DiffID']},
                        'New or changed HIGH/CRITICAL finding remains blocked')
    require(all(count == 1 for count in all_rows.values()) and blocking == Counter({SCOPE: 1}),
            'Reviewed finding is missing, duplicated, changed or accompanied by another blocker')
    return {'passed': True, 'mode': 'independent_exact1_fixed_source_technical_fact',
            'human_risk_acceptance': False, 'oci_authorization': False,
            'raw_report_modified': False, 'review_sha256': REVIEW_SHA256,
            'raw_counts': dict(counts), 'secret_findings': 0,
            'original_gate': {'passed': False, 'threshold': ['HIGH', 'CRITICAL'],
                              'raw_counts': {'HIGH': 1, 'CRITICAL': 0}, 'unresolved': [ADVISORY]},
            'technical_applied': [{'target': TARGET, 'advisory': ADVISORY, 'package': PACKAGE,
                                   'version': VERSION, 'raw_severity': 'HIGH',
                                   'disposition': 'FIXED_IN_EXACT_BOUND_ELF_SOURCE',
                                   'elf_sha256': material['elf_sha256']}],
            'actual_image_id': inspection['Id'], 'actual_diff_ids': layers,
            'scan_artifact_id': report['ArtifactID'], 'scan_report_id': report['ReportID']}


def validate_output(args):
    cache = (ROOT / '.cache/eval').resolve()
    output = args.output.resolve()
    require(cache.is_relative_to(ROOT.resolve())
            and not args.output.exists() and not args.output.is_symlink()
            and output.is_relative_to(cache) and output != args.scan.resolve()
            and not output.is_relative_to(args.materials_dir.resolve()),
            'Output must be a distinct new file under the workspace .cache/eval')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan', required=True, type=Path)
    parser.add_argument('--materials-dir', required=True, type=Path)
    parser.add_argument('--scanner-exit-code', required=True, type=int)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--go', default='go')
    args = parser.parse_args(argv)
    try:
        validate_output(args)
        review = load_review()
        require(not args.scan.is_symlink() and 0 < args.scan.stat().st_size <= 64 * 1024**2,
                'Raw scan file bound failed')
        inspection, material, native = verify_materials(args.materials_dir, review, args.go)
        result = assess(strict_json(args.scan.read_bytes()), inspection, material, native,
                        review, args.scanner_exit_code)
        result['raw_scan_sha256'] = file_hash(args.scan)
    except (ValueError, TypeError, KeyError, OSError, IndexError, AttributeError,
            subprocess.SubprocessError) as error:
        result = {'passed': False, 'mode': 'failed_closed', 'error_type': type(error).__name__,
                  'error': str(error)[:200] if isinstance(error, ValueError) else 'Evidence validation failed'}
    try:
        validate_output(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(result, indent=2) + '\n')
    except (ValueError, OSError):
        print(json.dumps({'passed': False, 'mode': 'failed_closed', 'error': 'Unsafe output path'}))
        return 1
    print(json.dumps({'passed': result['passed'], 'mode': result['mode']}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
