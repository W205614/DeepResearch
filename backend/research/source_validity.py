"""Revalidate persisted evidence against current authorized source records."""
from ..core.reliability import ServiceError


async def validate_sources(db, user, sources):
    for source in sources:
        if source.get("kind") == "local":
            row = await db.one("""SELECT c.text,d.index_version,d.hash FROM chunks c JOIN documents d ON d.id=c.document_id
                WHERE c.id=? AND d.id=? AND c.user_id=? AND d.user_id=? AND d.status IN ('ready','rebuilding')""",
                (source.get("chunk_id", ""), source.get("document_id", ""), user, user))
            if not row or row["index_version"] != source.get("index_version") or row["hash"] != source.get("document_hash") or row["text"] != source.get("text"):
                raise ServiceError("引用资料已删除、变化或缺少版本记录，请重新研究", code="source_changed", retryable=False)
        elif source.get("kind") == "attachment":
            if not await db.one("SELECT id FROM attachments WHERE id=? AND user_id=? AND status='ready'",
                                (source.get("attachment_id", ""), user)):
                raise ServiceError("图片已失效，请重新上传", code="source_changed", retryable=False)
