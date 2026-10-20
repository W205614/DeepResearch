"""Model formatting cannot rewrite targets or erase valid facts."""
from unittest.mock import AsyncMock

import pytest

from backend.domain.models import ReportDraft, TargetVerification
from backend.research.graph import (ResearchGraph, bind_target_verification,
                                    verification_targets, VERIFICATION_WIRE)
from backend.core.reliability import ServiceError


def test_targets_preserve_entire_claim_scope_attribution_and_negation():
    text = '根据资料，若A且B才可放行；但未获授权时不得放行。尾部限制也须保留。'
    assert verification_targets(7, text) == [{'id': '7:0', 'text': text}]
    result = bind_target_verification(TargetVerification(checks=[{'index': 7,
        'atomic_checks': [{'target_id': '7:0', 'supported': True}]}]), [{'index': 7, 'text': text}])
    assert result.checks[0].supported and result.checks[0].verification_complete
    assert result.checks[0].atomic_checks[0].text == text


@pytest.mark.parametrize('checks', [[],
    [{'index': 0, 'atomic_checks': []}],
    [{'index': 0, 'atomic_checks': [{'target_id': 'wrong', 'supported': True}]}],
    [{'index': 0, 'atomic_checks': [{'target_id': '0:0', 'supported': True}]},
     {'index': 0, 'atomic_checks': [{'target_id': '0:0', 'supported': True}]}],
    [{'index': 0, 'atomic_checks': [{'target_id': '0:0', 'supported': True},
                                   {'target_id': '0:0', 'supported': True}]}],
    [{'index': 9, 'atomic_checks': [{'target_id': '9:0', 'supported': True}]}],
    [{'index': 0, 'atomic_checks': [{'target_id': '0:0'}]}],
])
def test_invalid_target_ids_mean_incomplete_not_unsupported_fact(checks):
    result = bind_target_verification(TargetVerification.model_validate({'checks': checks}),
                                     [{'index': 0, 'text': '有据原文。'}])
    check = result.checks[0]
    assert not check.verification_complete and not check.supported and check.format_error
    assert not check.unsupported_parts and not check.omitted_conditions


def test_semantic_rejection_and_omitted_conditions_survive_correct_ids():
    raw = TargetVerification(checks=[{'index': 0, 'supported': True,
        'atomic_checks': [{'target_id': '0:0', 'supported': True}], 'omitted_conditions': ['必须B']}])
    check = bind_target_verification(raw, [{'index': 0, 'text': '只要A。'}]).checks[0]
    assert check.verification_complete and not check.supported and check.omitted_conditions == ['必须B']


async def test_incomplete_wire_keeps_draft_and_never_enters_fact_repair(runtime, monkeypatch):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    calls = []
    async def model(role, instruction, data, schema, run_id, **kwargs):
        calls.append(role)
        assert role == 'validator' and data['claims'][0]['targets'][0]['text'] == '正确事实。'
        return TargetVerification(checks=[])
    monkeypatch.setattr(runtime.providers, 'structured', model)
    draft = ReportDraft(title='scope', sections=[{'heading': 'facts', 'claims': [
        {'text': '正确事实。', 'source_ids': ['W-one']}]}])
    original = draft.model_dump()
    with pytest.raises(ServiceError) as error:
        await graph.validator({'run_id': 'incomplete-targets', 'user_id': 'alice', 'topic': '事实',
            'plan': {'questions': ['事实?']}, 'gaps': [], 'conflicts': [], 'draft': original,
            'evidence': [{'id': 'W-one', 'kind': 'web', 'text': '正确事实。', 'title': 'source',
                          'url': 'https://example.com', 'locator': '', 'access': 'fulltext', 'published_at': ''}]})
    assert error.value.code == 'verification_incomplete' and calls == ['validator']
    assert draft.model_dump() == original
    saved = await runtime.db.one('SELECT result FROM verified_claims WHERE run_id=?', ('incomplete-targets',))
    import json
    assert json.loads(saved['result'])['wire_version'] == VERIFICATION_WIRE


async def test_runtime_format_failure_does_not_fall_back_to_partial_evidence(runtime, monkeypatch):
    from backend.domain.models import RunRequest
    monkeypatch.setattr(runtime, 'schedule', AsyncMock())
    run = await runtime.create('alice', RunRequest(topic='format failure', client_request_id='wire-failure'))
    monkeypatch.setattr(runtime.graph, 'ainvoke', AsyncMock(side_effect=ServiceError(
        'incomplete', code='verification_incomplete')))
    fallback = AsyncMock(return_value=('old partial', []))
    monkeypatch.setattr(runtime, 'partial_report', fallback)
    await runtime.execute_run(run['id'])
    current = await runtime.db.owned_run(run['id'], 'alice')
    assert current['error_info']['code'] == 'verification_incomplete' and not current['report']
    fallback.assert_not_awaited()
