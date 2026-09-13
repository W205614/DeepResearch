import json

import httpx
import pytest

from backend.core.config import Settings, endpoint
from backend.core.db import Database
from backend.domain.models import Analysis, Route
from backend.infrastructure.providers import Providers, ServiceError, remove_json_trailing_commas


async def make_provider(tmp_path, response):
    db = Database(tmp_path / 'test.sqlite3')
    await db.init()
    settings = Settings(_env_file=None, llm_api_key='test-key', embedding_api_key='test-key', embedding_dimension=3)
    client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    return Providers(settings, db, client)


def test_model_id_alias_and_endpoint():
    settings = Settings(_env_file=None, LLM_MODEL_ID="chosen-model")
    assert settings.llm_model_id == "chosen-model"
    assert endpoint("https://api.deepseek.com", "chat/completions") == "https://api.deepseek.com/chat/completions"
    assert endpoint("https://provider/v1/", "embeddings") == "https://provider/v1/embeddings"


async def test_embedding_dimension_mismatch(tmp_path):
    p = await make_provider(tmp_path, lambda request: httpx.Response(200,json={"data":[{"index":0,"embedding":[1,2]}]}))
    with pytest.raises(ServiceError,match="维度不匹配"):
        await p.embed(["test"])
    await p.close()


async def test_embedding_order_is_validated(tmp_path):
    p = await make_provider(tmp_path, lambda request: httpx.Response(200,json={"data":[{"index":1,"embedding":[1,2,3]}]}))
    with pytest.raises(ServiceError,match="顺序"):
        await p.embed(["test"])
    await p.close()


async def test_api_error_does_not_expose_response_or_secret(tmp_path):
    p = await make_provider(tmp_path, lambda request: httpx.Response(401,json={"error":"sensitive echoed test-key"}))
    with pytest.raises(ServiceError) as captured:
        await p.embed(["test"])
    assert "test-key" not in str(captured.value) and "sensitive" not in str(captured.value)
    assert "401" in str(captured.value)
    await p.close()


async def test_invalid_json_retries_once(tmp_path):
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200,json={"choices":[{"message":{"content":"invalid json"}}]})
    p = await make_provider(tmp_path, response)
    with pytest.raises(ServiceError,match="重试一次"):
        await p.structured("router","test",{},Route,"run")
    assert len(calls) == 2
    retry = json.loads(calls[1].content)["messages"][-1]["content"]
    assert "错误类别" in retry and "尾逗号" in retry
    await p.close()


async def test_trailing_comma_is_repaired_without_another_model_call(tmp_path):
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '{"mode":"quick","reason":"ok",}'}}]})
    p = await make_provider(tmp_path, response)
    result = await p.structured("router", "test", {}, Route, "run")
    assert result.mode == "quick" and result.reason == "ok"
    assert len(calls) == 1
    await p.close()


def test_trailing_comma_repair_does_not_change_quoted_text():
    value = '{"text":"literal ,} and escaped \\\" quote", "items":[1,2,],}'
    assert remove_json_trailing_commas(value) == '{"text":"literal ,} and escaped \\\" quote", "items":[1,2]}'


def test_analysis_capacity_covers_multi_question_research():
    claims = [{"text": f"claim {index}", "source_ids": ["source"]} for index in range(25)]
    assert len(Analysis.model_validate({"claims": claims, "gaps": []}).claims) == 25


def test_deepseek_native_citations_only():
    result = {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "模型自己写的 https://bad.example",
            "annotations": [{"type": "url_citation", "url": "https://source.example/report", "title": "原始报告"}]}]}]
    }
    assert Providers._deepseek_citations(result) == [{"url": "https://source.example/report", "name": "原始报告", "summary": ""}]
    assert Providers._deepseek_citations({"output_text": "https://invented.example"}) == []


def test_deepseek_server_open_page_is_a_source():
    result = {"output": [
        {"type": "web_search_call", "action": {"type": "search", "queries": ["test"]}},
        {"type": "web_search_call", "action": {"type": "open_page", "url": "https://source.example/report"}},
        {"type": "web_search_call", "action": {"type": "open_page", "url": "https://source.example/report"}},
    ]}
    assert Providers._deepseek_citations(result) == [{"url": "https://source.example/report", "name": "https://source.example/report", "summary": ""}]


async def test_deepseek_search_request_is_forced_and_usage_is_recorded(tmp_path):
    def response(request):
        assert request.url.path == "/responses"
        body = json.loads(request.content)
        assert body["tools"] == [{"type": "web_search"}]
        assert body["tool_choice"] == {"type": "web_search"}
        return httpx.Response(200, json={"usage": {"input_tokens": 2, "output_tokens": 3}, "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "ok", "annotations": [
                {"type": "url_citation", "url": "https://source.example", "title": "资料"}]}]}]})
    p = await make_provider(tmp_path, response)
    assert await p.search("query", "search-run") == [{"url": "https://source.example", "name": "资料", "summary": ""}]
    counter = await p.db.one("SELECT * FROM counters WHERE run_id='search-run'")
    assert counter["search_calls"] == 1 and counter["llm_calls"] == 1
    await p.close()


async def test_deepseek_no_native_citation_is_rejected(tmp_path):
    p = await make_provider(tmp_path, lambda request: httpx.Response(200, json={"output_text": "answer without evidence"}))
    with pytest.raises(ServiceError, match="来源 URL"):
        await p.search("query", "search-run")
    await p.close()


async def test_vision_request_uses_the_configured_model_and_same_provider_credentials(tmp_path):
    def response(request):
        assert request.url.path == "/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-v4-flash-vision-exp"
        image = body["messages"][1]["content"][0]["image_url"]["url"]
        assert image.startswith("data:image/png;base64,")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"readable": True, "text": "图表文字"})}}]})

    p = await make_provider(tmp_path, response)
    assert await p.describe_image(b"png-bytes", "image/png") == "图表文字"
    await p.close()

async def test_writer_retry_identifies_overfull_section_without_echoing_text(tmp_path):
    from backend.domain.models import ReportDraft
    calls = []
    def response(request):
        calls.append(json.loads(request.content))
        count = 13 if len(calls) == 1 else 12
        draft = {"title": "report", "sections": [{"heading": "findings", "claims": [
            {"text": "sensitive-source-text", "source_ids": ["W-1"]} for _ in range(count)]}]}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(draft)}}]})
    p = await make_provider(tmp_path, response)
    try:
        draft = await p.structured("writer", "test", {}, ReportDraft, "run")
        assert len(draft.sections[0].claims) == 12
        feedback = calls[1]["messages"][-1]["content"]
        assert "sections.0.claims" in feedback and "max_length=12" in feedback
        assert "sensitive-source-text" not in feedback
    finally:
        await p.close()


async def test_token_truncation_is_distinct_from_schema_length(tmp_path):
    p = await make_provider(tmp_path, lambda request: httpx.Response(200, json={
        "choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}))
    try:
        with pytest.raises(ServiceError, match="output_truncated"):
            await p.structured("writer", "test", {}, Route, "run")
    finally:
        await p.close()

async def test_writer_persistent_overflow_fails_instead_of_silently_dropping_claims(tmp_path):
    from backend.domain.models import ReportDraft
    draft = {"title": "report", "sections": [{"heading": "findings", "claims": [
        {"text": "fact", "source_ids": ["W-1"]} for _ in range(13)]}]}
    p = await make_provider(tmp_path, lambda request: httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(draft)}}]}))
    try:
        with pytest.raises(ServiceError, match="too_long"):
            await p.structured("writer", "test", {}, ReportDraft, "run")
    finally:
        await p.close()
