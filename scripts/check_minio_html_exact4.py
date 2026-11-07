"""Preserve the original gates and apply independently reviewed HTML-only facts."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import gzip
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
CLASSIFICATION_REVIEW_PATH = Path('deployment/security/minio-html4-classification-review-20261107.json')
CLASSIFICATION_REVIEW_SHA256 = '70a770580a65a7d5f5d64f7f77e75ad1fb661cf47d21abe5fcbc71d63954f858'
CLASSIFIED_SEVERITIES = {'CVE-2026-78661': 'HIGH', 'CVE-2026-97045': 'MEDIUM'}
CLASSIFIED_GO_IDS = {'CVE-2026-78661': 'GO-2026-6860', 'CVE-2026-97045': 'GO-2026-6866'}


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


def complete_graph_facts(raw, native):
    """Reparse the full package closure, including inlined dependencies, against native modules."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate production graph field')
            result[key] = value
        return result
    def invalid_constant(_):
        raise original.DispositionError('Nonfinite production graph value')
    decoder = json.JSONDecoder(object_pairs_hook=unique, parse_constant=invalid_constant)
    text, cursor, packages, modules, imports = raw.decode('utf-8'), 0, {}, {}, set()
    while cursor < len(text):
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor == len(text):
            break
        row, cursor = decoder.raw_decode(text, cursor)
        require(type(row) is dict and 'Error' not in row
                and ('Incomplete' not in row or row['Incomplete'] is False)
                and ('DepsErrors' not in row or (type(row['DepsErrors']) is list and not row['DepsErrors'])),
                'Production graph contains incomplete or failed dependencies')
        name = row.get('ImportPath')
        require(type(name) is str and name and name not in packages, 'Production package is missing or duplicated')
        values = row.get('Imports', [])
        require(type(values) is list and all(type(value) is str and value for value in values), 'Malformed production imports')
        packages[name] = row
        imports.update(values)
        if 'Module' in row:
            module = row['Module']
            require(type(module) is dict and type(module.get('Path')) is str and module['Path'], 'Malformed module identity')
            identity = {key: module.get(key) for key in ('Path', 'Version', 'Sum', 'Main', 'Replace')}
            require(module['Path'] not in modules or modules[module['Path']] == identity, 'Ambiguous production module identity')
            modules[module['Path']] = identity
    require(packages and imports <= packages.keys(), 'Production dependency closure is incomplete')
    require(set(modules) == set(native), 'Full native and graph module coverage differs')
    for path, module in modules.items():
        observed = native[path]
        if module.get('Main'):
            require(observed['path'] == path and not observed.get('replace'), 'Native main module identity differs')
        else:
            effective = module.get('Replace') or module
            selected = observed.get('replace') or observed
            require((effective['Path'], effective.get('Version'), effective.get('Sum')) ==
                    (selected['path'], selected.get('version'), selected.get('sum')),
                    'Native effective dependency path/version/sum differs from the full graph')
    html = sorted(name for name in packages.keys() | imports
                  if name == 'golang.org/x/net/html' or name.startswith('golang.org/x/net/html/'))
    require(not html and native[PACKAGE] == {'path': PACKAGE, 'version': VERSION,
            'sum': 'h1:RR+6j/BTBCrggOLugxa+66R8FvoN6MHlzDlZIbN9YFw='},
            'Reviewed HTML package absence or actual x/net dependency changed')
    return {'package_count': len(packages), 'module_count': len(modules), 'html_imports': html,
            'imports_closure_complete': True, 'effective_native_dependencies_equal': True}


def classification_build_facts(binding, html_review, actual_native_files=None):
    inventory_path = 'deployment/security/minio-html4-evidence/actual-native-module-inventory-complete.json.gz'
    native = exact2.strict_json(gzip.decompress(original.checked_file(ROOT, inventory_path).read_bytes()))
    facts = {}
    for target, build in html_review['target_builds'].items():
        native_path = actual_native_files[target] if actual_native_files else build['native_buildinfo']['path']
        native_text = original.checked_file(ROOT, native_path).read_text(encoding='utf-8')
        require(native_text.splitlines()[0].endswith(': go1.27.3'), 'Actual native compiler identity differs')
        parsed, current = {}, None
        for line in native_text.splitlines()[1:]:
            fields = line.strip().split('\t')
            if fields[0] in ('mod', 'dep'):
                require(len(fields) in (3, 4) and fields[1] not in parsed, 'Malformed or duplicate native module')
                current = fields[1]
                parsed[current] = {'path': current, 'version': fields[2], 'sum': fields[3] if len(fields) == 4 else None}
            elif fields[0] == '=>':
                require(current is not None and len(fields) in (3, 4) and 'replace' not in parsed[current],
                        'Malformed native replacement')
                parsed[current]['replace'] = {'path': fields[1], 'version': fields[2], 'sum': fields[3] if len(fields) == 4 else None}
        require(parsed == native[target], 'Complete native text and extracted module inventory differ')
        proof = {}
        for kind in ('graph', 'functions'):
            evidence = build[kind]
            compressed = original.checked_file(ROOT, evidence['path']).read_bytes()
            require(hashlib.sha256(compressed).hexdigest() == evidence['sha256'], 'Compressed compile evidence changed')
            with gzip.open(original.checked_file(ROOT, evidence['path']), 'rb') as stream:
                raw = stream.read(evidence['raw_bytes'] + 1)
            require(len(raw) == evidence['raw_bytes'] and hashlib.sha256(raw).hexdigest() == evidence['raw_sha256'],
                    'Complete compile evidence size or bytes changed')
            if kind == 'graph':
                proof.update(complete_graph_facts(raw, native[target]))
            else:
                functions = exact2.strict_json(raw)
                require(functions['elf_sha256'] == binding['binary_sha256'][target]
                        and functions['function_count'] == len(functions['linked_functions']) == build['function_count']
                        and not any('golang.org/x/net/html' in name for name in functions['linked_functions']),
                        'Supplemental linked function/ELF proof differs')
                proof['function_count'] = functions['function_count']
        require(proof['package_count'] == build['package_count'] and proof['module_count'] == build['module_count'],
                'Reviewed complete package/module coverage changed')
        facts[target] = {'elf_sha256': binding['binary_sha256'][target], **proof}
    return facts


def load_classification_review(binding, html_review):
    path = original.checked_file(ROOT, CLASSIFICATION_REVIEW_PATH.as_posix())
    require(original.file_hash(path) == CLASSIFICATION_REVIEW_SHA256, 'New exact HTML classification review bytes changed')
    review = exact2.strict_json(path.read_bytes())
    require(review['schema'] == 'minio-html4-classification-review-v1'
            and review['actor_kind'] == 'automated_independent_agent'
            and review['review_kind'] == 'exact_classification_technical_appendix'
            and review['facts_passed'] is True and review['human_risk_acceptance'] is False
            and review['artifact_approved'] is False and review['original_binding_sha256'] == original.APPROVED_BINDING_SHA256
            and review['legacy_html_review_sha256'] == REVIEW_SHA256 and review['openpgp_review_sha256'] == exact2.REVIEW_SHA256,
            'New classification review is not the exact technical appendix')
    for field in ('index_digest', 'platform_manifest_digest', 'config_digest', 'archive_sha256', 'binary_sha256'):
        require(review['subject'][field] == binding[field], 'Classification appendix binds another artifact or ELF')
    expected = Counter((target, advisory, PACKAGE, VERSION, severity) for target in exact2.TARGETS
                       for advisory, severity in CLASSIFIED_SEVERITIES.items())
    require(Counter(tuple(row[key] for key in ('target', 'advisory', 'package', 'version', 'severity'))
                    for row in review['rows']) == expected, 'New exact HTML severity tuples changed')
    require(all(row['elf_sha256'] == binding['binary_sha256'][row['target']] for row in review['rows']),
            'Exact classification row binds another ELF')
    for name, digest in review['evidence_files'].items():
        require(original.file_hash(original.checked_file(ROOT, name)) == digest, 'Classification evidence bytes changed')
    require({item['advisory']: item['go_advisory'] for item in review['advisories']} == CLASSIFIED_GO_IDS
            and len(review['advisories']) == 2, 'Both exact official HTML advisories are required')
    for item in review['advisories']:
        advisory = exact2.strict_json(original.checked_file(ROOT, item['path']).read_bytes())
        require(advisory['id'] == item['go_advisory'] and item['advisory'] in advisory['aliases']
                and len(advisory['affected']) == 1 and advisory['affected'][0]['package']['name'] == PACKAGE
                and advisory['affected'][0]['ecosystem_specific']['imports'] == item['affected_imports']
                and all(row['path'] == 'golang.org/x/net/html' for row in item['affected_imports'])
                and advisory['affected'][0]['ranges'] == [{'type': 'SEMVER', 'events': [{'introduced': '0'}, {'fixed': '0.62.0'}]}],
                'Current official advisory import or fixed-version scope changed')
    replay = exact2.strict_json(original.checked_file(ROOT, review['independent_oci_replay']).read_bytes())
    require(replay['schema'] == 'minio-independent-oci-native-replay-v1'
            and replay['artifact_approved'] is False and replay['human_risk_acceptance'] is False
            and replay['container_started'] is False and replay['binary_executed'] is False
            and all(replay['oci'][field] == binding[field] for field in
                    ('index_digest', 'platform_manifest_digest', 'config_digest', 'archive_sha256', 'binary_sha256'))
            and replay['oci']['recomputed_diff_ids'] == binding['diff_ids']
            and replay['oci']['authorization_evaluated'] is False
            and set(review['actual_native_files']) == exact2.TARGETS,
            'Independent actual OCI/native replay does not bind this unchanged artifact')
    for target, name in review['actual_native_files'].items():
        require(replay['actual_native'][target]['native_file'] == name
                and replay['actual_native'][target]['native_sha256'] == review['evidence_files'][name]
                and replay['actual_native'][target]['elf_sha256'] == binding['binary_sha256'][target],
                'Newly reparsed native record is not bound to the actual ELF')
    require(review['build_facts'] == classification_build_facts(binding, html_review, review['actual_native_files']),
            'Independent native/graph facts differ')
    return review


def apply_classification_review(base, report, binding, openpgp_review, html_review, review, prior_error):
    """A separate exact tuple branch; the old UNKNOWN branch and raw report stay intact."""
    exact2.validate_report(report)
    require(not any(row.get('Secrets') for row in report['Results']), 'Every secret finding remains blocked independently')
    for record in [report, *report['Results']]:
        for field in ('Errors', 'ModifiedFindings', 'SuppressedFindings'):
            require(field not in record or (type(record[field]) is list and not record[field]),
                    'Scanner errors, modifications or suppressions remain blocked')
    expected_html = Counter(tuple(row[key] for key in ('target', 'advisory', 'package', 'version', 'severity'))
                            for row in review['rows'])
    observed_html, observed_unknown = Counter(), Counter()
    for record in report['Results']:
        if record.get('Target') in exact2.TARGETS:
            require([item.get('Version') for item in record.get('Packages', []) if item.get('Name') == PACKAGE] == [VERSION],
                    'Complete scanner inventory differs from the exact native x/net dependency')
        for finding in record.get('Vulnerabilities', []):
            key = (record.get('Target'), finding.get('VulnerabilityID'), finding.get('PkgName'),
                   finding.get('InstalledVersion'), finding.get('Severity'))
            if finding.get('VulnerabilityID') in ADVISORIES:
                require(finding.get('FixedVersion') == '0.62.0', 'Official HTML fixed-version classification changed')
                observed_html[key] += 1
            if finding.get('Severity') not in {'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'}:
                observed_unknown[key] += 1
    expected_unknown = Counter((target, exact2.ADVISORY, exact2.PACKAGE, exact2.VERSION, exact2.SEVERITY)
                               for target in exact2.TARGETS)
    unresolved = Counter(tuple(sorted(row.items())) for row in base['unresolved'])
    expected_unresolved = Counter(tuple(sorted({'reason': 'unknown_severity', 'finding': exact2.ADVISORY}.items()))
                                  for _ in exact2.TARGETS)
    expected_unresolved.update(tuple(sorted({'vulnerability': 'CVE-2026-78661', 'package': PACKAGE,
        'target': target, 'severity': 'HIGH'}.items())) for target in exact2.TARGETS)
    require(observed_html == expected_html and observed_unknown == expected_unknown
            and base['passed'] is False and base['raw_counts'] == {'HIGH': 3, 'CRITICAL': 0}
            and base['verified_fixed_count'] == 1 and base['verified_fixed'] == [{'vulnerability': binding['vulnerability'],
                'target': binding['finding']['target'], 'disposition': 'FIXED'}]
            and base['unresolved_count'] == 4 and unresolved == expected_unresolved,
            'Additional, missing, duplicate or changed classified findings remain blocked')
    require({row['target'] for row in openpgp_review['targets']} == exact2.TARGETS
            and set(html_review['target_builds']) == exact2.TARGETS,
            'Prior exact technical reviews are required')
    return {'passed': True, 'original_gate': base, 'legacy_html_gate': {'passed': False, 'error': prior_error},
            'mode': 'original_FIXED_plus_exact_openpgp_and_classified_html4_technical_facts',
            'raw_report_modified': False, 'human_risk_acceptance': False, 'global_security_approved': False,
            'classification_review_applied': True, 'classification_review_sha256': CLASSIFICATION_REVIEW_SHA256,
            'html_review_sha256': REVIEW_SHA256, 'openpgp_review_sha256': exact2.REVIEW_SHA256,
            'technical_applied': [{**row, 'elf_sha256': binding['binary_sha256'][row['target']],
                'disposition': 'NOT_APPLICABLE_TO_EXACT_BOUND_TARGET_BUILD'} for row in review['rows']]}


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
            html_review = load_review(binding)
            try:
                result = apply_review(base, report, binding, openpgp_review, html_review)
            except original.DispositionError as prior_error:
                result = apply_classification_review(base, report, binding, openpgp_review, html_review,
                    load_classification_review(binding, html_review), str(prior_error))
        result.update(mode=(result['mode'] if result.get('classification_review_applied')
                            else 'original_FIXED_plus_independent_exact2_and_html4_facts'),
                      raw_scan_sha256=base['raw_scan_sha256'], validity_policy='change-driven',
                      superseded_calendar_expiry=binding['expires'])
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
