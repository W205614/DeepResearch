"""Retire one shadowed legacy constraint only after exact approved selection.

All original filesystem rows and the two prior verdict files are preserved.
This evaluates configured Compose/CI selection, not running-container replacement.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_candidate_exact28 as approved  # noqa: E402
from scripts import check_grafana_candidate_source_exact2 as source  # noqa: E402
from scripts import runtime_ci_inventory as inventory  # noqa: E402

REVIEW_PATH = 'deployment/security/grafana-active-source-retirement-technical-review.json'
REVIEW_SHA256 = 'd0401b07ce343517e8782358529903dc3855f1a96fa728025505d87c0786d2cd'
OLD_SOURCE = 'deployment/vendors/grafana/main/go.mod'
PACKAGE = 'github.com/go-jose/go-jose/v4'
SCOPE = ('CVE-2026-116587', OLD_SOURCE, PACKAGE, 'v4.1.4', 'HIGH')
SELECTOR_FILES = ('compose.yaml', '.github/workflows/ci.yml', '.github/workflows/security.yml')
BASE = '.cache/eval/'
require = approved.require


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def selector_files(root):
    files = set(SELECTOR_FILES)
    for directory in (root / '.github/workflows', root / 'deployment/compose'):
        if directory.is_dir():
            files.update(p.relative_to(root).as_posix() for p in directory.iterdir()
                         if p.is_file() and p.suffix in {'.yml', '.yaml'})
    return sorted(files)


def job(text, name):
    match = re.search(r'^  ' + re.escape(name) + r':\s*\n(.*?)(?=^  [A-Za-z][A-Za-z0-9_-]*:|\Z)',
                      text, re.M | re.S)
    require(match is not None, 'Required explicit CI job is missing')
    return match.group(1)


def steps(block):
    return re.findall(r'^      - .*?(?=^      - |\Z)', block, re.M | re.S)


def field(step, key):
    values = re.findall(r'^        ' + re.escape(key) + r':\s*([^\n]*)$', step, re.M)
    require(len(values) <= 1, 'Ambiguous workflow step field')
    return values[0].strip() if values else None


def run_command(step):
    match = re.search(r'^(?:      -|       ) run:\s*([^\n]*)$', step, re.M)
    if match is None:
        return None
    command = match.group(1).strip()
    if command in {'|', '|-', '>', '>-'}:
        body = step[match.end():].splitlines()
        lines = []
        for line in body:
            if not line.strip():
                continue
            if not line.startswith('          '):
                break
            lines.append(line.strip().removesuffix('\\').strip())
        command = ' '.join(lines)
    return command


def script_step(block, script):
    selected = [(index, step, run_command(step)) for index, step in enumerate(steps(block))
                if run_command(step) and ('scripts/' + script) in run_command(step)]
    require(len(selected) == 1, 'Required CI command is missing or repeated')
    index, step, command = selected[0]
    require(not any(value in command for value in ('||', '&&', ';', '|', '`', '\n')),
            'CI gate command must not be shell-bypassed')
    return index, step, command


def command_arguments(command, script):
    values = shlex.split(command)
    require(values[:2] == ['python', 'scripts/' + script] and len(values[2:]) % 2 == 0,
            'Unexpected CI script invocation')
    args = {}
    for key, value in zip(values[2::2], values[3::2]):
        require(key.startswith('--') and key not in args, 'Repeated or unexpected CI arguments')
        args[key] = value
    return args


def enabled_gate(step, condition=None):
    require(field(step, 'continue-on-error') in (None, 'false'), 'A final CI gate may not ignore failure')
    require(field(step, 'if') == condition, 'Required CI gate condition differs')


def workflow_facts(root):
    ci = (root / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    security = (root / '.github/workflows/security.yml').read_text(encoding='utf-8')
    enterprise = job(ci, 'enterprise-compose-smoke')
    ci_index, ci_step, ci_run = script_step(enterprise, 'fetch_grafana_candidate.py')
    enabled_gate(ci_step)
    ci_args = command_arguments(ci_run, 'fetch_grafana_candidate.py')
    require(ci_args in ({}, {'--tag': inventory.APPROVED_GRAFANA}), 'Enterprise CI selects another artifact tag')
    start_index, _, start_run = script_step(enterprise, 'smoke-enterprise.py')
    require(ci_index < start_index and start_run == 'python scripts/smoke-enterprise.py --start',
            'Approved Grafana must be loaded before Compose starts')
    require(not re.search(r'fetch_approved_image\.py[^\n]*--artifact\s+grafana', ci),
            'Old Grafana loader remains active in CI')
    runtime = job(security, 'runtime-image-scan')
    load_index, load_step, load_run = script_step(runtime, 'fetch_grafana_candidate.py')
    enabled_gate(load_step, "matrix.artifact == 'grafana'")
    load_args = command_arguments(load_run, 'fetch_grafana_candidate.py')
    require(load_args == {'--tag': 'deepresearch-scan-grafana:ci',
            '--output': BASE + 'approved-grafana-load.json'}, 'Security loader tag/output differs')
    collect_index, collect_step, collect_run = script_step(runtime, 'scan_grafana_candidate.py')
    enabled_gate(collect_step, "matrix.artifact == 'grafana' && steps.approved_scan.outcome == 'success'")
    require(command_arguments(collect_run, 'scan_grafana_candidate.py') == {
        '--image': 'deepresearch-scan-grafana:ci', '--output-dir': BASE + 'grafana-native'},
        'Actual full native collector targets another image or output')
    require('scripts/scan_grafana_image.py' not in runtime, 'Old Grafana native collector remains active')
    gate_index, gate_step, gate_run = script_step(runtime, 'check_grafana_candidate_exact28.py')
    enabled_gate(gate_step, "matrix.artifact == 'grafana' && steps.approved_scan.outcome == 'success'")
    require(load_index < collect_index < gate_index, 'Runtime native collection/gate must follow the new loader')
    require(command_arguments(gate_run, 'check_grafana_candidate_exact28.py') == {
        '--archive': BASE + 'approved-grafana.oci.tar', '--scan': 'runtime-image.json',
        '--grype-scan': BASE + 'grafana-native/full-grype.json', '--sbom': BASE + 'grafana-native/full-syft.json',
        '--inspect': BASE + 'approved-grafana-load-inspect.json', '--scanner-exit-code': '0',
        '--grype-exit-code': '0', '--output': BASE + 'grafana-ci-disposition.json'},
        'Runtime exact28 gate input identity differs')
    require('scripts/grafana_image_disposition.py' not in runtime, 'Old Grafana runtime gate remains active')
    for step in steps(runtime):
        run = run_command(step) or ''
        if 'scripts/fetch_approved_image.py' in run:
            enabled_gate(step, "matrix.artifact == 'minio'")
    fs = job(security, 'dependency-and-image-scan')
    inventory_index, inventory_step, inventory_run = script_step(fs, 'runtime_ci_inventory.py')
    inventory_condition = field(inventory_step, 'if')
    require(inventory_condition in (None, "always() && steps.source_scan.outcome == 'success'"),
            'Source inventory must run whenever the full source producer succeeds')
    enabled_gate(inventory_step, inventory_condition)
    require(command_arguments(inventory_run, 'runtime_ci_inventory.py') == {}, 'Source inventory invocation differs')
    old_index, old_step, old_run = script_step(fs, 'check_fs_source_applicability.py')
    new_index, new_step, new_run = script_step(fs, 'check_grafana_candidate_source_exact2.py')
    final_index, final_step, final_run = script_step(fs, 'check_grafana_active_source_retirement.py')
    require(old_index < new_index < final_index and inventory_index < final_index,
            'Source verdict/inventory dependency order differs')
    require(field(old_step, 'continue-on-error') == field(new_step, 'continue-on-error') == 'true',
            'Original failing source verdicts must be retained before the final gate')
    enabled_gate(final_step, "always() && steps.source_scan.outcome == 'success'")
    raw = {'--scan': BASE + 'source-fs/raw.json', '--scan-receipt': BASE + 'source-fs/completion.json'}
    require(command_arguments(old_run, 'check_fs_source_applicability.py') == raw | {
        '--output': BASE + 'source-fs-original-verdict.json'}, 'Original source2 output is not preserved')
    require(command_arguments(new_run, 'check_grafana_candidate_source_exact2.py') == raw | {
        '--original-verdict': BASE + 'source-fs-original-verdict.json',
        '--output': BASE + 'source-fs-candidate-verdict.json'}, 'New source2 output is not separately preserved')
    require(command_arguments(final_run, 'check_grafana_active_source_retirement.py') == raw | {
        '--original-verdict': BASE + 'source-fs-original-verdict.json',
        '--candidate-verdict': BASE + 'source-fs-candidate-verdict.json',
        '--runtime-inventory': BASE + 'runtime-image-inventory.json',
        '--output': BASE + 'source-fs-verdict.json'}, 'Final source gate inputs differ')
    return {'ci_loader': ci_run, 'runtime_loader': load_run, 'runtime_collector': collect_run, 'runtime_gate': gate_run,
            'source_inventory': inventory_run, 'original_source_gate': old_run,
            'candidate_source_gate': new_run, 'final_source_gate': final_run}


def load_review(root, now):
    review = approved.read_json(approved.repository_file(root, REVIEW_PATH))
    require(approved.file_hash(root / REVIEW_PATH) == REVIEW_SHA256, 'Conditional retirement review bytes changed')
    require(review['schema'] == 'grafana-active-source-retirement-technical-review-v1'
            and review['actor_kind'] == 'automated_independent_agent'
            and review['review_kind'] == 'conditional_source_retirement'
            and review['human_risk_acceptance'] is False and review['facts_passed'] is True
            and review.get('enabled') is True,
            'No independent conditional source-selection review')
    require(now.tzinfo is not None and now < datetime.fromisoformat(review['expires']), 'Conditional retirement review expired')
    require(tuple(review['scope'][key] for key in ('id', 'target', 'package', 'version', 'severity')) == SCOPE,
            'Exact legacy constraint retirement tuple changed')
    for name, sha in review['input_files'].items():
        require(approved.file_hash(approved.repository_file(root, name)) == sha, 'Conditional source or control input changed')
    request = approved.load_request(root, now=now)
    auth = approved.read_json(root / approved.AUTH_PATH)
    approved.validate_authorization(auth, request)
    require(review['request_sha256'] == approved.REQUEST_SHA256
            and review['authorization_sha256'] == approved.file_hash(root / approved.AUTH_PATH)
            and review['subject'] == approved.subject_for(request['binding']), 'New human approval/subject differs')
    require(auth.get('actor_kind') == 'human_user_via_chat'
            and auth.get('status') == 'EXPLICIT_HUMAN_APPROVAL_RECORDED'
            and auth.get('question_item_id') == review['human_authorization_reference'],
            'Conditional source retirement lacks the genuine new approval reference')
    from scripts import fetch_grafana_candidate as fetch
    transport = fetch.load_transport(root, request['binding'])
    require(review['transport_sha256'] == fetch.TRANSPORT_SHA256
            and review['transport_sha256'] == approved.file_hash(root / fetch.TRANSPORT_PATH),
            'Current published exact-artifact loader transport differs')
    publication = approved.read_json(root / 'deployment/security/grafana-candidate-publication.json')
    proof = publication['proof']
    require(publication['published'] is True and publication['release_id'] == transport['release_id']
            and publication['release_tag'] == transport['release_tag']
            and publication['request_sha256'] == approved.REQUEST_SHA256
            and all(proof[key] == review['subject'][key] for key in ('index_digest', 'platform_manifest_digest',
                'config_digest', 'archive_sha256', 'binary_sha256'))
            and proof['recomputed_diff_ids'] == review['subject']['diff_ids']
            and proof['authorization_evaluated'] is False, 'Published complete OCI receipt differs from the approved subject')
    require(review['candidate_source2_review_sha256'] == source.REVIEW_SHA256,
            'Independent actual new-main source2 facts differ')
    native = approved.read_json(root / (source.EVIDENCE + 'main-actual-native-buildinfo.json'))
    modules = [m for m in native['Deps'] if m['Path'] == PACKAGE]
    require(len(modules) == 1 and modules[0].get('Version') == 'v4.1.6'
            and modules[0].get('Sum') == 'h1:sNRkftxFJxwsILiQukqp5z83QBSmntVwmgRY7AqBDO4='
            and not modules[0].get('Replace'), 'Actually selected native jose dependency differs')
    dockerfile = (root / 'deployment/docker/Dockerfile.grafana-security').read_text(encoding='utf-8')
    require('COPY deployment/vendors/grafana/main/ ./\nCOPY deployment/vendors/grafana-security/main/ ./\n'
            in dockerfile, 'Old constraint is not immediately overlaid before compilation')
    return review


def verify_selectors(root, recorded, review, source_commit):
    current = inventory.build_record(root)
    require(recorded == current and recorded['schema'] == 'runtime-image-inventory-v2'
            and recorded['source_commit'] == source_commit
            and recorded['github_sha'] in (None, source_commit), 'Current independently derived runtime inventory differs')
    selected = recorded['grafana_selection']
    require(selected['authorization_verified'] is True and selected['image'] == inventory.APPROVED_GRAFANA
            and selected['subject'] == review['subject'] and selected['request_sha256'] == review['request_sha256']
            and selected['authorization_sha256'] == review['authorization_sha256'], 'Active inventory selects another artifact')
    rows = [row for row in recorded['vendor_matrix']['include'] if row['artifact'] == 'grafana']
    require(len(rows) == 1 and rows[0]['services'] == 'grafana' and rows[0]['image'] == inventory.APPROVED_GRAFANA
            and rows[0]['approved_artifact'] is True, 'Grafana runtime matrix is ambiguous')
    require(not any('grafana' in row['services'].split(',') for row in recorded['custom_matrix']['include']),
            'Grafana must not use a current source build')
    for row in recorded['vendor_matrix']['include']:
        reference = row['image']
        if reference.startswith(('deepresearch-approved-grafana:', 'deepresearch-scan-grafana:', 'grafana/grafana')):
            require(reference == inventory.APPROVED_GRAFANA and row['services'] == 'grafana',
                    'Another active Grafana source/image selection remains')
    require(selector_files(root) == sorted(review['selector_files_sha256']),
            'Current Compose/CI selector path set changed')
    for name in selector_files(root):
        require(approved.file_hash(root / name) == review['selector_files_sha256'][name],
                'Reviewed current Compose/CI selector bytes changed')
    require(workflow_facts(root) == review['workflow_facts'], 'Concrete CI loader or gate references differ')
    return current


def apply_retirement(previous, scan):
    require(previous.get('scan_completion_verified') is True and 'error' not in previous
            and previous.get('technical_not_applicable_count') == 4
            and previous.get('candidate_source2_technical_not_applicable_count') == 2,
            'Both previous source2 technical routes must remain valid')
    findings, _, secrets = source.old.summarize(scan)
    require(not any(secrets.values()), 'Every secret blocks conditional source retirement')
    blocking = [f for f in findings if f['severity'] in source.old.BASELINE_GATE_SEVERITIES]
    require(len(blocking) == 5, 'Exact raw five blocking-source rows are required')
    remaining = previous['unresolved']
    observed = Counter(tuple(row[key] for key in ('id', 'target', 'package', 'version', 'severity'))
                       for row in remaining if row['kind'] == 'vulnerability')
    require(len(remaining) == 1 and observed == Counter({SCOPE: 1}),
            'Only one exact legacy jose row may retire; additional, missing, duplicate or secret rows block')
    retired = {**remaining[0], 'technical_disposition': 'NOT_SELECTED_BY_CURRENT_AUTHORIZED_PRODUCTION_BUILD'}
    return {**deepcopy(previous), 'passed': True, 'mode': 'authorized_active_selection_exact_legacy_constraint_retired',
            'retired_old_constraint_count': 1, 'retired_old_constraint': [retired],
            'technical_not_applicable_count': 5,
            'technical_not_applicable': deepcopy(previous['technical_not_applicable']) + [retired],
            'unresolved': [], 'unresolved_count': 0, 'configured_selectors_verified': True, 'existing_human_authorization_verified': True,
            'running_container_replacement_claimed': False, 'new_human_risk_acceptance': False,
            'old_and_candidate_source_verdicts_preserved': True, 'conditional_review_sha256': REVIEW_SHA256}


def evaluate(scan, receipt, original, candidate, recorded_inventory, *, original_file_sha256,
             raw_sha256, source_commit, root=ROOT, now=None, tracked_clean=True, expected_targets=None):
    result = {'passed': False, 'mode': 'failed_closed', 'retired_old_constraint_count': 0,
              'raw_report_modified': False, 'new_human_risk_acceptance': False}
    try:
        now = now or datetime.now(timezone.utc)
        previous = source.evaluate(scan, receipt, original, raw_sha256=raw_sha256, source_commit=source_commit,
            root=root, now=now, tracked_clean=tracked_clean, expected_targets=expected_targets)
        saved = {k: v for k, v in candidate.items() if k != 'preserved_original_verdict_file_sha256'}
        require(saved == previous and candidate.get('preserved_original_verdict_file_sha256') == original_file_sha256,
                'Preserved candidate source2 verdict differs from independent recomputation')
        review = load_review(root, now)
        verify_selectors(root, recorded_inventory, review, source_commit)
        result = apply_retirement(previous, scan)
        result['original_verdict_file_sha256'] = original_file_sha256
        result['candidate_verdict_canonical_sha256'] = digest(source.old.canonical(candidate))
        result['runtime_inventory_canonical_sha256'] = digest(source.old.canonical(recorded_inventory))
    except (ValueError, KeyError, TypeError, OSError, EOFError, UnicodeError, RuntimeError, subprocess.SubprocessError) as error:
        result['error_type'] = type(error).__name__
        result['error'] = str(error) if isinstance(error, ValueError) else 'Conditional source-selection evidence is unavailable'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('scan', 'scan-receipt', 'original-verdict', 'candidate-verdict', 'runtime-inventory', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    cache = ROOT.resolve() / '.cache'
    if args.output.exists() or args.output.is_symlink() or not args.output.resolve().is_relative_to(cache):
        print(json.dumps({'passed': False, 'error': 'Use a new distinct output under workspace .cache'}))
        raise SystemExit(1)
    try:
        paths = [args.scan, args.scan_receipt, args.original_verdict, args.candidate_verdict, args.runtime_inventory]
        require(all(p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(cache)
                    and 0 < p.stat().st_size <= source.old.MAX_REPORT_BYTES for p in paths), 'Original inputs are missing or unsafe')
        values = [source.old.json_loads(p.read_bytes()) for p in paths]
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout
        result = evaluate(*values, original_file_sha256=approved.file_hash(args.original_verdict),
                          raw_sha256=approved.file_hash(args.scan), source_commit=head, tracked_clean=not dirty,
                          expected_targets=source.old.committed_manifest_targets(ROOT, head))
        result['preserved_input_files_sha256'] = {p.resolve().relative_to(ROOT.resolve()).as_posix(): approved.file_hash(p) for p in paths}
    except (ValueError, OSError, subprocess.SubprocessError):
        result = {'passed': False, 'mode': 'failed_closed', 'error': 'Original source-selection inputs are unavailable'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        output.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('passed', 'mode', 'retired_old_constraint_count', 'unresolved_count', 'error') if k in result}))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
