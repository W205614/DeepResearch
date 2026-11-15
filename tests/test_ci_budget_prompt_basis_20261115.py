"""The prompt continuation never authorizes source changes or additional HTTP."""
import ast
import json
import subprocess

import pytest

from scripts import build_ci_budget_prompt_basis_20261115 as producer
from scripts import check_ci_evaluation_budget as precheck


@pytest.fixture
def graphs():
    before = subprocess.check_output(['git', 'show', producer.BASE + ':' + producer.GRAPH], cwd=producer.ROOT)
    return before, (producer.ROOT / producer.GRAPH).read_bytes()


def test_complete_real_module_has_only_the_two_exact_reviewed_prompt_changes(graphs):
    before, after = graphs
    assert set(producer.verify_prompt_only(before, after)) == {
        'ResearchGraph.analyst', 'ResearchGraph.validator'}


@pytest.mark.parametrize('change', ['validator-branch', 'extra-module-code', 'additional-model-call'])
def test_any_other_executable_change_cannot_reuse_the_http_bound(graphs, change):
    before, after = graphs
    if change == 'validator-branch':
        changed = after.replace(b'repaired = len(approved) < total', b'repaired = len(approved) <= total', 1)
    elif change == 'additional-model-call':
        changed = after.replace(b'await self.check_draft(state, draft, sources)',
                                b'await self.check_draft_again(state, draft, sources)', 1)
    else:
        changed = after + b'\nUNREVIEWED_HTTP_POLICY = True\n'
    assert changed != after
    with pytest.raises(ValueError, match='Executable module changed'):
        producer.verify_prompt_only(before, changed)


@pytest.mark.parametrize('method,stage', [('analyst', 'analyst'), ('validator', 'coverage')])
def test_even_an_extra_prompt_instruction_requires_its_own_reaudit(graphs, method, stage):
    before, after = graphs
    tree = ast.parse(after)
    producer.prompt(tree, method, stage).value += 'Unreviewed instruction.'
    with pytest.raises(ValueError, match='Prompt delta exceeds'):
        producer.verify_prompt_only(before, ast.unparse(tree).encode())


def test_real_generator_and_original_checker_keep_37_symbols_and_245_296_bounds(tmp_path):
    policy = producer.ROOT / 'eval/ci-provider-budget.json'
    original_policy = policy.read_bytes()
    output = tmp_path / 'prompt-basis.json'
    proof = producer.generate(output=output)
    assert len(proof['all_37_audited_symbols']) == 37
    assert len(proof['four_fresh_invocations_unchanged']) == 4
    assert proof['original_checker_recomputed']['critical_path_lower_bound'] == 245
    assert proof['original_checker_recomputed']['pass_rate_refinement_lower_bound'] == 296
    assert proof['supplier_http_requests'] == 0 and not proof['policy_authorization_or_ledger_changed']
    failures, references = precheck.source_basis(producer.ROOT, json.loads(output.read_bytes()))
    assert failures == [] and all(row['audited'] for row in references)
    assert policy.read_bytes() == original_policy
    with pytest.raises(ValueError, match='Preserve prior outputs'):
        producer.generate(output=output)
