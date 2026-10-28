"""Zero-HTTP necessary-budget precheck; never runs/imports a supplier adapter.

Passing this check means only that a proved lower bound is not violated. It is
not permission to enlarge a budget and does not predict a successful quality run.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STABILITY_IDS = ('conflict-1', 'compare-3', 'single-5')


def fingerprint(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def symbol(path, name):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    body = tree.body
    selected = None
    for part in name.split('.'):
        selected = next(node for node in body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == part)
        body = selected.body
    return selected


def symbol_hash(path, name):
    node = symbol(path, name)
    # CPython 3.12 adds type_params=[] to ordinary class/function AST nodes.
    # The audited basis was made on 3.11: ignore only that empty compatibility
    # field, never a nonempty generic declaration or any executable source.
    for child in ast.walk(node):
        if (isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and 'type_params' in child._fields and child.type_params == []):
            child._fields = tuple(field for field in child._fields if field != 'type_params')
    # Python 3.13 omits empty lists by default; the audited 3.11 dump includes
    # them. Keep that representation without changing the audited source basis.
    dump_options = {'show_empty': True} if sys.version_info >= (3, 13) else {}
    return hashlib.sha256(ast.dump(node, include_attributes=False, **dump_options).encode()).hexdigest()


def source_basis(root, basis):
    failures = []
    references = []
    for item in basis['symbols']:
        path = root/item['path']
        try:
            node = symbol(path, item['symbol'])
            actual = symbol_hash(path, item['symbol'])
            if actual != item['ast_sha256']:
                failures.append(item['path'] + ':' + item['symbol'])
            references.append({'path': item['path'], 'symbol': item['symbol'], 'line': node.lineno,
                               'ast_sha256': actual, 'audited': actual == item['ast_sha256']})
        except (OSError, SyntaxError, StopIteration):
            failures.append(item['path'] + ':' + item['symbol'])
    workflow = (root/'.github/workflows/answer-quality.yml').read_text(encoding='utf-8')
    commands = [line.strip() for line in workflow.splitlines()
                if '- run: uv run python scripts/run_ci_evaluation.py paid ' in line]
    if commands != basis['fresh_invocations']:
        failures.append('fresh evaluation invocations changed')
    return failures, references


def read_ledger(path):
    if path is None or not path.exists():
        return {'exists': False, 'http_requests': 0, 'model_requests': 0, 'llm_requests': 0, 'halt': None}
    # Read-only SQLite URI; this check must not initialize, reserve or amend it.
    uri = 'file:' + quote(path.resolve().as_posix(), safe='/:') + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        kinds = dict(db.execute('SELECT kind,COUNT(*) FROM requests GROUP BY kind'))
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        halt = db.execute("SELECT value FROM state WHERE key='halt'").fetchone() if 'state' in tables else None
        states = dict(db.execute('SELECT key,value FROM state')) if 'state' in tables else {}
    return {'exists': True, 'http_requests': sum(kinds.values()), 'model_requests': kinds.get('llm', 0)+kinds.get('search', 0),
            'llm_requests': kinds.get('llm', 0), 'halt': halt[0] if halt else None,
            'series_id': states.get('ci_series'), 'authorization_sha256': states.get('ci_authorization_sha256'),
            'run_identity': json.loads(states['ci_current_run']) if 'ci_current_run' in states else None}


def stage(name, cases, *, repetitions=1, require_all=False):
    selected = cases * repetitions
    mandatory = [case for case in selected if require_all or case.get('critical')]
    positives = [case for case in mandatory if not case.get('must_refuse')]
    refusals = [case for case in mandatory if case.get('must_refuse')]
    return {'label': name, 'attempted_cases': len(selected), 'repetitions': repetitions, 'require_all': require_all,
            'positive_case_ids': [case['id'] for case in cases if not case.get('must_refuse')],
            'refusal_case_ids': [case['id'] for case in cases if case.get('must_refuse')],
            'mandatory_positive_case_ids': [case['id'] for case in positives],
            'mandatory_refusal_case_ids': [case['id'] for case in refusals],
            'mandatory_positive_http_each': 8, 'mandatory_refusal_http_each': 2,
            'critical_path_lower_bound': 8*len(positives)+2*len(refusals)}


def assess(root=ROOT, *, policy_path=None, ledger_path=None, basis_path=None):
    policy_path = policy_path or root/'eval/ci-provider-budget.json'
    basis_path = basis_path or root/'eval/ci-budget-source-basis.json'
    policy = json.loads(policy_path.read_bytes())
    basis = json.loads(basis_path.read_bytes())
    failures, references = source_basis(root, basis)
    answers = json.loads((root/'eval/answer_quality_cases.json').read_bytes())
    research = json.loads((root/'eval/research_quality_cases.json').read_bytes())
    extensions = json.loads((root/'eval/research_quality_extensions.json').read_bytes())
    selected = [case for case in research if case['id'] in STABILITY_IDS]
    if len(selected) != len(STABILITY_IDS):
        failures.append('stability case ids missing or duplicated')
    for path, expected in basis['case_fingerprints'].items():
        if fingerprint(root/path) != expected:
            failures.append(path + ': input/dependency changed; re-audit before using the old proof')
    # Every answer draft and the three fixed adversarial grades are unconditional.
    # Only the critical, non-refusal/non-allow_refusal citation requirement is
    # counted here; noncritical cases may fail under the unchanged 85% gate.
    required_critical_support = [case['id'] for case in answers if case.get('critical')
        and case.get('required_source_ids') and not case.get('must_refuse') and not case.get('allow_refusal')]
    answer_lower = len(answers)+basis['adversarial_cases']+len(required_critical_support)
    stages = [{'label': 'answers', 'base_cases': len(answers), 'adversarial_cases': basis['adversarial_cases'],
               'result_count': len(answers)+basis['adversarial_cases'], 'critical_required_support_case_ids': required_critical_support,
               'critical_path_lower_bound': answer_lower},
              stage('research', research), stage('stability', selected, repetitions=3, require_all=True),
              stage('extensions', extensions)]
    lower = sum(item['critical_path_lower_bound'] for item in stages)
    # Additional necessary bounds from current pass-rate counts; deliberately
    # unused as an execution allowance or promise of sufficiency.
    answer_max_failures = (len(answers)+3)-math.ceil((len(answers)+3)*0.85)
    source_required = sum(bool(case.get('required_source_ids')) and not case.get('allow_refusal') and not case.get('must_refuse') for case in answers)
    answer_rate_floor = len(answers)+3+max(0, source_required-answer_max_failures)
    research_required_passes = math.ceil(len(research)*0.85)
    research_refusals = sum(bool(case.get('must_refuse')) and bool(case.get('critical')) for case in research)
    research_rate_floor = research_refusals*2+max(0, research_required_passes-research_refusals)*8
    rate_floor = answer_rate_floor+research_rate_floor+stages[2]['critical_path_lower_bound']+stages[3]['critical_path_lower_bound']
    ledger = read_ledger(ledger_path)
    cumulative_required = policy.get('ci_series_id')
    from scripts.ci_budget_ledger import contract
    agreement = contract(root) if cumulative_required else None
    authorization = agreement['authorization'] if agreement else None
    authorization_hash = agreement['authorization_sha256'] if agreement else None
    reconciled = not cumulative_required or (ledger['exists'] and ledger.get('series_id')==cumulative_required
                 and ledger.get('authorization_sha256')==authorization_hash and ledger.get('run_identity')
                 and policy==agreement['policy'])
    if reconciled and cumulative_required and os.environ.get('GITHUB_RUN_ID'):
        owner=ledger['run_identity']
        reconciled=(owner.get('run_id')==int(os.environ['GITHUB_RUN_ID'])
                    and owner.get('run_attempt')==int(os.environ['GITHUB_RUN_ATTEMPT'])
                    and owner.get('head_sha')==os.environ['GITHUB_SHA'])
    remaining = {'model_http': policy['model_http']-ledger['model_requests'],
                 'llm_http': policy['llm_http']-ledger['llm_requests'],
                 'total_http': policy['total_http']-ledger['http_requests']}
    available = max(0, min(remaining.values())) if reconciled else None
    known = not failures
    required_lower = max(lower, rate_floor)
    insufficient = known and reconciled and available < required_lower
    max_claims, structured_http = 96, 6
    settings = symbol(root/'backend/core/config.py', 'Settings')
    cap_field = next(node for node in settings.body if isinstance(node, ast.AnnAssign)
                     and isinstance(node.target, ast.Name) and node.target.id == 'max_run_call_attempts')
    run_cap = int(os.environ.get('MAX_RUN_CALL_ATTEMPTS', str(ast.literal_eval(cap_field.value.args[0]))))
    per_graph = run_cap+structured_http*(2*max_claims-1)+structured_http
    answers_upper = (len(answers)*2+3)*structured_http
    graph_cases = sum(item['attempted_cases'] for item in stages[1:])
    protected = {item['path'] for item in basis['symbols']} | set(basis['case_fingerprints']) | {
        'backend/core/config.py', '.github/workflows/answer-quality.yml', 'scripts/bounded_evaluation.py',
        'scripts/reuse_quality_evidence.py', 'scripts/check_ci_evaluation_budget.py', 'eval/ci-budget-source-basis.json'}
    model = os.environ.get('LLM_MODEL_ID', 'deepseek-v4-flash')
    base = os.environ.get('LLM_BASE_URL', 'https://api.deepseek.com')
    extra = os.environ.get('LLM_EXTRA_BODY', '{"thinking":{"type":"disabled"}}')
    return {'schema': 'ci-budget-assessment-v1', 'generated_at': datetime.now(timezone.utc).isoformat(),
            'precheck_status': ('cumulative_reconciliation_required' if not reconciled else 'existing_provider_halt' if ledger['halt']
                               else 'insufficient_known_budget' if insufficient else 'bound_not_verified' if not known else 'necessary_bound_not_violated'),
            'exit_code': 1 if not reconciled or not known or insufficient or ledger['halt'] else 0, 'supplier_http_requests': 0,
            'proof_verified': known, 'proof_drift': failures, 'source_references': references,
            'policy': policy, 'policy_sha256': fingerprint(policy_path), 'ledger': ledger,
            'remaining_requests': remaining if reconciled else None, 'available_model_and_total_http': available,
            'critical_path_lower_bound': lower if known else None,
            'required_lower_bound': required_lower if known else None,
            'cumulative_reconciliation_verified': bool(reconciled),
            'authorization': authorization, 'authorization_sha256': authorization_hash,
            'additional_http_below_critical_bound': max(0, lower-available) if known and reconciled else None,
            'pass_rate_refinement_lower_bound': rate_floor if known else None,
            'additional_http_below_refined_bound': max(0, rate_floor-available) if known and reconciled else None,
            'additional_http_below_required_bound': max(0, required_lower-available) if known and reconciled else None,
            'stages': stages, 'theoretical_envelope': {'structured_http_max': structured_http, 'format_attempts': 2,
                'http_attempts_each': 3, 'graph_http_cap_each': run_cap, 'max_visible_claims_each': max_claims,
                'external_support_group_nodes_max': 2*max_claims-1, 'external_coverage_http_max': structured_http,
                'per_research_case_http': per_graph, 'answers_http': answers_upper,
                'all_four_stages_http': answers_upper+graph_cases*per_graph,
                'meaning': 'Loose parameterized adapter envelope, not expected demand, recommended quota or a completion guarantee.'},
            'model_conditions': {'model_id': model, 'endpoint_sha256': hashlib.sha256(base.encode()).hexdigest(),
                'extra_body_sha256': hashlib.sha256(extra.encode()).hexdigest(),
                'local_defaults_are_not_remote_secret_verification': True, 'fresh_isolated_case_databases': True,
                'fixed_web_sources': True, 'embedding_reranker_search_http': 0},
            'file_fingerprints': {path: fingerprint(root/path) for path in sorted(protected)},
            'limitations': ['A necessary lower bound is not a sufficient budget or a prediction.',
                'Unknown source drift is reported as unverified, never invented as a known quota shortage.',
                'No policy or ledger is enlarged, reset, reserved or amended by this script.',
                'Old evidence reuse remains subject to the existing one-time direct-child Java-OS-only contract.']}


def historical(path):
    data = json.loads(path.read_bytes())
    labels = Counter(row['label'] for row in data['requests'])
    return {'path': str(path), 'sha256': fingerprint(path), 'http_requests': data['http_requests'],
            'by_stage': dict(labels), 'actual_input_tokens': data['actual_prompt_tokens'],
            'actual_output_tokens': data['actual_completion_tokens'], 'usage_missing_requests': data['usage_missing_requests'],
            'execution_contexts': data['execution_contexts'],
            'meaning': 'Historical exact-source/config observation, not a forecast for changed Python/prompt/evaluator inputs.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, default=ROOT/'eval/ci-provider-budget.json')
    parser.add_argument('--ledger', type=Path, default=ROOT/'.cache/eval/provider-budget.sqlite3')
    parser.add_argument('--output', type=Path, default=ROOT/'.cache/eval/ci-budget-precheck.json')
    args = parser.parse_args(argv)
    report = assess(policy_path=args.policy, ledger_path=args.ledger)
    report['checkout_sha'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('precheck_status', 'critical_path_lower_bound',
        'pass_rate_refinement_lower_bound', 'required_lower_bound', 'available_model_and_total_http',
        'additional_http_below_required_bound', 'supplier_http_requests')}))
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
