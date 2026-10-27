"""Two exact manifest alerts: independent bound-main technical facts, not risk acceptance."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import grafana_image_disposition as gfx  # noqa: E402
from scripts.image_disposition import DispositionError, canonical, checked_file, file_hash  # noqa: E402

POLICY = 'deployment/security/grafana-source2-technical-scope.json'
EXPECTED_SCOPE_SHA256 = '12dd01ca6c46961ebc89adfb6a4b9c263047410ec9c51229de90a1239945e051'
TECHNICAL_REVIEW_SHA256 = 'f5ade0c49161d5e60a21fd0834d6abf7582ee33d8fb91d51da1129b550015c68'
TECHNICAL_DISPOSITION = 'NOT_APPLICABLE_TO_EXACT_BOUND_MAIN_BUILD'
MAX_REPORT_BYTES = 128 * 1024**2
MAX_SCAN_AGE_SECONDS = 7200
SEVERITIES = ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN')
BASELINE_GATE_SEVERITIES = frozenset({'HIGH', 'CRITICAL'})
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()


def require(value, message):
    if not value:
        raise DispositionError(message)


def unique_object(pairs):
    value = {}
    for key, child in pairs:
        require(key not in value, 'Duplicate JSON metadata field')
        value[key] = child
    return value


def invalid_constant(value):
    raise DispositionError('Non-finite JSON metadata number')


def json_loads(raw):
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)


def inventory(packages):
    require(type(packages) is list and bool(packages), 'Package inventory is missing or empty')
    rows = []
    for package in packages:
        require(type(package) is dict and type(package.get('Name')) is str and bool(package['Name'].strip())
                and (package.get('Version') is None or type(package['Version']) is str),
                'Package inventory identity types are invalid')
        rows.append((package['Name'], package.get('Version')))
    require(len(set(rows)) == len(rows), 'Package inventory contains duplicate identities')
    # A missing version stays None; never invent a version or compare None to str.
    return sorted(rows, key=lambda p: (p[0], p[1] is not None, p[1] or ''))


def no_errors_or_suppressions(value):
    for key in ('Errors', 'ModifiedFindings', 'SuppressedFindings'):
        if key in value:
            require(type(value[key]) is list and not value[key], 'Errors/suppressions are present or malformed')


def string_list(value, message):
    require(type(value) is list and all(type(v) is str and bool(v) for v in value)
            and len(value) == len(set(value)), message)
    return value


def summarize(scan):
    require(type(scan) is dict and type(scan.get('Results')) is list and bool(scan['Results']), 'Full raw scan is missing')
    no_errors_or_suppressions(scan)
    for key in ('Trivy', 'Metadata'):
        if key in scan:
            require(type(scan[key]) is dict, 'Raw scanner metadata type is invalid')
    findings, raw, secrets = [], Counter({s: 0 for s in SEVERITIES}), Counter({s: 0 for s in SEVERITIES})
    for result in scan['Results']:
        require(type(result) is dict and type(result.get('Target')) is str and bool(result['Target'])
                and all(type(result[k]) is str for k in ('Class', 'Type') if k in result), 'Raw result identity is invalid')
        no_errors_or_suppressions(result)
        if result.get('Class') == 'lang-pkgs':
            inventory(result.get('Packages'))
        for field, kind in [('Vulnerabilities', 'vulnerability'), ('Secrets', 'secret')]:
            values = result.get(field, [])
            require(type(values) is list, 'Raw finding list is invalid')
            for value in values:
                require(type(value) is dict, 'Raw finding is invalid')
                severity = value.get('Severity')
                require(type(severity) is str and severity in SEVERITIES, 'Raw finding severity is unknown')
                fields = ('VulnerabilityID', 'PkgName', 'InstalledVersion') if kind == 'vulnerability' else ('RuleID',)
                require(all(type(value.get(k)) is str and bool(value[k]) for k in fields), 'Raw finding identity is invalid')
                if kind == 'secret':
                    secrets[severity] += 1
                else:
                    raw[severity] += 1
                # Never copy secret Match, Code, snippets, descriptions or lines.
                findings.append({'kind': kind, 'id': value.get('VulnerabilityID') if kind == 'vulnerability' else value.get('RuleID'),
                    'target': result['Target'], 'package': value.get('PkgName') if kind == 'vulnerability' else None,
                    'version': value.get('InstalledVersion') if kind == 'vulnerability' else None, 'severity': severity})
    return findings, dict(raw), dict(secrets)


def load_scope(root, policy_path, now):
    policy = json_loads(checked_file(root, policy_path).read_bytes())
    require(type(policy) is dict and policy.get('schema') == 'source2-independent-technical-applicability-v1',
            'Technical source2 schema differs')
    scope = policy['scope']
    require(hashlib.sha256(canonical(scope)).hexdigest() == EXPECTED_SCOPE_SHA256, 'Exact source2 scope changed')
    require(now.tzinfo is not None and now < datetime.fromisoformat(scope['expires']), 'Source2 technical review expired')
    review_file = checked_file(root, scope['technical_review_path'])
    require(file_hash(review_file) == scope['technical_review_sha256'] == TECHNICAL_REVIEW_SHA256,
            'Independent technical review bytes changed')
    review = json_loads(review_file.read_bytes())
    require(type(review) is dict and review.get('schema') == 'source2-independent-technical-applicability-review-v1'
            and review.get('actor_kind') == 'independent_root_coordinator' and review.get('facts_passed') is True
            and review.get('new_human_CVE_risk_acceptance') is False and review.get('runtime18_verdicts_inherited') is False
            and review.get('scope_rows') == scope['rows'] and review.get('expires') == scope['expires'],
            'Review is not the pinned exact independent technical conclusion')
    rows = scope['rows']
    require(type(rows) is list and len(rows) == 2 and scope['expected_exact_row_count'] == 2
            and {r['id'] for r in rows} == {'CVE-2026-21728', 'CVE-2026-28377'}
            and all(r['target'] == scope['source_path'] and r['source_sha256'] == scope['source_sha256']
                    and r['package'] == scope['module'] and r['version'] == scope['version']
                    and r['severity'] == 'HIGH' and r['technical_disposition'] == TECHNICAL_DISPOSITION for r in rows),
            'Exact two technical rows differ')
    # load_binding supplies immutable source/graph/ELF identity only. No runtime
    # evaluate_scans result or runtime18 authorization is used on these FS rows.
    binding = gfx.load_binding(root, now=now)
    require(scope['runtime_binding_sha256'] == gfx.APPROVED_BINDING_SHA256 == review['runtime_binding_sha256']
            and scope['expires'] == binding['expires'] and scope['image_index'] == binding['index_digest'] == review['image_index']
            and scope['main_target'] == review['main_target']
            and scope['main_elf_sha256'] == binding['binary_sha256'].get(scope['main_target']) == review['main_ELF_sha256']
            and scope['main_graph'] == review['graph'] and scope['main_image_packages'] == review['main_ELF_package_inventory_count']
            and binding['evidence_files'].get(scope['main_image_scan']) == review['actual_bound_main_image_inventory_sha256'],
            'Bound source/main artifact identity differs from technical review')
    require(binding['recipe_files'].get(scope['source_path']) == scope['source_sha256']
            and file_hash(checked_file(root, scope['source_path'])) == scope['source_sha256'], 'Frozen source bytes changed')
    return policy, scope, binding

def verify_legacy_compiled_scope(root, scope, binding):
    graph = scope['main_graph']
    require(graph in binding['compiled_graphs'] and graph['target'] == scope['main_target'], 'Main compile graph identity differs')
    path = checked_file(root, graph['compressed_evidence'])
    require(binding['evidence_files'].get(graph['compressed_evidence']) == file_hash(path), 'Compile graph compressed bytes changed')
    require(type(graph['raw_bytes']) is int and 0 < graph['raw_bytes'] <= MAX_REPORT_BYTES, 'Compile graph size is invalid')
    with gzip.open(path, 'rb') as stream:
        raw = stream.read(graph['raw_bytes'] + 1)
    require(len(raw) == graph['raw_bytes'] and hashlib.sha256(raw).hexdigest() == graph['raw_sha256'],
            'Compile graph raw bytes changed')
    decoder = json.JSONDecoder(object_pairs_hook=unique_object, parse_constant=invalid_constant)
    text, cursor, count = raw.decode('utf-8'), 0, 0
    while cursor < len(text):
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor == len(text):
            break
        package, cursor = decoder.raw_decode(text, cursor)
        require(type(package) is dict and 'Error' not in package
                and ('DepsErrors' not in package or type(package['DepsErrors']) is list and not package['DepsErrors'])
                and ('Incomplete' not in package or package['Incomplete'] is False), 'Compile graph is incomplete')
        module = package.get('Module', {})
        require(type(module) is dict and ('Replace' not in module or type(module['Replace']) is dict),
                'Compile graph module metadata is invalid')
        if 'Module' in package:
            require(type(module.get('Path')) is str and bool(module['Path']), 'Compile module identity is missing')
        if 'Replace' in module:
            require(type(module['Replace'].get('Path')) is str and bool(module['Replace']['Path']),
                    'Compile replacement identity is missing')
        name = package.get('ImportPath', '')
        require(type(name) is str and bool(name), 'Compile graph package identity is missing')
        require(module.get('Path') != scope['module'] and module.get('Replace', {}).get('Path') != scope['module']
                and name != scope['module'] and not name.startswith(scope['module'] + '/'), 'Tempo is compiled into main')
        count += 1
    require(count == graph['packages'], 'Compile package coverage differs')
    scan_path = checked_file(root, scope['main_image_scan'])
    require(file_hash(scan_path) == binding['evidence_files'].get(scope['main_image_scan']), 'Main image inventory bytes changed')
    scan = json_loads(scan_path.read_bytes())
    require(type(scan) is dict and type(scan.get('Metadata')) is dict and type(scan.get('Results')) is list
            and all(type(r) is dict for r in scan['Results'])
            and scan['Metadata'].get('ImageID') == scope['image_index'], 'Main image inventory targets another image')
    selected = [r for r in scan['Results'] if r.get('Target') == scope['main_target']]
    require(len(selected) == 1, 'Main ELF package target is missing or duplicated')
    packages = inventory(selected[0].get('Packages'))
    require(len(packages) == scope['main_image_packages'],
            'Main ELF package coverage differs')
    require(not any(p.get('Name') == scope['module'] for p in selected[0]['Packages']), 'Tempo is present in main ELF inventory')


def validate_completion(scan, receipt, raw_sha256, scope, source_commit, tracked_clean, findings, now):
    require(type(raw_sha256) is str and bool(re.fullmatch('[0-9a-f]{64}', raw_sha256)), 'Raw report SHA256 is invalid')
    require(type(scan.get('SchemaVersion')) is int and scan['SchemaVersion'] == 2 and scan.get('ArtifactType') == 'filesystem'
            and type(scan.get('Trivy')) is dict and scan['Trivy'].get('Version') == scope['scanner_version'],
            'Complete unfiltered Trivy filesystem report is required')
    require(isinstance(receipt, dict) and receipt.get('schema') == 'fs-scan-completion-v1'
            and receipt.get('completed') is True and receipt.get('operational_success') is True
            and receipt.get('timed_out') is False and receipt.get('scanner_version') == scope['scanner_version']
            and receipt.get('raw_sha256') == raw_sha256 and receipt.get('raw_report_id') == scan.get('ReportID')
            and isinstance(scan.get('ReportID'), str) and bool(scan['ReportID']), 'Original scanner completion proof is missing or differs')
    require(tracked_clean is True and type(source_commit) is str and bool(re.fullmatch('[0-9a-f]{40}', source_commit))
            and receipt.get('source_commit') == source_commit, 'Raw scan does not bind the clean current source commit')
    scanners = string_list(receipt.get('scanners'), 'Scanner list is missing or malformed')
    severities = string_list(receipt.get('severities'), 'Scanner severity coverage is missing or malformed')
    require(set(scanners) == {'vuln', 'secret'}
            and set(scope['required_severities']).issubset(set(severities)) and set(severities).issubset(SEVERITIES),
            'Scanner coverage omits vulnerability, secret or unknown findings')
    filters = receipt.get('filters')
    require(type(filters) is dict and set(filters) == {'ignorefile_used', 'ignorefile_rules_empty', 'ignorefile_sha256',
            'ignorefile_mode', 'include_dev_dependencies', 'skip_files', 'skip_dirs', 'ignore_unfixed', 'vex_documents'}
            and filters['ignorefile_used'] is True and filters['ignorefile_rules_empty'] is True
            and filters['ignorefile_sha256'] == EMPTY_SHA256 and filters['ignorefile_mode'] == 'verified_empty_neutral'
            and filters['include_dev_dependencies'] is True
            and filters['ignore_unfixed'] is False
            and all(type(filters[k]) is list and not filters[k] for k in ('skip_files', 'skip_dirs', 'vex_documents')),
            'Scanner suppression/exclusion configuration differs')
    require(type(receipt.get('configured_finding_exit_code')) is int and receipt['configured_finding_exit_code'] == 0
            and type(receipt.get('process_exit_code')) is int and receipt['process_exit_code'] == 0,
            'Scanner operational exit does not prove completion; configured and actual exit must both be zero')
    start, finish, created = (datetime.fromisoformat(receipt[k]) for k in ('started_at', 'finished_at', 'report_created_at'))
    require(all(t.tzinfo is not None for t in (start, finish, created)) and start <= created <= finish
            and receipt['report_created_at'] == scan.get('CreatedAt'), 'Raw scan freshness/completion interval differs')
    require(-60 <= (now - finish).total_seconds() <= MAX_SCAN_AGE_SECONDS
            and 0 <= (finish - start).total_seconds() <= MAX_SCAN_AGE_SECONDS,
            'Raw scan is stale, future-dated or exceeds its completion bound')
    targets = [[r['Target'], r.get('Class', ''), r.get('Type', '')] for r in scan['Results']]
    require(len({tuple(t) for t in targets}) == len(targets)
            and all(t in targets for t in scope['required_scan_targets'])
            and receipt.get('result_targets_sha256') == hashlib.sha256(canonical(sorted(targets))).hexdigest(),
            'Full source result-target coverage is missing or ambiguous')
    result = [r for r in scan['Results'] if [r['Target'], r.get('Class', ''), r.get('Type', '')]
              == [scope['source_path'], 'lang-pkgs', 'gomod']]
    require(len(result) == 1, 'Exact source result is missing')
    packages = inventory(result[0].get('Packages'))
    require(len(packages) == scope['source_inventory_count']
            and hashlib.sha256(canonical(packages)).hexdigest() == scope['source_inventory_sha256'],
            'Frozen manifest package inventory is incomplete or differs')


def evaluate(scan, receipt, *, raw_sha256, source_commit, root=ROOT, now=None, tracked_clean=True, policy_path=POLICY):
    result = {'passed': False, 'mode': 'failed_closed', 'raw_counts': None, 'raw_secret_counts': None,
              'technical_not_applicable_count': 0, 'technical_not_applicable': [], 'scan_completion_verified': False,
              'retained_nonblocking_count': None, 'retained_nonblocking': [],
              'runtime_dispositions_inherited': False, 'new_human_risk_acceptance': False, 'raw_report_modified': False,
              'baseline_gate_severities': sorted(BASELINE_GATE_SEVERITIES)}
    try:
        now = datetime.now(timezone.utc) if now is None else now
        require(type(now) is datetime and now.tzinfo is not None, 'Source checker clock is invalid')
        findings, counts, secret_counts = summarize(scan)
        retained = [f for f in findings if f['severity'] not in BASELINE_GATE_SEVERITIES]
        blocking = [f for f in findings if f['severity'] in BASELINE_GATE_SEVERITIES]
        result.update(raw_counts=counts, raw_secret_counts=secret_counts,
                      unresolved_count=len(blocking), unresolved=blocking,
                      retained_nonblocking_count=len(retained), retained_nonblocking=retained)
        _, scope, binding = load_scope(root, policy_path, now)
        result['scoped_raw_HIGH'] = sum(f['kind'] == 'vulnerability' and f['target'] == scope['source_path']
                                        and f['severity'] == 'HIGH' for f in findings)
        verify_legacy_compiled_scope(root, scope, binding)
        validate_completion(scan, receipt, raw_sha256, scope, source_commit, tracked_clean, findings, now)
        result['scan_completion_verified'] = True
        required = {(r['id'], r['target'], r['package'], r['version'], r['severity']) for r in scope['rows']}
        selected, unresolved, seen = [], [], set()
        for finding in blocking:
            key = tuple(finding[k] for k in ('id', 'target', 'package', 'version', 'severity'))
            if finding['kind'] == 'vulnerability' and key in required:
                require(key not in seen, 'Duplicate exact source finding is ambiguous')
                seen.add(key)
                selected.append({**finding, 'technical_disposition': TECHNICAL_DISPOSITION})
            else:
                unresolved.append(finding)
        result['missing_expected_count'] = len(required - seen)
        require(seen == required, 'Expected raw source findings are missing; no zero-finding claim is allowed')
        # Preserve the existing6bec HIGH,CRITICAL vulnerability/secret threshold.
        # Raw M/L/UNKNOWN remain visible; none acquire a FIXED/NOT_AFFECTED label.
        result.update(technical_not_applicable_count=len(selected), technical_not_applicable=selected,
                      unresolved_count=len(unresolved), unresolved=unresolved, passed=not unresolved,
                      mode='independent_exact_bound_main_technical_applicability' if not unresolved else 'failed_closed')
    except (ValueError, KeyError, TypeError, OSError, EOFError, UnicodeError) as error:
        result['error_type'] = type(error).__name__
        result['error'] = str(error) if isinstance(error, DispositionError) else 'Source review or bound evidence is invalid'
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan', required=True, type=Path)
    parser.add_argument('--scan-receipt', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--policy', default=POLICY)
    args = parser.parse_args()
    # A diagnostic must not overwrite original raw evidence or frozen source.
    safe_root = ROOT.resolve() / '.cache'
    if (args.output.exists() or args.output.is_symlink()
            or not args.output.resolve().is_relative_to(safe_root)):
        print(json.dumps({'passed': False, 'mode': 'failed_closed',
                          'error': 'Use a new diagnostic file under repository .cache; existing inputs are preserved'}))
        raise SystemExit(1)
    try:
        require(args.scan.is_file() and not args.scan.is_symlink() and 0 < args.scan.stat().st_size <= MAX_REPORT_BYTES,
                'Original raw scan file is missing or oversized')
        scan = json_loads(args.scan.read_bytes())
        receipt = json_loads(args.scan_receipt.read_bytes())
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout
        result = evaluate(scan, receipt, raw_sha256=file_hash(args.scan), source_commit=head,
                          tracked_clean=not dirty, policy_path=args.policy)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        result = {'passed': False, 'mode': 'failed_closed', 'technical_not_applicable_count': 0,
                  'error_type': type(error).__name__, 'error': 'Raw scan/completion evidence is unavailable', 'raw_counts': None}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('passed', 'mode', 'raw_counts', 'scoped_raw_HIGH',
        'technical_not_applicable_count', 'unresolved_count', 'error') if k in result}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
