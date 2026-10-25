from unittest.mock import AsyncMock

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
