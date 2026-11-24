from unittest.mock import AsyncMock

import pytest

from backend.core.reliability import quality
from backend.domain.models import QuestionCoverage, Reflection
from backend.research.graph import ResearchGraph


async def test_refined_search_retains_new_query_and_stops_at_existing_round_bound(runtime):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    graph.ask = AsyncMock(return_value=Reflection(
        queries=["existing API query", 'site:docs.example API "specific condition"'], reason="Missing condition"))
    state = {"run_id": "refined", "user_id": "alice", "mode": "deep", "round": 0,
             "gaps": ["Specific condition unknown"], "plan": {}, "searched": ["existing API query"]}
    result = await graph.reflect(state)
    assert result["queries"] == ['site:docs.example API "specific condition"']
    assert result["round"] == 1
    graph.ask.reset_mock()
    stopped = await graph.reflect(state | {"round": runtime.settings.max_reflection_rounds})
    assert stopped["reflect"] is False
    graph.ask.assert_not_awaited()


async def test_final_coverage_receives_unresolved_gaps_and_keeps_partial_report(runtime):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    source = {"id": "W-abc123", "kind": "web", "title": "Source", "text": "Known fact.",
              "url": "https://example.org/fact", "access": "fulltext", "locator": "paragraph",
              "published_at": "", "trust_label": "example"}
    claim = {"text": "Known fact.", "source_ids": [source["id"]], "kind": "fact"}
    graph.check_draft = AsyncMock(return_value=({0}, [{"index": 0, "supported": True}]))
    graph.ask = AsyncMock(return_value=QuestionCoverage(original_request_answered=False, answered_questions=[0]))
    result = await graph.validator({"run_id": "partial", "user_id": "alice", "topic": "Fact and condition?",
        "evidence": [source], "plan": {"questions": ["Fact?", "Condition?"]},
        "draft": {"title": "Report", "sections": [{"heading": "Facts", "claims": [claim]}]},
        "claims": [claim], "gaps": ["Condition unknown"], "conflicts": []})
    assert graph.ask.call_args.args[2]["gaps"] == ["Condition unknown"]
    assert result["final_claims"] == [claim]
    assert result["validation"]["original_request_answered"] is False
    assert "Condition?" in result["validation"]["unanswered_questions"]
    assert "Known fact." in result["report"]


@pytest.mark.parametrize("answered", [False, True])
async def test_coverage_preserves_whole_question_verdict_when_requirement_repeats_topic(runtime, answered):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    topic = "Fact and measured condition?"
    source = {"id": "W-example", "kind": "web", "title": "Source", "text": "Known fact.",
              "url": "https://example.org/fact", "access": "fulltext", "locator": "paragraph", "published_at": ""}
    claim = {"text": source["text"], "source_ids": [source["id"]], "kind": "fact"}
    graph.check_draft = AsyncMock(return_value=({0}, [{"index": 0, "supported": True}]))
    graph.ask = AsyncMock(return_value=QuestionCoverage.model_validate({
        "original_request_answered": answered, "answered_questions": [0],
        "requirements": [{"requirement": topic, "status": "answered", "claim_indices": [0]}],
    }))
    result = await graph.validator({"run_id": "whole-question", "user_id": "alice", "topic": topic,
        "evidence": [source], "plan": {"questions": [topic]}, "gaps": [], "conflicts": [],
        "draft": {"title": "Report", "sections": [{"heading": "Facts", "claims": [claim]}]}})
    validation = result["validation"]
    assert validation["original_request_answered"] is answered
    assert validation["unanswered_questions"] == ([] if answered else [topic])
    assert validation["requirements"][0]["status"] == ("answered" if answered else "partial")
    assert validation["requirements"][0]["claim_ids"] == [validation["claim_links"][0]["id"]]
    assert ("尚未确认完整回答的问题" in result["report"]) is not answered
    assert quality(validation) == ("complete" if answered else "partial")
