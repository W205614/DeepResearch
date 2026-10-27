"""Model formatting cannot rewrite targets or erase valid facts."""
from unittest.mock import AsyncMock
import json

import httpx
import pytest

from backend.domain.models import ReportDraft, TargetVerification, Verification
from backend.research.graph import (ResearchGraph, bind_target_verification,
                                    verification_targets, VERIFICATION_WIRE)
from backend.core.reliability import ServiceError
from tests.helpers import make_provider


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


def wire_row(index, supported=True):
    return {'index': index, 'supported': supported,
            'atomic_checks': [{'target_id': f'{index}:0', 'supported': supported}]}


def target_input(count=3):
    return {'claims': [{'index': i, 'text': f'private-claim-{i}', 'source_ids': [f's{i}']}
                       for i in range(count)],
            'sources': {f's{i}': {'id': f's{i}', 'text': f'private-source-{i}'} for i in range(count)}}


async def test_missing_targets_retry_only_incomplete_claims_and_preserve_semantic_false(tmp_path):
    calls = []

    def response(request):
        calls.append(json.loads(request.content))
        verdict = {'checks': [wire_row(0), wire_row(1, False)] if len(calls) == 1 else [wire_row(2)]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(verdict)}}]})

    provider = await make_provider(tmp_path, response)
    graph = ResearchGraph(provider.settings, provider.db, provider, None, None)
    try:
        data = target_input()
        result = await graph.ask('validator', 'test unchanged semantics', data, Verification, 'narrow-retry')
        assert len(calls) == 2
        second = json.loads(calls[1]['messages'][1]['content'])
        assert [row['index'] for row in second['claims']] == [2]
        assert set(second['sources']) == {'s2'}
        assert [row.supported for row in result.checks] == [True, False, True]
        assert all(row.verification_complete for row in result.checks)
        assert (await graph.ask('validator', 'test unchanged semantics', data, Verification,
                                'narrow-retry')).model_dump() == result.model_dump()
        assert len(calls) == 2  # Complete canonical verdict, including semantic false, is cached.
        event = await provider.db.one("SELECT data FROM events WHERE type='verification_format'")
        diagnostic = json.loads(event['data'])
        assert diagnostic['indices'] == [2] and diagnostic['missing_claims'] == 1
        assert 'private-claim' not in event['data'] and 'private-source' not in event['data']
    finally:
        await provider.close()


@pytest.mark.parametrize('first', [
    {'checks': [wire_row(0), wire_row(0), wire_row(1)]},
    {'checks': [wire_row(0), wire_row(1), wire_row(99)]},
    {'checks': [{'index': 0, 'atomic_checks': [{'target_id': 'private-unknown-id', 'supported': True}]},
                wire_row(1)]},
])
async def test_duplicate_unknown_ids_get_only_one_bounded_shape_retry(tmp_path, first):
    calls = []

    def response(request):
        calls.append(json.loads(request.content))
        verdict = first if len(calls) == 1 else {'checks': [wire_row(c['index']) for c in
            json.loads(calls[-1]['messages'][1]['content'])['claims']]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(verdict)}}]})

    provider = await make_provider(tmp_path, response)
    graph = ResearchGraph(provider.settings, provider.db, provider, None, None)
    try:
        result = await graph.ask('validator', 'test', target_input(2), Verification, 'ids')
        assert len(calls) == 2 and all(row.verification_complete for row in result.checks)
        second = json.loads(calls[1]['messages'][1]['content'])
        assert [row['index'] for row in second['claims']] == ([0, 1] if len(first['checks']) == 3 and
            first['checks'][-1]['index'] == 99 else [0])
        event = await provider.db.one("SELECT data FROM events WHERE type='verification_format'")
        assert 'private-unknown-id' not in event['data']
    finally:
        await provider.close()


@pytest.mark.parametrize('outputs', [
    ['not-json-private-output', {'checks': []}],
    [{'checks': []}, 'not-json-private-output'],
    [{'checks': []}, {'checks': []}],
])
async def test_parse_and_id_failures_share_two_attempts_and_keep_draft(tmp_path, outputs):
    calls = []

    def response(request):
        calls.append(request)
        result = outputs[len(calls) - 1]
        return httpx.Response(200, json={'choices': [{'message': {
            'content': result if isinstance(result, str) else json.dumps(result)}}]})

    provider = await make_provider(tmp_path, response)
    graph = ResearchGraph(provider.settings, provider.db, provider, None, None)
    draft = ReportDraft(title='private title', sections=[{'heading': 'fact', 'claims': [
        {'text': 'private correct fact', 'source_ids': ['s']}]}])
    original = draft.model_dump()
    try:
        with pytest.raises(ServiceError) as caught:
            await graph.check_draft({'run_id': 'bad-shape'}, draft, {
                's': {'id': 's', 'text': 'private correct fact', 'access': 'fulltext'}})
        assert caught.value.code == 'verification_incomplete'
        assert len(calls) == 2 and draft.model_dump() == original
        assert not await provider.db.rows('SELECT * FROM verified_claims')
        events = await provider.db.rows("SELECT data FROM events WHERE type='verification_format'")
        assert len(events) == 2
        assert not any('private' in row['data'] for row in events)
    finally:
        await provider.close()


async def test_complete_semantic_false_does_not_trigger_format_retry(tmp_path):
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {
            'content': json.dumps({'checks': [wire_row(0, False)]})}}]})

    provider = await make_provider(tmp_path, response)
    graph = ResearchGraph(provider.settings, provider.db, provider, None, None)
    try:
        result = await graph.ask('validator', 'test', target_input(1), Verification, 'semantic-false')
        assert len(calls) == 1 and not result.checks[0].supported
        assert result.checks[0].verification_complete
        assert not await provider.db.rows("SELECT * FROM events WHERE type='verification_format'")
    finally:
        await provider.close()


async def test_provider_authorization_failure_is_not_a_shape_retry(tmp_path):
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(401, json={'error': 'private upstream'})

    provider = await make_provider(tmp_path, response)
    graph = ResearchGraph(provider.settings, provider.db, provider, None, None)
    try:
        with pytest.raises(ServiceError) as caught:
            await graph.ask('validator', 'test', target_input(1), Verification, 'auth')
        assert caught.value.code == 'provider_configuration' and len(calls) == 1
        assert 'private' not in str(caught.value)
        assert not await provider.db.rows("SELECT * FROM events WHERE type='verification_format'")
    finally:
        await provider.close()


async def test_shape_retry_obeys_shared_execution_budget(tmp_path):
    from backend.core.reliability import execution

    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"checks":[]}'}}]})

    provider = await make_provider(tmp_path, response)
    # The execution stub now supplies the same accounting identity contract as a
    # persisted execution. The test still permits exactly one model HTTP call.
    await provider.db.execute("INSERT INTO workspaces VALUES('provider-fixture','Fixture','fixture-operator','today')")
    await provider.db.execute("INSERT INTO memberships VALUES('provider-fixture','fixture-operator','admin','today')")

    class OneCallBudget:
        reservations = 0

        async def check(self):
            return {'data_policy': 'public', 'deadline_at': 0,
                    'user_id': 'provider-fixture', 'created_by': 'fixture-operator'}

        async def reserve(self, tokens):
            self.reservations += 1
            if self.reservations > 1:
                raise ServiceError('budget exhausted', code='budget_exhausted', retryable=False)
            return 'reserved-first-call'

    budget = OneCallBudget()
    token = execution.set(budget)
    graph = ResearchGraph(provider.settings, provider.db, provider, None, None)
    try:
        with pytest.raises(ServiceError) as caught:
            await graph.ask('validator', 'test', target_input(1), Verification, 'budget')
        assert caught.value.code == 'budget_exhausted'
        assert len(calls) == 1 and budget.reservations == 2
    finally:
        execution.reset(token)
        await provider.close()
