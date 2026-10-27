import asyncio
import pytest

from backend.core.reliability import ServiceError
from backend.services.parser_process import parse_document


async def test_parser_timeout_reaps_process_and_next_document_is_usable(settings, monkeypatch):
    original = asyncio.create_subprocess_exec
    children = []
    async def tracked(*args, **kwargs):
        child = await original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(asyncio, "create_subprocess_exec", tracked)
    settings.parser_wall_seconds = 0.001
    with pytest.raises(ServiceError) as failure:
        await parse_document("large.pdf", b"%PDF-1.7", settings)
    assert failure.value.code == "parse_timeout"
    assert children[0].returncode is not None
    settings.parser_wall_seconds = 30
    blocks = await parse_document("normal.md", "# 隔离\n后续文件正常".encode(), settings)
    assert "后续文件正常" in blocks[0].text
    assert children[1].returncode == 0


async def test_parser_rejects_invalid_document_without_exposing_environment(settings, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "must-not-reach-parser")
    with pytest.raises(ServiceError) as failure:
        await parse_document("bad.pdf", b"invalid", settings)
    assert failure.value.code == "parse_rejected"
    assert "must-not-reach-parser" not in str(failure.value)
