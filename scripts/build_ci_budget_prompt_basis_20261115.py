"""Re-audit two exact prompt literals without changing evaluation budgets or ledgers."""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_ci_evaluation_budget as precheck  # noqa: E402

BASE = '9ca6b7a57b850d0da06312ba8749297515897833'
GRAPH = 'backend/research/graph.py'
PRIOR = 'eval/ci-budget-source-basis-20261115.json'
PRIOR_SHA = '8224ce26296d493b32a2797424e81e2f06514d32d7dd626468c228a79a70f53b'
OUTPUT = 'eval/ci-budget-source-basis-20261115-prompts.json'
PROMPTS = {
    'analyst': ('analyst', '3ed47dbc0f3acbaf7dedb6286ed72f45ea558224d2ed1e8553b0f3c136659e2a',
                '0d850e154776c2a4041a90ec0de480010af1f47267f5ea3d37d0ca85069e1a7d'),
    'validator': ('coverage', 'c8c2f8cef2ee645e18fb183064b2e7d3903252889f5b88c570ee6a4b9367165b',
                  '714475bf9c818447661d60c175a47390551115c09b9f12115bb67bbcd204ffc2'),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def prompt(tree, method, stage):
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ResearchGraph']
    require(len(classes) == 1, 'ResearchGraph class identity differs')
    methods = [n for n in classes[0].body if isinstance(n, ast.AsyncFunctionDef) and n.name == method]
    require(len(methods) == 1, 'Audited method identity differs')
    calls = [n for n in ast.walk(methods[0]) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
             and n.func.value.id == 'self' and n.func.attr == 'ask' and len(n.args) >= 2
             and isinstance(n.args[0], ast.Constant) and n.args[0].value == stage]
    require(len(calls) == 1 and isinstance(calls[0].args[1], ast.Constant)
            and isinstance(calls[0].args[1].value, str), 'Exact prompt call is missing or ambiguous')
    return calls[0].args[1]


def verify_prompt_only(previous, current):
    old, new = ast.parse(previous), ast.parse(current)
    changes = {}
    for method, (stage, before_sha, after_sha) in PROMPTS.items():
        before, after = prompt(old, method, stage), prompt(new, method, stage)
        require(sha(before.value.encode()) == before_sha and sha(after.value.encode()) == after_sha,
                'Prompt delta exceeds the exact reviewed text: ' + method)
        changes['ResearchGraph.' + method] = {'stage': stage, 'before_prompt_sha256': before_sha,
                                             'after_prompt_sha256': after_sha}
        after.value = before.value
    require(ast.dump(old, include_attributes=False) == ast.dump(new, include_attributes=False),
            'Executable module changed beyond the two reviewed prompt literals')
    return changes


def generate(root=ROOT, output=None):
    output = output or root / OUTPUT
    require(not output.exists(), 'Preserve prior outputs; use a new output path')
    old_raw = (root / PRIOR).read_bytes()
    historical = subprocess.check_output(['git', 'show', BASE + ':' + PRIOR], cwd=root)
    require(old_raw == historical and sha(old_raw) == PRIOR_SHA, 'Historical basis changed')
    old_graph = subprocess.check_output(['git', 'show', BASE + ':' + GRAPH], cwd=root)
    new_graph = (root / GRAPH).read_bytes()
    deltas = verify_prompt_only(old_graph, new_graph)
    prior = json.loads(old_raw)
    failures, _ = precheck.source_basis(root, prior)
    require(set(failures) == {GRAPH + ':' + name for name in deltas}, 'Unexpected audited source drift')
    for name, expected in prior['case_fingerprints'].items():
        require(precheck.fingerprint(root / name) == expected, 'Case, dependency or grading input changed: ' + name)
    new = deepcopy(prior)
    for entry in new['symbols']:
        if entry['path'] == GRAPH and entry['symbol'] in deltas:
            entry['ast_sha256'] = precheck.symbol_hash(root / GRAPH, entry['symbol'])
    failures, references = precheck.source_basis(root, new)
    require(not failures and len(references) == 37, 'The complete 37-symbol/four-invocation proof differs')
    proof = {
        'schema': 'ci-budget-exact-prompt-continuation-20261115-v1',
        'prior_basis': {'path': PRIOR, 'sha256': PRIOR_SHA, 'source_commit': BASE, 'bytes_preserved': True},
        'producer': {'path': Path(__file__).relative_to(ROOT).as_posix(), 'sha256': sha(Path(__file__).read_bytes())},
        'graph': {'path': GRAPH, 'before_sha256': sha(old_graph), 'after_sha256': sha(new_graph)},
        'exact_prompt_deltas': deltas, 'entire_other_module_ast_identical': True,
        'all_37_audited_symbols': references, 'four_fresh_invocations_unchanged': prior['fresh_invocations'],
        'all_case_and_dependency_fingerprints_unchanged': prior['case_fingerprints'],
        'policy_authorization_or_ledger_changed': False, 'supplier_http_requests': 0,
        'paid_quality_pass_claimed': False,
        'rationale': [
            'Explicit source statements that an item is pending may be extracted as scoped cited facts when the user asks for pending evidence. Unproven absence from a fragment remains only a gap.',
            'Coverage follows the original request: identifying evidence gaps does not require completing those tests. Missing requested concrete values remain unanswered; gaps and deleted claims cannot independently establish coverage.',
            'Only two exact prompt constants change. All branches, calls, model schemas, independent claim checks, grading rules, success thresholds and HTTP/retry paths remain unchanged.',
        ],
    }
    new['source_prompt_continuation_20261115'] = proof
    with tempfile.TemporaryDirectory(prefix='ci-prompt-budget-proof-') as directory:
        temporary = Path(directory)
        basis_path, policy_path = temporary / 'basis.json', temporary / 'policy.json'
        basis_path.write_text(json.dumps(new), encoding='utf-8')
        policy = json.loads((root / 'eval/ci-provider-budget.json').read_bytes())
        policy.pop('ci_series_id', None)
        policy.update(model_http=296, llm_http=296, total_http=296)
        policy_path.write_text(json.dumps(policy), encoding='utf-8')
        ledger = temporary / 'never-created.sqlite3'
        result = precheck.assess(root, basis_path=basis_path, policy_path=policy_path, ledger_path=ledger)
        require(result['proof_verified'] and not result['proof_drift']
                and result['critical_path_lower_bound'] == 245 and result['pass_rate_refinement_lower_bound'] == 296
                and result['required_lower_bound'] == 296 and result['supplier_http_requests'] == 0
                and result['precheck_status'] == 'necessary_bound_not_violated' and not ledger.exists(),
                'Unchanged necessary budget bounds were not recomputed')
        proof['original_checker_recomputed'] = {k: result[k] for k in [
            'proof_verified', 'critical_path_lower_bound', 'pass_rate_refinement_lower_bound',
            'required_lower_bound', 'supplier_http_requests', 'precheck_status']}
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(new, stream, indent=2)
        stream.write('\n')
    return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / OUTPUT)
    arguments = parser.parse_args()
    result = generate(output=arguments.output)
    print(json.dumps({'generated': True, 'symbols': len(result['all_37_audited_symbols']),
                      'bounds': result['original_checker_recomputed'], 'supplier_http_requests': 0}))
