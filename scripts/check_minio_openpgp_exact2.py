"""Two exact technical tuples; frozen MinIO artifact checker stays intact."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import stat
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import image_disposition as original  # noqa: E402

REVIEW_PATH = Path('deployment/security/minio-openpgp2-technical-review.json')
# The independent reviewer must author the complete facts and actual byte pin.
REVIEW_SHA256 = '39513648eacbdd29bebe30fbe70e6efae5a7c474bb255c13122b892c75e6d247'
ADVISORY = 'GO-2026-5932'
PACKAGE, VERSION, SEVERITY = 'golang.org/x/crypto', 'v0.58.0', 'UNKNOWN'
OLD_PACKAGES = {'golang.org/x/crypto/openpgp' + suffix for suffix in
                ('', '/packet', '/armor', '/clearsign', '/errors', '/elgamal', '/s2k')}
TARGETS = {'usr/bin/mcli', 'usr/bin/silo'}


def strict_json(data):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise original.DispositionError('Duplicate JSON key in evidence')
            result[key] = value
        return result

    def reject_constant(_):
        raise original.DispositionError('Nonfinite JSON number in evidence')

    return json.loads(data, object_pairs_hook=unique_pairs, parse_constant=reject_constant)


def validate_report(report):
    if not isinstance(report, dict) or not isinstance(report.get('Results'), list) or not report['Results']:
        raise original.DispositionError('Complete nonempty scanner Results array required')
    for record in report['Results']:
        if not isinstance(record, dict):
            raise original.DispositionError('Scanner result must be an object')
        for key in ('Packages', 'Vulnerabilities', 'Secrets'):
            if key in record and (not isinstance(record[key], list)
                                  or any(not isinstance(row, dict) for row in record[key])):
                raise original.DispositionError('Scanner finding and inventory fields must be object arrays')


def require_new_output(path):
    root = ROOT.resolve()
    allowed = (ROOT / '.cache/eval').resolve()
    if not allowed.is_relative_to(root):
        raise original.DispositionError('Output cache resolves outside the checkout')
    if not path.resolve().is_relative_to(allowed):
        raise original.DispositionError('Output must be inside this checkout evaluation cache')
    for ancestor in (path, *path.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            raise original.DispositionError('Output path contains a symbolic link or reparse point')
    if path.exists():
        raise original.DispositionError('Output must be a new nonexistent file')


def write_new_output(path, result):
    require_new_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    require_new_output(path)
    # Exclusive creation also prevents a late hard-link substitution overwriting evidence.
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(result, indent=2) + '\n')


def validate_output_paths(args):
    outputs = [args.original_output, args.output]
    inputs = [args.archive, args.scan, args.inspect, ROOT / REVIEW_PATH, ROOT / original.POLICY]
    for output in outputs:
        require_new_output(output)
    for index, output in enumerate(outputs):
        for protected in inputs + outputs[index + 1:]:
            if output.resolve() == protected.resolve() or (
                    output.exists() and protected.exists() and output.samefile(protected)):
                raise original.DispositionError('Output aliases input evidence or another output')


def load_technical_review(binding):
    path = original.checked_file(ROOT, REVIEW_PATH.as_posix())
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != REVIEW_SHA256:
        raise original.DispositionError('Independent exact-target technical review missing or changed')
    review = strict_json(raw)
    if (review['actor'] != 'independent_security_review_agent'
            or review['review_kind'] != 'technical_applicability'
            or review['human_risk_acceptance'] is not False
            or review['original_binding_sha256'] != original.APPROVED_BINDING_SHA256
            or review['decision'] != 'NOT_APPLICABLE_TO_EXACT_BOUND_TARGET_BUILDS'
            or review['advisory'] != ADVISORY
            or set(review['excluded_imports']) != OLD_PACKAGES
            or review['source_commit'] != binding['source_commit']
            or review['expires'] != binding['expires']):
        raise original.DispositionError('Technical review is outside the exact original artifact scope')
    for key in ('index_digest', 'platform_manifest_digest', 'config_digest', 'archive_sha256'):
        if review[key] != binding[key]:
            raise original.DispositionError('Technical subject differs from original MinIO approval')
    rows = review['targets']
    if len(rows) != 2 or {row['target'] for row in rows} != TARGETS:
        raise original.DispositionError('Technical review must bind exactly two unique targets')
    for row in rows:
        if (row['elf_sha256'] != binding['binary_sha256'][row['target']]
                or row['package'] != PACKAGE or row['version'] != VERSION
                or row['severity'] != SEVERITY or row['advisory'] != ADVISORY
                or row['production_graph_complete'] is not True
                or row['old_openpgp_packages'] != []
                or row['old_openpgp_named_functions'] != 0
                or not isinstance(row['function_count'], int) or row['function_count'] <= 100
                or not isinstance(row['package_count'], int) or row['package_count'] <= 100):
            raise original.DispositionError('Independent technical fact tuple is incomplete or inconsistent')
    return review


def apply_exact_technical_facts(result, report, binding, review):
    validate_report(report)
    if any(record.get('Secrets') for record in report['Results']):
        raise original.DispositionError('Every secret finding remains blocked independently')
    if result['passed']:
        return {'passed': True, 'technical_applied': [], 'original_gate': result}
    expected = Counter((target, ADVISORY, PACKAGE, VERSION, SEVERITY) for target in TARGETS)
    actual = Counter()
    for record in report['Results']:
        for finding in record.get('Vulnerabilities', []):
            if finding.get('Severity') not in {'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'}:
                actual[(record.get('Target'), finding.get('VulnerabilityID'), finding.get('PkgName'),
                        finding.get('InstalledVersion'), finding.get('Severity'))] += 1
    if (actual != expected or result['verified_fixed_count'] != 1
            or result['raw_counts'] != {'HIGH': 1, 'CRITICAL': 0}
            or result['unresolved_count'] != 2
            or result['unresolved'] != [{'reason': 'unknown_severity', 'finding': ADVISORY}] * 2
            or result['verified_fixed'] != [{'vulnerability': binding['vulnerability'],
                                            'target': binding['finding']['target'], 'disposition': 'FIXED'}]):
        raise original.DispositionError('New, duplicate, changed, or unreviewed finding remains blocked')
    return {'passed': True, 'original_gate': result,
            'technical_applied': [{'target': row['target'], 'advisory': ADVISORY,
                'package': PACKAGE, 'version': VERSION, 'raw_severity': SEVERITY,
                'disposition': 'NOT_APPLICABLE_TO_EXACT_BOUND_TARGET_BUILD',
                'elf_sha256': row['elf_sha256']} for row in review['targets']]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--scan', required=True, type=Path)
    parser.add_argument('--inspect', required=True, type=Path)
    parser.add_argument('--scanner-exit-code', required=True, type=int)
    parser.add_argument('--original-output', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    # Reject unsafe destinations before any evidence write, including error output.
    try:
        validate_output_paths(args)
    except (ValueError, OSError) as error:
        print(json.dumps({'passed': False, 'mode': 'failed_closed', 'error': str(error)[:220]}))
        return 1
    try:
        binding = original.load_binding()  # All original pins, evidence, expiry and authorization.
        archive = original.verify_archive(args.archive, binding)  # Exact full OCI/ELF facts.
        report = strict_json(args.scan.read_bytes())
        validate_report(report)
        inspection = strict_json(args.inspect.read_bytes())
        if isinstance(inspection, list):
            if len(inspection) != 1:
                raise original.DispositionError('Exactly one actual image inspection is required')
            inspection = inspection[0]
        base = {**original.evaluate_scan(report, inspection, binding, scanner_exit_code=args.scanner_exit_code),
                'archive': archive, 'raw_scan_sha256': original.file_hash(args.scan),
                'mode': 'authorized_exact_artifact_FIXED'}
        write_new_output(args.original_output, base)
        review = load_technical_review(binding)
        result = {**apply_exact_technical_facts(base, report, binding, review),
                  'mode': 'original_FIXED_plus_exact2_independent_technical_facts',
                  'raw_scan_sha256': base['raw_scan_sha256'], 'raw_report_modified': False,
                  'original_approval_expires': binding['expires'], 'review_sha256': REVIEW_SHA256}
    except (ValueError, TypeError, KeyError, OSError, tarfile.TarError) as error:
        result = {'passed': False, 'mode': 'failed_closed', 'error': str(error)[:220]}
    try:
        write_new_output(args.output, result)
    except (ValueError, OSError) as error:
        print(json.dumps({'passed': False, 'mode': 'failed_closed', 'error': str(error)[:220]}))
        return 1
    print(json.dumps({'passed': result['passed'], 'mode': result['mode']}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
