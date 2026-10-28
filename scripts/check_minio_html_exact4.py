"""Preserve the original gates and apply independently reviewed HTML-only facts."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_minio_openpgp_exact2 as exact2  # noqa: E402

original = exact2.original
REVIEW_PATH = Path('deployment/security/minio-html4-technical-review.json')
REVIEW_SHA256 = 'adab1800183614929156c4625e3787d1be62e95e0c39975ec3db4e06a131ef4f'
ADVISORIES = frozenset({'CVE-2026-78661', 'CVE-2026-97045'})
PACKAGE, VERSION, SEVERITY = 'golang.org/x/net', 'v0.61.0', 'UNKNOWN'


def require(condition, message):
    if not condition:
        raise original.DispositionError(message)


def load_review(binding):
    path = original.checked_file(ROOT, REVIEW_PATH.as_posix())
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == REVIEW_SHA256,
            'Independent exact HTML-target review bytes are missing or changed')
    review = exact2.strict_json(raw)
    require(review['schema'] == 'minio-html4-technical-applicability-review-v1'
            and review['actor_kind'] == 'automated_independent_agent'
            and review['review_kind'] == 'technical_applicability'
            and review['human_risk_acceptance'] is False
            and review['facts_passed'] is True
            and review['decision'] == 'NOT_APPLICABLE_TO_EXACT_BOUND_TARGET_BUILDS'
            and review['original_binding_sha256'] == original.APPROVED_BINDING_SHA256
            and review['expires'] == binding['expires']
            and review['source_commit'] == binding['source_commit'],
            'HTML review is outside the unchanged original artifact scope')
    for key in ('index_digest', 'platform_manifest_digest', 'config_digest', 'archive_sha256'):
        require(review[key] == binding[key], 'HTML review binds another artifact')
    expected = Counter((target, advisory, PACKAGE, VERSION, SEVERITY)
                       for target in exact2.TARGETS for advisory in ADVISORIES)
    rows = review['rows']
    require(type(rows) is list and len(rows) == 4, 'Four independently reviewed rows are required')
    actual = Counter()
    for row in rows:
        actual[(row['target'], row['advisory'], row['package'], row['version'], row['raw_severity'])] += 1
        require(row['elf_sha256'] == binding['binary_sha256'].get(row['target'])
                and row['production_graph_complete'] is True
                and row['html_packages'] == [] and row['html_named_functions'] == []
                and type(row['package_count']) is int and row['package_count'] > 100
                and type(row['function_count']) is int and row['function_count'] > 100,
                'Exact HTML package/ELF absence proof is incomplete')
    require(actual == expected, 'HTML review row identities are missing, changed or duplicated')
    builds = review['target_builds']
    require(set(builds) == exact2.TARGETS, 'Exact production target inventory differs')
    for row in rows:
        build = builds[row['target']]
        require(build['elf_sha256'] == row['elf_sha256']
                and build['production_graph_complete'] is True
                and build['native_effective_dependency_path_version_sum_matches_graph'] is True
                and build['package_count'] == build['unique_package_count'] == row['package_count']
                and build['module_count'] == build['native_module_count'] == row['module_count']
                and build['function_count'] == row['function_count']
                and build['html_packages'] == [] and build['html_named_functions'] == [],
                'Complete native/production dependency identity differs from the reviewed rows')
    evidence = review['evidence_files']
    require(type(evidence) is dict and len(evidence) >= 2, 'Independent source evidence is missing')
    for name, digest in evidence.items():
        require(original.file_hash(original.checked_file(ROOT, name)) == digest,
                'Independent HTML review evidence bytes changed')
    return {**review, 'targets': rows}


def apply_review(base, report, binding, openpgp_review, html_review):
    """Compare every blocking raw row; neither input report is edited or filtered."""
    exact2.validate_report(report)
    require(not any(record.get('Secrets') for record in report['Results']),
            'Every secret finding remains blocked independently')
    expected_openpgp = Counter((target, exact2.ADVISORY, exact2.PACKAGE, exact2.VERSION, exact2.SEVERITY)
                              for target in exact2.TARGETS)
    expected_html = Counter((row['target'], row['advisory'], row['package'], row['version'], row['raw_severity'])
                            for row in html_review['targets'])
    expected = expected_openpgp + expected_html
    actual = Counter()
    for record in report['Results']:
        for finding in record.get('Vulnerabilities', []):
            if finding.get('Severity') not in {'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'}:
                actual[(record.get('Target'), finding.get('VulnerabilityID'), finding.get('PkgName'),
                        finding.get('InstalledVersion'), finding.get('Severity'))] += 1
    unresolved = Counter((row.get('reason'), row.get('finding')) for row in base['unresolved'])
    require(actual == expected and base['passed'] is False
            and base['raw_counts'] == {'HIGH': 1, 'CRITICAL': 0}
            and base['verified_fixed_count'] == 1
            and base['verified_fixed'] == [{'vulnerability': binding['vulnerability'],
                'target': binding['finding']['target'], 'disposition': 'FIXED'}]
            and base['unresolved_count'] == 6
            and unresolved == Counter({('unknown_severity', exact2.ADVISORY): 2,
                **{('unknown_severity', advisory): 2 for advisory in ADVISORIES}}),
            'New, duplicate, changed or unreviewed findings remain blocked')
    require({row['target'] for row in openpgp_review['targets']} == exact2.TARGETS,
            'Original independently reviewed OpenPGP scope differs')
    return {'passed': True, 'original_gate': base, 'raw_report_modified': False,
            'technical_applied': [{'target': row['target'], 'advisory': row['advisory'],
                'package': row['package'], 'version': row['version'], 'raw_severity': row['raw_severity'],
                'elf_sha256': row['elf_sha256'], 'disposition': 'NOT_APPLICABLE_TO_EXACT_BOUND_TARGET_BUILD'}
                for row in html_review['targets']],
            'openpgp_review_sha256': exact2.REVIEW_SHA256, 'html_review_sha256': REVIEW_SHA256}


def validate_outputs(args):
    exact2.validate_output_paths(args)
    extra = argparse.Namespace(**vars(args))
    extra.original_output = args.exact2_output
    exact2.validate_output_paths(extra)
    require(len({path.resolve() for path in (args.original_output, args.exact2_output, args.output)}) == 3,
            'Gate outputs must be distinct new files')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('archive', 'scan', 'inspect', 'original-output', 'exact2-output', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--scanner-exit-code', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        validate_outputs(args)
    except (ValueError, OSError) as error:
        print(json.dumps({'passed': False, 'mode': 'failed_closed', 'error': str(error)[:220]}))
        return 1
    try:
        binding = original.load_binding()
        archive = original.verify_archive(args.archive, binding)
        report = exact2.strict_json(args.scan.read_bytes())
        exact2.validate_report(report)
        inspection = exact2.strict_json(args.inspect.read_bytes())
        if isinstance(inspection, list):
            require(len(inspection) == 1, 'Exactly one actual image inspection is required')
            inspection = inspection[0]
        base = {**original.evaluate_scan(report, inspection, binding, scanner_exit_code=args.scanner_exit_code),
                'archive': archive, 'raw_scan_sha256': original.file_hash(args.scan),
                'mode': 'authorized_exact_artifact_FIXED'}
        exact2.write_new_output(args.original_output, base)
        openpgp_review = exact2.load_technical_review(binding)
        try:
            previous = exact2.apply_exact_technical_facts(base, report, binding, openpgp_review)
        except original.DispositionError as error:
            previous = {'passed': False, 'mode': 'original_FIXED_plus_exact2_independent_technical_facts',
                        'error': str(error), 'raw_scan_sha256': base['raw_scan_sha256']}
        exact2.write_new_output(args.exact2_output, previous)
        if previous['passed']:
            result = {**previous, 'html_technical_applied': [], 'raw_report_modified': False}
        else:
            result = apply_review(base, report, binding, openpgp_review, load_review(binding))
        result.update(mode='original_FIXED_plus_independent_exact2_and_html4_facts',
                      raw_scan_sha256=base['raw_scan_sha256'], original_approval_expires=binding['expires'])
    except (ValueError, TypeError, KeyError, OSError, tarfile.TarError) as error:
        result = {'passed': False, 'mode': 'failed_closed', 'error': str(error)[:220]}
    try:
        exact2.write_new_output(args.output, result)
    except (ValueError, OSError) as error:
        print(json.dumps({'passed': False, 'mode': 'failed_closed', 'error': str(error)[:220]}))
        return 1
    print(json.dumps({'passed': result['passed'], 'mode': result['mode']}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
