import json
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from backend.domain.models import Plan, Reflection
from backend.research.graph import ResearchGraph
from backend.research.search_policy import normalize_queries, select_web_candidates


def test_query_variants_do_not_repeat_but_distinct_operators_survive():
    assert normalize_queries(
        ["  AI\t研究 ", "ai  研究", "", " \n", "AI -研究", 'AI "研究"', "新 查询", "新\t查询"],
        searched=["ai\n研究"],
    ) == ["AI -研究", 'AI "研究"', "新 查询"]


def test_duplicate_and_invalid_urls_do_not_spend_query_turn():
    rows = [
        [{"url": url} for url in ["https://a.example/1", "https://a.example/1?utm_source=x",
                                  "file:///private", "https://a.example/2"]],
        [{"url": f"https://b.example/{i}"} for i in range(4)],
    ]
    assert list(select_web_candidates(rows, 4)) == [
        "https://a.example/1", "https://b.example/0", "https://a.example/2", "https://b.example/1"]
    assert len(select_web_candidates(rows, 20)) == 6
    assert not select_web_candidates(rows, 0)
    assert not select_web_candidates([], 4)


def test_cross_query_duplicates_and_exhausted_queries_redistribute_slots():
    shared = {"url": "https://shared.example/", "name": "first"}
    rows = [[shared], [dict(shared, name="second"), {"url": "https://other.example/"}], []]
    result = select_web_candidates(rows, 8)
    assert len(result) == 2
    assert result[shared["url"]]["name"] == "first"


@pytest.mark.parametrize("queries,expected", [
    ([" ", " AI\t研究 ", "ai 研究"], ["AI 研究"]),
    ([" ", "\n"], ["原始研究主题"]),
])
async def test_planner_emits_only_executable_queries(runtime, queries, expected):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    graph.ask = AsyncMock(return_value=Plan(title="研究", questions=["问题"], queries=queries, scope="未知"))
    result = await graph.planner({"run_id": "plan-policy", "topic": "原始研究主题", "mode": "quick"})
    assert result["queries"] == expected
    assert result["plan"]["queries"] == expected
    event = await runtime.db.one("SELECT data FROM events WHERE run_id=? AND type='plan'", ("plan-policy",))
    assert json.loads(event["data"])["queries"] == expected


async def test_web_scout_normalizes_restored_queries_before_provider_calls(runtime):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    graph.search_web = AsyncMock(return_value=[])
    result = await graph.web_scout({"run_id": "restored-policy", "queries": ["AI 研究", "ai\t研究", " "]})
    graph.search_web.assert_awaited_once_with("AI 研究", "restored-policy")
    assert result["searched"] == ["AI 研究"]


@pytest.mark.parametrize("overrides,expected", [
    ({"mode": "quick"}, "quick_mode"),
    ({"gaps": []}, "no_gaps"),
    ({"round": 100}, "round_limit"),
    ({}, "no_new_queries"),
])
async def test_reflection_records_stop_without_wasting_another_round(runtime, overrides, expected):
    graph = ResearchGraph(runtime.settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    graph.ask = AsyncMock(return_value=Reflection(queries=["ai\t研究", " "], reason="补充"))
    state = {"run_id": "stop-policy", "user_id": "alice", "mode": "deep", "gaps": ["缺口"],
             "round": 0, "plan": {}, "searched": ["AI 研究"], **overrides}
    result = await graph.reflect(state)
    assert result["reflect"] is False
    event = await runtime.db.one("SELECT data FROM events WHERE run_id=? AND type='reflection'", ("stop-policy",))
    assert json.loads(event["data"])["stop_reason"] == expected
    assert graph.ask.await_count == (1 if expected == "no_new_queries" else 0)


@pytest.mark.parametrize("provider,key,expected", [
    ("auto", "configured", True),
    ("bocha", "configured", True),
    ("deepseek", "configured", False),
    ("auto", "", False),
])
async def test_reflection_capacity_matches_selected_provider(runtime, provider, key, expected):
    settings = runtime.settings.model_copy(update={
        "demo_mode": False, "web_search_provider": provider,
        "llm_api_key": SecretStr(""), "bocha_api_key": SecretStr(key)})
    graph = ResearchGraph(settings, runtime.db, runtime.providers, runtime.vectors, runtime.documents)
    graph.ask = AsyncMock(return_value=Reflection(queries=["new query"], reason="补充缺口"))
    result = await graph.reflect({"run_id": "capacity-policy", "user_id": "alice", "mode": "deep",
                                  "gaps": ["缺口"], "round": 0, "plan": {}, "searched": []})
    assert result["reflect"] is expected
    if not expected:
        graph.ask.assert_not_awaited()
        event = await runtime.db.one(
            "SELECT data FROM events WHERE run_id=? AND type='reflection'", ("capacity-policy",))
        assert json.loads(event["data"])["stop_reason"] == "no_search_capacity"
