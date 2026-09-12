import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from backend.domain.models import Analysis, Evidence, ReportDraft, ReportRepair, Verification
from backend.infrastructure.demo import generate
from backend.infrastructure.providers import ServiceError
from backend.research.graph import ResearchGraph, apply_report_repair


def graph_for(runtime, **settings):
    return ResearchGraph(runtime.settings.model_copy(update=settings), runtime.db,
                         runtime.providers, runtime.vectors, runtime.documents)


async def test_searches_overlap_with_bounded_concurrency_and_keep_query_order(runtime):
    graph = graph_for(runtime, web_search_concurrency=2)
    active = peak = 0
    started, release = asyncio.Event(), asyncio.Event()

    async def search(query, run_id):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            started.set()
        try:
            await release.wait()
            return [{"url": f"https://{query}.example/page", "summary": query}]
        finally:
            active -= 1

    graph.search_web = search
    task = asyncio.create_task(graph.web_scout({"run_id": "parallel", "queries": ["one", "two", "three"]}))
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert peak == 2
    finally:
        release.set()
        result = await task
    assert peak == 2
    assert [row["text"] for row in result["web_results"]] == ["one", "two", "three"]


async def test_failed_query_does_not_discard_other_search_results(runtime):
    graph = graph_for(runtime)

    async def search(query, run_id):
        if query == "bad":
            raise ServiceError("search unavailable")
        return [{"url": "https://good.example/", "summary": "surviving evidence"}]

    graph.search_web = search
    result = await graph.web_scout({"run_id": "partial-search", "queries": ["bad", "good"]})
    assert len(result["web_results"]) == 1
    assert await runtime.db.one("SELECT id FROM events WHERE run_id=? AND type='warning'", ("partial-search",))


async def test_cancelled_scout_leaves_no_background_searches(runtime):
    graph = graph_for(runtime, web_search_concurrency=2)
    active = 0
    started = asyncio.Event()

    async def search(query, run_id):
        nonlocal active
        active += 1
        if active == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    graph.search_web = search
    task = asyncio.create_task(graph.web_scout({"run_id": "cancel-search", "queries": ["one", "two", "three"]}))
    try:
        await asyncio.wait_for(started.wait(), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert active == 0


async def test_analysis_reuses_only_identical_inputs(runtime):
    graph = graph_for(runtime)
    graph.ask = AsyncMock(return_value=Analysis(claims=[{"text": "fact", "source_ids": ["one"]}], gaps=["gap"]))
    state = {"run_id": "analysis-reuse", "plan": {"questions": ["question"]}, "conflicts": [],
             "evidence": [{"id": "one", "text": "fact", "access": "fulltext"}]}
    state.update(await graph.analyst(state))
    assert (await graph.analyst(state))["claims"] == state["claims"]
    assert graph.ask.await_count == 1
    for field, value in [("conflicts", ["new conflict"]), ("plan", {"questions": ["different question"]}),
                         ("evidence", [{"id": "one", "text": "changed fact", "access": "fulltext"}])]:
        await graph.analyst({**state, field: value})
    assert graph.ask.await_count == 4
    assert (await graph.analyst({**state, "evidence": []}))["analysis_input_hash"] == ""


async def test_validation_deduplicates_full_sources_without_cross_claim_support(runtime):
    graph = graph_for(runtime)
    text = "original excerpt " * 700 + "retained tail"
    sources = {"one": {"id": "one", "text": text, "access": "fulltext"},
               "two": {"id": "two", "text": "different fact", "access": "fulltext"},
               "unused": {"id": "unused", "text": "unrelated", "access": "fulltext"}}
    draft = ReportDraft.model_validate({"title": "test", "sections": [{"heading": "test", "claims": [
        {"text": "retained tail", "source_ids": ["one"]},
        {"text": "original excerpt", "source_ids": ["one"]},
        {"text": "different fact", "source_ids": ["one"]},
        {"text": "retained tail", "source_ids": ["two"]}]}]})
    captured = {}

    async def ask(role, instruction, data, schema, run_id):
        captured.update(data)
        assert "不得跨条借用证据" in instruction
        return Verification.model_validate(generate(role, data))

    graph.ask = ask
    approved, _ = await graph.check_draft({"run_id": "compact-validator"}, draft, sources)
    assert approved == {0, 1}
    assert set(captured["sources"]) == {"one", "two"}
    assert captured["sources"]["one"]["text"] == text
    assert json.dumps(captured).count("retained tail") == 3  # once in original, twice in claims
    old = {"claims": [{"index": i, "text": c.text, "sources": [sources[k] for k in c.source_ids]}
                      for i, c in enumerate(draft.sections[0].claims)]}
    assert len(json.dumps(captured)) < len(json.dumps(old)) / 2


def test_partial_repair_cannot_change_approved_claims_or_expand_report():
    draft = ReportDraft.model_validate({"title": "test", "sections": [{"heading": "test", "claims": [
        {"text": text, "source_ids": ["one"]} for text in ["approved", "ambiguous", "omitted", "removed"]]}]})
    repair = ReportRepair.model_validate({"repairs": [
        {"index": 0, "replacement": {"text": "changed approved claim", "source_ids": ["other"]}},
        {"index": 1, "replacement": {"text": "first", "source_ids": ["one"]}},
        {"index": 1, "replacement": {"text": "duplicate", "source_ids": ["one"]}},
        {"index": 3, "replacement": None},
        {"index": 999, "replacement": {"text": "extra claim", "source_ids": ["one"]}},
    ]})
    updated, excluded = apply_report_repair(draft, {0}, repair)
    assert updated.model_dump() == draft.model_dump()
    assert excluded == {1, 2, 3}


async def test_partial_repairs_are_revalidated_and_unknown_citations_fail_closed(runtime):
    graph = graph_for(runtime)
    source = Evidence(id="one", kind="web", title="source", url="https://source.example/",
                      text="approved fact. corrected fact.", access="fulltext").model_dump()
    draft = ReportDraft.model_validate({"title": "test", "sections": [{"heading": "test", "claims": [
        {"text": text, "source_ids": ["one"]} for text in ["approved fact", "bad", "invented", "remove"]]}]})
    roles = []

    async def ask(role, instruction, data, schema, run_id):
        roles.append(role)
        if role == "repair":
            assert [row["index"] for row in data["failed_claims"]] == [1, 2, 3]
            assert "draft" not in data
            return ReportRepair.model_validate({"repairs": [
                {"index": 0, "replacement": {"text": "tampered", "source_ids": ["one"]}},
                {"index": 1, "replacement": {"text": "corrected fact", "source_ids": ["one"]}},
                {"index": 2, "replacement": {"text": "invented", "source_ids": ["unknown"]}},
                {"index": 3, "replacement": None},
            ]})
        return Verification.model_validate(generate(role, data))

    graph.ask = ask
    result = await graph.validator({"run_id": "partial-repair", "topic": "test", "evidence": [source],
                                    "draft": draft.model_dump(), "gaps": [], "conflicts": []})
    assert roles == ["validator", "repair", "validator"]
    assert "approved fact" in result["report"] and "corrected fact" in result["report"]
    assert "tampered" not in result["report"] and "invented" not in result["report"]
    assert result["validation"]["supported_claims"] == 2
    assert result["validation"]["removed_claims"] == 2
