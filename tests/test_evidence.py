import pytest

from backend.services.documents import split_document
from backend.research.graph import merge_evidence, sanitize_claims, supported_indices
from backend.domain.models import ClaimCheck, ReportDraft, Verification
from backend.infrastructure.providers import ServiceError
from backend.core import security
from backend.core.security import canonical_url, public_addresses


def evidence(source_id, text, access="summary"):
    return {"id": source_id, "text": text, "access": access}


def test_deduplication_and_fulltext_precedence():
    rows = merge_evidence([evidence("one", "same text"), evidence("two", "same  text"), evidence("one", "full article", "fulltext")])
    assert len(rows) == 1
    assert rows[0]["text"] == "full article"


def test_unknown_citations_are_rejected():
    draft = ReportDraft.model_validate({"title": "test", "sections": [{"heading": "test", "claims": [
        {"text": "good", "source_ids": ["one"]}, {"text": "bad", "source_ids": ["one", "invented"]}]}]})
    assert [i for i, _ in sanitize_claims(draft, {"one": {}})] == [0]


def test_duplicate_or_omitted_verdict_fails_closed():
    check = Verification(checks=[ClaimCheck(index=0,supported=True,reason="ok"),
        ClaimCheck(index=0,supported=False,reason="conflict"), ClaimCheck(index=1,supported=True,reason="ok",atomic_checks=[{"text":"approved","supported":True}]),
        ClaimCheck(index=999,supported=True,reason="invented")])
    assert supported_indices(check, {0,1,2}, {0:"conflict",1:"approved",2:"missing"}) == {1}


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/a", "https://user:secret@example.com", "https://example.com:8888"])
def test_unsafe_url_schemes_and_credentials(url):
    with pytest.raises(ServiceError):
        canonical_url(url)


def test_url_normalizes_tracking_and_fragment():
    assert canonical_url("https://EXAMPLE.com/a?q=test&utm_source=x#part") == "https://example.com/a?q=test"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1"])
async def test_private_dns_results_are_blocked(monkeypatch, ip):
    import asyncio
    async def resolve(*args, **kwargs):
        return [(2,1,6,"",(ip,443))]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    with pytest.raises(ServiceError):
        await public_addresses("example.com",443)


async def test_mixed_public_private_dns_is_blocked(monkeypatch):
    import asyncio
    async def resolve(*args, **kwargs):
        return [(2,1,6,"",("8.8.8.8",443)), (2,1,6,"",("127.0.0.1",443))]
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    with pytest.raises(ServiceError):
        await public_addresses("example.com",443)


async def test_docker_egress_proxy_uses_verified_public_dns(monkeypatch):
    import asyncio

    async def resolve(*args, **kwargs):
        return [(2, 1, 6, "", ("198.18.0.239", 443))]

    async def verified(host):
        assert host == "example.com"
        return ["8.8.8.8"]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(security, "verified_public_addresses", verified)
    assert await public_addresses("example.com", 443) == ["8.8.8.8"]


def test_document_chunk_locations_and_limits():
    chunks = split_document("notes.md", ("研究资料。"*800).encode())
    assert len(chunks) > 1
    assert all("字符" in row["locator"] and len(row["text"]) <= 1500 for row in chunks)
    with pytest.raises(ServiceError):
        split_document("bad.exe", b"abc")
    with pytest.raises(ServiceError):
        split_document("empty.txt", b"   ")


@pytest.mark.parametrize('max_bytes', [1_500_000, 128])
async def test_long_web_source_keeps_relevant_paragraphs_within_download_limit(monkeypatch, max_bytes):
    import httpx
    from backend.research.graph import evidence_context

    introduction = '<p>Background introduction with unrelated information.</p>' * 300
    definition = 'ID Token contains authentication claims. Access Token is used to access protected resources.'
    code = 'def example():\n    return "a  b"'
    html = ('<main>' + introduction + '<h2>Token definitions</h2><p>ID Token contains\n'
            '<em>authentication</em> claims.\nAccess Token is used to access protected resources.</p>'
            '<pre>' + code + '</pre></main>')
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, headers={'content-type': 'text/html; charset=utf-8'}, content=html.encode()))

    async def addresses(*args):
        return ['8.8.8.8']

    monkeypatch.setattr(security, 'public_addresses', addresses)
    monkeypatch.setattr(security.httpx, 'AsyncClient',
                        lambda **kwargs: original_client(transport=transport, **kwargs))
    if max_bytes == 128:
        with pytest.raises(ServiceError, match='大小限制'):
            await security.fetch_text('https://example.com/specification', max_bytes=max_bytes)
        return
    text = await security.fetch_text('https://example.com/specification', max_bytes=max_bytes)
    assert definition in text
    assert code in text
    assert '\n' in text
    context = evidence_context([{'id': 'spec', 'text': text}], text_limit=1500, total_limit=1500,
                               query='ID Token Access Token authentication')
    assert definition in context[0]['text']
    assert len(context[0]['text'].encode()) <= 1500


async def test_web_reader_refreshes_legacy_prefix_cache_once(runtime, monkeypatch):
    from unittest.mock import AsyncMock
    from backend.core.db import now
    from backend.research import graph as graph_module

    url = 'https://example.com/specification'
    await runtime.db.execute('INSERT INTO web_cache VALUES(?,?,?,?,?)',
                             (url, 'legacy first 10000 characters', now(), 'fulltext', ''))
    fetch = AsyncMock(return_value='Complete source with relevant paragraphs.')
    monkeypatch.setattr(graph_module, 'fetch_text', fetch)
    graph = graph_module.ResearchGraph(runtime.settings, runtime.db, runtime.providers,
                                       runtime.vectors, runtime.documents)
    assert await graph.read_web(url) == 'Complete source with relevant paragraphs.'
    assert await graph.read_web(url) == 'Complete source with relevant paragraphs.'
    assert fetch.await_count == 1
