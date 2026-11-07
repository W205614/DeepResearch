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
REVIEW_SHA256 = '6e38ee2752a1d6c40fbe671db7db776e7ed88156a63df734f9140bf0d78dcd97'
MAINTENANCE_REVIEW_PATH = 'deployment/security/grafana-maintenance-source-retirement-review-20261107.json'
MAINTENANCE_REVIEW_SHA256 = 'da8142af63ff91a481782474841677b1e1b7298db44dc57ee9edcb6a623c159d'
OLD_SOURCE = 'deployment/vendors/grafana/main/go.mod'
PACKAGE = 'github.com/go-jose/go-jose/v4'
SCOPE = ('CVE-2026-116587', OLD_SOURCE, PACKAGE, 'v4.1.4', 'HIGH')
HTML_REVIEW_PATH = 'deployment/security/grafana-source-html9-current-build-review-20261107.json'
HTML_REVIEW_SHA256 = 'a0d78b19e3387de26d69cbee8435abbe02d653eaafdbacc60c3903da7a10386a'
HTML_MODULE = 'golang.org/x/net'
HTML_PLUGINS = ('grafana-postgresql-datasource', 'grafana-pyroscope-datasource', 'influxdb',
                'jaeger', 'loki', 'mssql', 'mysql', 'opentsdb')
HTML_SOURCES = (OLD_SOURCE, *('deployment/vendors/grafana/plugins/' + name + '/go.mod' for name in HTML_PLUGINS))
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


def workflow_facts(root, *, profile='exact28'):
    require(profile in {'exact28', 'maintenance'}, 'Unknown current selector profile')
    maintenance_args = {'--profile': 'maintenance'} if profile == 'maintenance' else {}
    runtime_gate_script = ('check_grafana_maintenance.py' if profile == 'maintenance'
                           else 'check_grafana_candidate_exact28.py')
    ci = (root / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    security = (root / '.github/workflows/security.yml').read_text(encoding='utf-8')
    enterprise = job(ci, 'enterprise-compose-smoke')
    ci_index, ci_step, ci_run = script_step(enterprise, 'fetch_grafana_candidate.py')
    enabled_gate(ci_step)
    ci_args = command_arguments(ci_run, 'fetch_grafana_candidate.py')
    require(ci_args in (maintenance_args, maintenance_args | {'--tag': inventory.APPROVED_GRAFANA}),
            'Enterprise CI selects another artifact profile or tag')
    start_index, _, start_run = script_step(enterprise, 'smoke-enterprise.py')
    require(ci_index < start_index and start_run == 'python scripts/smoke-enterprise.py --start',
            'Approved Grafana must be loaded before Compose starts')
    require(not re.search(r'fetch_approved_image\.py[^\n]*--artifact\s+grafana', ci),
            'Old Grafana loader remains active in CI')
    runtime = job(security, 'runtime-image-scan')
    load_index, load_step, load_run = script_step(runtime, 'fetch_grafana_candidate.py')
    enabled_gate(load_step, "matrix.artifact == 'grafana'")
    load_args = command_arguments(load_run, 'fetch_grafana_candidate.py')
    require(load_args == maintenance_args | {'--tag': 'deepresearch-scan-grafana:ci',
            '--output': BASE + 'approved-grafana-load.json'}, 'Security loader tag/output differs')
    collect_index, collect_step, collect_run = script_step(runtime, 'scan_grafana_candidate.py')
    enabled_gate(collect_step, "matrix.artifact == 'grafana' && steps.approved_scan.outcome == 'success'")
    require(command_arguments(collect_run, 'scan_grafana_candidate.py') == maintenance_args | {
        '--image': 'deepresearch-scan-grafana:ci', '--output-dir': BASE + 'grafana-native'},
        'Actual full native collector targets another image or output')
    require('scripts/scan_grafana_image.py' not in runtime, 'Old Grafana native collector remains active')
    gate_index, gate_step, gate_run = script_step(runtime, runtime_gate_script)
    enabled_gate(gate_step, "matrix.artifact == 'grafana' && steps.approved_scan.outcome == 'success'")
    require(load_index < collect_index < gate_index, 'Runtime native collection/gate must follow the new loader')
    require(command_arguments(gate_run, runtime_gate_script) == {
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
    require(command_arguments(final_run, 'check_grafana_active_source_retirement.py') == raw | maintenance_args | {
        '--original-verdict': BASE + 'source-fs-original-verdict.json',
        '--candidate-verdict': BASE + 'source-fs-candidate-verdict.json',
        '--runtime-inventory': BASE + 'runtime-image-inventory.json',
        '--output': BASE + 'source-fs-verdict.json'}, 'Final source gate inputs differ')
    return {'ci_loader': ci_run, 'runtime_loader': load_run, 'runtime_collector': collect_run, 'runtime_gate': gate_run,
            'source_inventory': inventory_run, 'original_source_gate': old_run,
            'candidate_source_gate': new_run, 'final_source_gate': final_run}


def load_review(root, now, *, profile='exact28'):
    if profile == 'maintenance':
        return load_maintenance_review(root, now)
    require(profile == 'exact28', 'Unknown conditional review profile')
    review = approved.read_json(approved.repository_file(root, REVIEW_PATH))
    require(approved.file_hash(root / REVIEW_PATH) == REVIEW_SHA256, 'Conditional retirement review bytes changed')
    require(review['schema'] == 'grafana-active-source-retirement-technical-review-v1'
            and review['actor_kind'] == 'automated_independent_agent'
            and review['review_kind'] == 'conditional_source_retirement'
            and review['human_risk_acceptance'] is False and review['facts_passed'] is True
            and review.get('enabled') is True,
            'No independent conditional source-selection review')
    approved.validate_review_policy(root, REVIEW_PATH, now=now)
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


def maintenance_selection_facts(root, now=None):
    """Derive the new selection only from an authorized, really published subject."""
    from scripts import check_grafana_maintenance as maintenance
    from scripts import fetch_grafana_candidate as fetch

    request = maintenance.load_request(root, now=now)
    auth = maintenance.read_json(root / maintenance.AUTH_PATH)
    maintenance.validate_authorization(auth, request)
    require(auth.get('actor_kind') == 'human_user_via_chat'
            and auth.get('status') == 'EXPLICIT_HUMAN_APPROVAL_RECORDED'
            and type(auth.get('authorization_source')) is str and auth['authorization_source'],
            'Maintenance selection has no genuine new human instruction reference')
    subject = maintenance.subject_for(request['binding'])
    core_path = 'deployment/security/grafana-maintenance-evidence/core-independent-review.json'
    core = maintenance.read_json(maintenance.repository_file(root, core_path))
    core_sha = maintenance.file_hash(root / core_path)
    require(request['files_sha256'].get(core_path) == core_sha
            and core.get('actor_kind') == 'automated_independent_agent'
            and core.get('facts_passed') is True and core.get('human_risk_acceptance') is False
            and core.get('artifact_approved') is False
            and all(core['subject'].get(key) == value for key, value in subject.items()),
            'New independent core selection facts bind another subject')
    scope = core['source_selection_retirement']
    require(tuple(scope[key] for key in ('id', 'target', 'package', 'version', 'severity')) == SCOPE
            and scope.get('decision') == 'NOT_SELECTED_BY_EXACT_MAINTENANCE_PRODUCTION_BUILD'
            and scope.get('conditional_only') is True
            and scope.get('formal_selection_or_authorization_granted') is False
            and scope.get('actual_core_elf_sha256') == subject['binary_sha256']['usr/share/grafana/bin/grafana'],
            'Independent exact legacy source-retirement scope changed')
    expected_jose = {'Path': PACKAGE, 'Version': 'v4.1.6',
                     'Sum': 'h1:sNRkftxFJxwsILiQukqp5z83QBSmntVwmgRY7AqBDO4='}
    require(scope.get('actual_native_dependency') == expected_jose,
            'Independent selected native jose dependency differs')
    native_path = 'deployment/security/grafana-maintenance-evidence/core-actual-actual-native-buildinfo.json'
    require(core['input_files_sha256'].get(native_path) == maintenance.file_hash(root / native_path),
            'Independently read native build metadata changed')
    native = maintenance.read_json(root / native_path)
    require([row for row in native['Deps'] if row['Path'] == PACKAGE] == [expected_jose],
            'Actually selected native jose dependency differs')
    for path_key, hash_key in (('actual_production_go_mod', 'actual_production_go_mod_sha256'),
                               ('superseding_source', 'superseding_source_sha256')):
        require(maintenance.file_hash(maintenance.repository_file(root, scope[path_key])) == scope[hash_key],
                'Actually compiled fixed module source changed')
    require(scope['actual_production_go_mod_sha256'] == scope['superseding_source_sha256']
            and scope['superseding_source'] == 'deployment/vendors/grafana-security/main/go.mod'
            and maintenance.file_hash(root / OLD_SOURCE) == scope['old_source_sha256'],
            'Actual production overlay no longer shadows the old constraint')
    dockerfile_path = 'deployment/docker/Dockerfile.grafana-maintenance'
    dockerfile = (root / dockerfile_path).read_text(encoding='utf-8')
    require('COPY deployment/vendors/grafana/main/ ./\nCOPY deployment/vendors/grafana-security/main/ ./\n'
            in dockerfile, 'Old constraint is not immediately overlaid before production compilation')
    profile = fetch.maintenance_profile()
    transport = fetch.load_transport(root, request['binding'], profile=profile)
    require(maintenance.TRANSPORT_SHA256 == maintenance.file_hash(root / maintenance.TRANSPORT_PATH),
            'Maintenance published transport bytes changed')
    publication_path = 'deployment/security/grafana-maintenance-publication.json'
    publication = maintenance.read_json(maintenance.repository_file(root, publication_path))
    proof = publication['proof']
    require(publication.get('published') is True
            and publication['release_id'] == transport['release_id']
            and publication['release_tag'] == transport['release_tag']
            and publication['request_sha256'] == maintenance.REQUEST_SHA256
            and all(proof[key] == subject[key] for key in ('index_digest', 'platform_manifest_digest',
                'config_digest', 'archive_sha256', 'binary_sha256'))
            and proof['recomputed_diff_ids'] == subject['diff_ids']
            and proof['authorization_evaluated'] is False,
            'New publication lacks a complete verified exact OCI receipt')
    input_files = (core_path, native_path, dockerfile_path, OLD_SOURCE, scope['actual_production_go_mod'],
                   scope['superseding_source'], 'scripts/runtime_ci_inventory.py',
                   'scripts/check_grafana_maintenance.py', 'scripts/fetch_grafana_candidate.py',
                   'scripts/scan_grafana_candidate.py')
    return {'schema': 'grafana-maintenance-active-source-retirement-v1', 'validity': 'change-driven',
            'actor_kind': 'automated_execution_agent', 'review_kind': 'conditional_source_selection_binding',
            'facts_passed': True, 'enabled': True, 'human_risk_acceptance': False,
            'scope': dict(zip(('id', 'target', 'package', 'version', 'severity'), SCOPE)),
            'subject': subject, 'request_sha256': maintenance.REQUEST_SHA256,
            'authorization_sha256': maintenance.file_hash(root / maintenance.AUTH_PATH),
            'human_authorization_reference': auth['authorization_source'],
            'core_independent_review_sha256': core_sha,
            'source_selection_retirement': scope,
            'transport_sha256': maintenance.TRANSPORT_SHA256,
            'publication_sha256': maintenance.file_hash(root / publication_path),
            'candidate_source2_review_sha256': source.REVIEW_SHA256,
            'input_files': {name: maintenance.file_hash(root / name) for name in sorted(set(input_files))},
            'selector_files_sha256': {name: maintenance.file_hash(root / name) for name in selector_files(root)},
            'workflow_facts': workflow_facts(root, profile='maintenance')}


def load_maintenance_review(root, now):
    require(type(MAINTENANCE_REVIEW_SHA256) is str and approved.HEX.fullmatch(MAINTENANCE_REVIEW_SHA256),
            'Maintenance selection binding has not been finalized after publication')
    review = approved.read_json(approved.repository_file(root, MAINTENANCE_REVIEW_PATH))
    require(approved.file_hash(root / MAINTENANCE_REVIEW_PATH) == MAINTENANCE_REVIEW_SHA256,
            'New conditional selection binding bytes changed')
    refresh = review.get('selection_refresh')
    if refresh is not None:
        require(type(refresh) is dict and set(refresh) == {'actor', 'reason', 'scope_boundary'}
                and refresh['actor'] == '/root/grafana_plugins_review'
                and type(refresh['reason']) is str and 0 < len(refresh['reason']) <= 1000
                and refresh['scope_boundary'] == 'Selector refresh only; no artifact, CVE disposition or authorization change.',
                'Malformed selector refresh annotation')
    require({key: value for key, value in review.items() if key != 'selection_refresh'}
            == maintenance_selection_facts(root, now),
            'Current independent source facts, authorization, publication or selectors differ')
    return review


def verify_selectors(root, recorded, review, source_commit, *, profile='exact28'):
    current = inventory.build_record(root)
    require(recorded == current and recorded['schema'] == 'runtime-image-inventory-v2'
            and recorded['source_commit'] == source_commit
            and recorded['github_sha'] in (None, source_commit), 'Current independently derived runtime inventory differs')
    selected = recorded['grafana_selection']
    if profile == 'maintenance':
        require(selected.get('profile') == 'maintenance', 'Current inventory selects another authorization profile')
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
    require(workflow_facts(root, profile=profile) == review['workflow_facts'], 'Concrete CI loader or gate references differ')
    return current


def apply_retirement(previous, scan, *, review_sha256=REVIEW_SHA256):
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
            'old_and_candidate_source_verdicts_preserved': True, 'conditional_review_sha256': review_sha256}


def current_html_module_facts(root, rows, subject):
    """Bind each frozen manifest to the actual selected ELF and its fixed native module."""
    prefix = 'deployment/security/grafana-maintenance-evidence/'
    core_path = prefix + 'core-actual-actual-native-buildinfo.json'
    plugin_path = prefix + 'plugins-actual-native-buildinfo.json'
    core = source.old.json_loads((root / core_path).read_bytes())
    plugins = source.old.json_loads((root / plugin_path).read_bytes())
    facts = []
    for row in rows:
        target = row['target']
        old_path = root / target
        require(approved.file_hash(old_path) == row['source_sha256']
                and re.search(r'(?m)^\s*golang\.org/x/net\s+v0\.60\.0(?:\s|$)', old_path.read_text(encoding='utf-8')),
                'Frozen HTML source snapshot bytes or declared module version changed')
        if target == OLD_SOURCE:
            native, actual_path = core, core_path
            actual_target = 'usr/share/grafana/bin/grafana'
            actual_mod = 'deployment/vendors/grafana-security/main/go.mod'
        else:
            name = Path(target).parent.name
            selected = plugins[name]
            native, actual_path, actual_target = selected['native'], plugin_path, selected['target']
            require(selected['sha256'] == subject['binary_sha256'].get(actual_target),
                    'Selected plugin ELF differs from actual native inventory')
            actual_mod = 'deployment/vendors/grafana-security/plugins/' + name + '/go.mod'
        expected = {'Path': HTML_MODULE, 'Version': 'v0.62.0', 'Sum': 'h1:18ELalfM5+XNRwzM2nYqXsZQ9ByS1uhj2O/U7+5jqRY='}
        require([entry for entry in native['Deps'] if entry['Path'] == HTML_MODULE] == [expected],
                'Actual selected ELF does not contain the exact fixed native x/net dependency')
        production = root / actual_mod
        require(re.search(r'(?m)^\s*golang\.org/x/net\s+v0\.62\.0(?:\s|$)', production.read_text(encoding='utf-8')),
                'Actually compiled module overlay no longer selects fixed x/net')
        facts.append({'source': target, 'source_sha256': row['source_sha256'], 'selected_target': actual_target,
            'selected_elf_sha256': subject['binary_sha256'][actual_target], 'native_file': actual_path,
            'native_file_sha256': approved.file_hash(root / actual_path), 'actual_native_dependency': expected,
            'production_go_mod': actual_mod, 'production_go_mod_sha256': approved.file_hash(production)})
    return facts


def load_html_review(root, selection):
    review = source.old.json_loads(approved.repository_file(root, HTML_REVIEW_PATH).read_bytes())
    require(approved.file_hash(root / HTML_REVIEW_PATH) == HTML_REVIEW_SHA256,
            'Independent source HTML9 review bytes changed')
    require(review['schema'] == 'grafana-source-html9-current-build-review-v1'
            and review['actor_kind'] == 'automated_independent_agent'
            and review['facts_passed'] is True and review['human_risk_acceptance'] is False
            and review['artifact_approved'] is False
            and review['subject'] == selection['subject']
            and review['request_sha256'] == selection['request_sha256']
            and review['authorization_sha256'] == selection['authorization_sha256']
            and review['selection_review_sha256'] == MAINTENANCE_REVIEW_SHA256,
            'HTML snapshot review cannot transfer to another selected or unauthorized subject')
    required = Counter(('CVE-2026-78661', target, HTML_MODULE, 'v0.60.0', 'HIGH') for target in HTML_SOURCES)
    observed = Counter(tuple(row[key] for key in ('id', 'target', 'package', 'version', 'severity')) for row in review['rows'])
    require(observed == required and len(review['rows']) == 9, 'Only nine exact HIGH source tuples are reviewed')
    for name, sha in review['evidence_files'].items():
        require(approved.file_hash(approved.repository_file(root, name)) == sha, 'HTML source/native/advisory evidence changed')
    advisory = source.old.json_loads((root / review['official_advisory']).read_bytes())
    require(advisory['id'] == 'GO-2026-6860' and 'CVE-2026-78661' in advisory['aliases']
            and len(advisory['affected']) == 1 and advisory['affected'][0]['package']['name'] == HTML_MODULE
            and advisory['affected'][0]['ranges'] == [{'type': 'SEMVER', 'events': [{'introduced': '0'}, {'fixed': '0.62.0'}]}]
            and [item['path'] for item in advisory['affected'][0]['ecosystem_specific']['imports']] == ['golang.org/x/net/html'],
            'Official source HTML affected-package or fixed-version scope changed')
    require(review['native_source_facts'] == current_html_module_facts(root, review['rows'], selection['subject']),
            'Native fixed-module and shadowed-source facts differ')
    recipe = (root / 'deployment/docker/Dockerfile.grafana-security').read_text(encoding='utf-8')
    require('COPY deployment/vendors/grafana/plugins/ /locks/\nCOPY deployment/vendors/grafana-security/plugins/ /locks/\n' in recipe,
            'Frozen plugin manifests are no longer overlaid before the reviewed production build')
    require('COPY deployment/vendors/grafana/main/ ./\nCOPY deployment/vendors/grafana-security/main/ ./\n' in recipe,
            'Frozen main manifest is no longer overlaid before the reviewed production build')
    return review


def apply_html_retirement(previous, scan, review, prior_error):
    """Evaluate fourteen full raw HIGH rows directly; never feed filtered input to the old five-row gate."""
    require(previous.get('scan_completion_verified') is True and 'error' not in previous
            and previous.get('technical_not_applicable_count') == 4
            and previous.get('candidate_source2_technical_not_applicable_count') == 2,
            'Both original exact source2 routes must remain valid')
    findings, _, secrets = source.old.summarize(scan)
    require(not any(secrets.values()), 'Every secret blocks the independent HTML source appendix')
    legacy = Counter([SCOPE] + [(advisory, path, source.MODULE, source.VERSION, 'HIGH')
        for path in (OLD_SOURCE, source.SOURCE) for advisory in sorted(source.IDS)])
    html = Counter(tuple(row[key] for key in ('id', 'target', 'package', 'version', 'severity')) for row in review['rows'])
    observed_html = Counter(tuple(row[key] for key in ('id', 'target', 'package', 'version', 'severity'))
                            for row in findings if row['id'] == 'CVE-2026-78661')
    blocking = Counter(tuple(row[key] for key in ('id', 'target', 'package', 'version', 'severity'))
                       for row in findings if row['severity'] in source.old.BASELINE_GATE_SEVERITIES)
    require(blocking == legacy + html and observed_html == html and sum(legacy.values()) == 5 and sum(html.values()) == 9,
            'Full raw source findings must be exactly the original five plus the independently reviewed nine')
    companion = Counter((row['id'], row['target'], row['package'], row['version'], row['severity'])
                        for row in findings if row['id'] == 'CVE-2026-97045')
    require(companion == Counter(('CVE-2026-97045', target, HTML_MODULE, 'v0.60.0', 'MEDIUM') for target in HTML_SOURCES),
            'Companion HTML classification rows changed; their raw MEDIUM severity is preserved')
    remaining = previous['unresolved']
    require(len(remaining) == 10 and all(row['kind'] == 'vulnerability' for row in remaining)
            and Counter(tuple(row[key] for key in ('id', 'target', 'package', 'version', 'severity'))
                        for row in remaining) == Counter({SCOPE: 1}) + html,
            'Only the exact legacy jose and nine shadowed HTML rows may receive additional dispositions')
    retired = [{**row, 'technical_disposition': 'NOT_SELECTED_BY_CURRENT_AUTHORIZED_PRODUCTION_BUILD'}
               for row in remaining if row['id'] == SCOPE[0]]
    mapping = {item['source']: item for item in review['native_source_facts']}
    fixed = [{**row, 'technical_disposition': 'NOT_SELECTED_BY_EXACT_AUTHORIZED_FIXED_MODULE_BUILD',
              'selected_target': mapping[row['target']]['selected_target'],
              'selected_elf_sha256': mapping[row['target']]['selected_elf_sha256'],
              'actual_native_dependency': mapping[row['target']]['actual_native_dependency']}
             for row in remaining if row['id'] == 'CVE-2026-78661']
    return {'passed': True, 'mode': 'authorized_selection_exact_legacy5_plus_html9_source_appendix',
            'preserved_candidate_gate': deepcopy(previous),
            'legacy_five_row_gate': {'passed': False, 'error': prior_error, 'raw_blocking_count': sum(blocking.values())},
            'raw_blocking_count': 14, 'legacy_blocking_count': 5, 'additional_html_blocking_count': 9,
            'retired_old_constraint_count': 1, 'retired_old_constraint': retired,
            'source_html_not_selected_count': 9, 'source_html_not_selected': fixed,
            'technical_not_applicable_count': 14,
            'technical_not_applicable': deepcopy(previous['technical_not_applicable']) + retired + fixed,
            'unresolved': [], 'unresolved_count': 0, 'raw_report_modified': False,
            'configured_selectors_verified': True, 'existing_human_authorization_verified': True,
            'running_container_replacement_claimed': False, 'new_human_risk_acceptance': False,
            'old_and_candidate_source_verdicts_preserved': True, 'global_security_approved': False,
            'source_html_review_sha256': HTML_REVIEW_SHA256, 'conditional_review_sha256': MAINTENANCE_REVIEW_SHA256}


def evaluate(scan, receipt, original, candidate, recorded_inventory, *, original_file_sha256,
             raw_sha256, source_commit, root=ROOT, now=None, tracked_clean=True, expected_targets=None,
             profile='exact28'):
    result = {'passed': False, 'mode': 'failed_closed', 'retired_old_constraint_count': 0,
              'raw_report_modified': False, 'new_human_risk_acceptance': False}
    try:
        now = now or datetime.now(timezone.utc)
        previous = source.evaluate(scan, receipt, original, raw_sha256=raw_sha256, source_commit=source_commit,
            root=root, now=now, tracked_clean=tracked_clean, expected_targets=expected_targets)
        saved = {k: v for k, v in candidate.items() if k != 'preserved_original_verdict_file_sha256'}
        require(saved == previous and candidate.get('preserved_original_verdict_file_sha256') == original_file_sha256,
                'Preserved candidate source2 verdict differs from independent recomputation')
        review = load_review(root, now, profile=profile)
        verify_selectors(root, recorded_inventory, review, source_commit, profile=profile)
        try:
            result = apply_retirement(previous, scan, review_sha256=(MAINTENANCE_REVIEW_SHA256
                if profile == 'maintenance' else REVIEW_SHA256))
        except ValueError as prior_error:
            if profile != 'maintenance':
                raise
            result = apply_html_retirement(previous, scan, load_html_review(root, review), str(prior_error))
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
    parser.add_argument('--profile', choices=['exact28', 'maintenance'], default='exact28')
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
                          expected_targets=source.old.committed_manifest_targets(ROOT, head), profile=args.profile)
        if args.profile == 'maintenance' and not result['passed']:
            from scripts import grafana_source_candidate4
            if result.get('error') == grafana_source_candidate4.OLD_ERROR:
                result = grafana_source_candidate4.evaluate(sys.modules[__name__], *values,
                    previous=result, root=ROOT, original_file_sha256=approved.file_hash(args.original_verdict),
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
