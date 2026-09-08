import asyncio
import io
import zipfile

import pytest

from backend.core.clamav import ScanRejected, ScanUnavailable
from backend.services import documents as document_service
from backend.services.runtime import Runtime


async def wait_for_status(client, document_id, expected):
    for _ in range(80):
        rows = (await client.get("/api/documents")).json()
        row = next((item for item in rows if item["id"] == document_id), None)
        if row and row["status"] == expected:
            return row
        await asyncio.sleep(0.02)
    raise AssertionError(f"document {document_id} did not reach {expected}")


async def test_clean_upload_moves_out_of_quarantine_and_is_indexed(api_client, monkeypatch):
    app, client = api_client
    app.state.runtime.settings.document_scan_mode = "clamav"

    async def clean(*_):
        return None

    monkeypatch.setattr(document_service, "scan", clean)
    response = await client.post("/api/documents", files={"file": ("clean.md", "可检索资料".encode(), "text/markdown")})
    assert response.status_code == 202, response.text
    document = await wait_for_status(client, response.json()["id"], "ready")
    assert (app.state.runtime.settings.data_dir / "uploads" / document["id"]).is_file()
    assert not (app.state.runtime.settings.data_dir / "quarantine" / document["id"]).exists()


@pytest.mark.parametrize("error_type, expected", [(ScanRejected, "quarantined"), (ScanUnavailable, "scan_failed")])
async def test_rejected_or_unavailable_scan_is_not_indexed_and_can_be_deleted(api_client, monkeypatch, error_type, expected):
    app, client = api_client
    app.state.runtime.settings.document_scan_mode = "clamav"

    async def rejected(*_):
        raise error_type("scanner decision")

    monkeypatch.setattr(document_service, "scan", rejected)
    response = await client.post("/api/documents", files={"file": ("blocked.md", "隔离资料".encode(), "text/markdown")})
    assert response.status_code == 503
    rows = (await client.get("/api/documents")).json()
    assert rows and rows[0]["status"] == expected
    document = rows[0]
    assert (app.state.runtime.settings.data_dir / "quarantine" / document["id"]).is_file()
    assert (await client.post("/api/documents/search", json={"query": "隔离"})).json() == []
    exported = await client.get("/api/data/export")
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert document["id"] not in archive.read("documents.json").decode("utf-8")
    assert (await client.delete(f"/api/documents/{document['id']}")).status_code == 200
    assert not (app.state.runtime.settings.data_dir / "quarantine" / document["id"]).exists()
    audit = (await client.get("/api/workspaces/alice/audit")).json()
    assert any(row["action"] == "document.upload" and row["result"] == "rejected" for row in audit)


async def test_indexing_document_recovers_after_runtime_restart(settings):
    first = await Runtime(settings).start()
    try:
        document = await first.documents.add("alice", "recover.md", "可恢复索引资料".encode())
        assert document["status"] == "indexing"
    finally:
        await first.close()
    second = await Runtime(settings).start()
    try:
        for _ in range(80):
            row = await second.db.one("SELECT * FROM documents WHERE id=?", (document["id"],))
            if row and row["status"] == "ready":
                break
            await asyncio.sleep(0.02)
        assert row["status"] == "ready"
    finally:
        await second.close()


def test_long_unpunctuated_text_keeps_its_tail_in_the_index():
    parts = document_service.semantic_chunks("正文", "A" * 1600 + "UNIQUE_END_MARKER")

    assert all(len(part["text"]) <= 1100 for part in parts)
    assert "UNIQUE_END_MARKER" in "".join(part["text"] for part in parts)
