from argparse import Namespace

import pytest

from scripts.evaluate_research import main, assess_report_support, summarize_repetitions


@pytest.mark.parametrize('overrides', [
    {'live': True},
    {'external_results': 'private-reports'},
    {'cases': 'private-cases.json'},
])
async def test_failure_body_logging_rejects_non_fixture_data(overrides):
    options = {'debug_fixed_failures': True, 'live': False,
               'external_results': None, 'cases': 'eval/research_quality_cases.json'}
    options.update(overrides)
    # Rejection must happen before opening user data or starting any model run.
    with pytest.raises(ValueError, match='bundled synthetic fixed cases'):
        await main(Namespace(**options))


async def test_independent_report_support_preserves_whole_report_when_it_fits(monkeypatch):
    from types import SimpleNamespace
    from scripts import evaluate_research

    sources = [{'id': 'W-1', 'text': '完整原文' * 1000}]
    claims = [SimpleNamespace(text=f'结论 {index}', source_ids=['W-1']) for index in range(5)]
    seen = []

    async def check(_providers, case, draft, run_id):
        assert case['evidence'] is sources
        seen.append((run_id, [claim.text for claim in draft.claims]))
        return [{'index': index, 'supported': True} for index in range(len(draft.claims))]

    monkeypatch.setattr(evaluate_research, 'assess_support', check)
    checks = await assess_report_support(None, sources, claims, 'run')

    assert [row['index'] for row in checks] == list(range(5))
    assert seen == [('run', [claim.text for claim in claims])]


async def test_independent_report_support_splits_only_on_context_limit(monkeypatch):
    from types import SimpleNamespace
    from backend.core.reliability import ServiceError
    from scripts import evaluate_research

    claims = [SimpleNamespace(text=f'结论 {index}', source_ids=['W-1']) for index in range(5)]
    successful = []

    async def check(_providers, _case, draft, run_id):
        if len(draft.claims) > 2:
            raise ServiceError('too long', code='context_too_large')
        successful.append((run_id, [claim.text for claim in draft.claims]))
        return [{'index': index, 'supported': True} for index in range(len(draft.claims))]

    monkeypatch.setattr(evaluate_research, 'assess_support', check)
    checks = await assess_report_support(None, [{'id': 'W-1', 'text': '完整原文'}], claims, 'run')

    assert [row['index'] for row in checks] == list(range(5))
    assert successful == [
        ('run-support-0', ['结论 0', '结论 1']),
        ('run-support-2', ['结论 2']),
        ('run-support-3', ['结论 3', '结论 4']),
    ]


def test_stability_gate_cannot_select_a_good_run_or_hide_a_failed_case():
    cases = [{'id': str(i), 'critical': False} for i in range(10)]
    results = [{'id': c['id'], 'critical': False, 'passed': not (n == 1 and int(c['id']) < 2),
                'claim_count': 1, 'support': 1, 'coverage': 1, 'repetition': n}
               for n in range(1, 4) for c in cases]
    # 28/30 overall exceeds 85%, but the first cohort fails the unchanged 85% gate.
    assert not summarize_repetitions(cases, results, 3)['passed']
    results[0]['passed'] = True  # Every cohort now clears 85%, but one case still fails.
    assert summarize_repetitions(cases, results, 3)['passed']
    assert not summarize_repetitions(cases, results, 3, require_all=True)['passed']
