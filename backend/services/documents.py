import asyncio
import hashlib
import io
import json
import os
import re
import zipfile
from pathlib import Path

from docx import Document as WordDocument
from pypdf import PdfReader
from rank_bm25 import BM25Okapi

from ..core.clamav import ScanRejected, ScanUnavailable, scan
from ..core.db import Database, now, uid
from ..infrastructure.providers import ServiceError


ALLOWED_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}


def tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.lower())


def validate_upload(name: str, content: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ServiceError("仅支持 TXT、Markdown、DOCX 和文字型 PDF")
    if not content:
        raise ServiceError("文件内容不能为空")
    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise ServiceError("PDF 文件签名无效")
    if suffix == ".docx":
        if not content.startswith(b"PK"):
            raise ServiceError("DOCX 文件签名无效")
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                expanded = sum(entry.file_size for entry in entries)
                compressed = max(1, sum(entry.compress_size for entry in entries))
                if len(entries) > 200 or expanded > 40 * 1024 * 1024 or expanded / compressed > 100:
                    raise ServiceError("DOCX 解压后的内容超过安全限制")
                if "[Content_Types].xml" not in archive.namelist():
                    raise ServiceError("DOCX 文件结构无效")
        except ServiceError:
            raise
        except zipfile.BadZipFile:
            raise ServiceError("DOCX 文件无法解压") from None
    return suffix


def semantic_chunks(locator: str, text: str) -> list[dict]:
    text = text.strip().replace("\x00", "")
    sentences = re.split(r"(?<=[。！？!?；;])\s*", text)
    output, current, start = [], "", 1
    for sentence in sentences:
        if not sentence:
            continue
        if current and len(current) + len(sentence) > 1100:
            output.append({"text": current, "locator": f"{locator} · 字符 {start}–{start + len(current) - 1}"})
            overlap = current[-180:]
            start += max(1, len(current) - len(overlap))
            current = overlap + sentence
        else:
            current += sentence
    if current:
        output.append({"text": current[:1500], "locator": f"{locator} · 字符 {start}–{start + len(current[:1500]) - 1}"})
    return output


def split_document(name: str, content: bytes) -> list[dict]:
    suffix = validate_upload(name, content)
    if suffix == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted or len(reader.pages) > 200:
                raise ServiceError("PDF 加密或超过 200 页限制")
            pages = [(f"第 {i + 1} 页", page.extract_text() or "") for i, page in enumerate(reader.pages)]
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("PDF 无法解析") from None
    elif suffix in {".txt", ".md"}:
        try:
            pages = [("正文", content.decode("utf-8-sig"))]
        except UnicodeError:
            raise ServiceError("文本资料需要使用 UTF-8 编码") from None
    else:
        try:
            document = WordDocument(io.BytesIO(content))
            pages, heading, buffer = [], "正文", []
            for paragraph in document.paragraphs:
                text = paragraph.text.strip()
                if not text:
                    continue
                if paragraph.style.name.lower().startswith("heading"):
                    if buffer:
                        pages.append((heading, "\n".join(buffer)))
                    heading, buffer = text[:180], []
                else:
                    buffer.append(text)
            if buffer:
                pages.append((heading, "\n".join(buffer)))
        except Exception:
            raise ServiceError("DOCX 无法解析") from None
    output = []
    for locator, text in pages:
        output.extend(semantic_chunks(locator, text))
    if not output:
        raise ServiceError("未提取到文字；扫描件 OCR 不在当前范围内")
    if len(output) > 400:
        raise ServiceError("资料超过 400 个片段限制，请拆分文件")
    return output


class Documents:
    def __init__(self, settings, db: Database, providers, vectors):
        self.settings, self.db, self.providers, self.vectors = settings, db, providers, vectors
        self.gate = asyncio.Semaphore(1)

    def _path(self, directory: str, doc_id: str) -> Path:
        return self.settings.data_dir / directory / doc_id

    async def add(self, user: str, name: str, content: bytes):
        name = Path(name.replace("\\", "/")).name[:160]
        if len(content) > 10 * 1024 * 1024:
            raise ServiceError("文件不能超过 10 MB")
        validate_upload(name, content)
        digest = hashlib.sha256(content).hexdigest()
        existing = await self.db.one("SELECT * FROM documents WHERE user_id=? AND hash=?", (user, digest))
        if existing and existing["status"] in {"ready", "indexing", "scanning"}:
            return existing
        doc_id = existing["id"] if existing else uid()
        if existing:
            await self.db.execute("UPDATE documents SET status='scanning',error='' WHERE id=?", (doc_id,))
        else:
            await self.db.execute("INSERT INTO documents(id,user_id,name,hash,status,created_at) VALUES(?,?,?,?,?,?)",
                                  (doc_id, user, name, digest, "scanning", now()))
        quarantine = self._path("quarantine", doc_id)
        await asyncio.to_thread(quarantine.write_bytes, content)
        try:
            await scan(self.settings, content)
        except ScanRejected as exc:
            await self.db.execute("UPDATE documents SET status='quarantined',error=? WHERE id=?", (str(exc), doc_id))
            exc.document_id = doc_id
            raise
        except ScanUnavailable as exc:
            await self.db.execute("UPDATE documents SET status='scan_failed',error=? WHERE id=?", (str(exc), doc_id))
            exc.document_id = doc_id
            raise
        target = self._path("uploads", doc_id)
        await asyncio.to_thread(os.replace, quarantine, target)
        await self.db.execute("UPDATE documents SET status='indexing',error='' WHERE id=?", (doc_id,))
        return await self.db.one("SELECT * FROM documents WHERE id=?", (doc_id,))

    async def ingest(self, doc_id: str):
        document = await self.db.one("SELECT * FROM documents WHERE id=? AND status='indexing'", (doc_id,))
        if not document:
            return
        async with self.gate:
            try:
                path = self._path("uploads", doc_id)
                if not path.is_file():
                    raise ServiceError("原始上传文件不存在，无法建立索引")
                content = await asyncio.to_thread(path.read_bytes)
                chunks = await asyncio.to_thread(split_document, document["name"], content)
                vectors = await self.providers.embed([part["text"] for part in chunks])
                rows = [dict(part, id=hashlib.sha256(f"{doc_id}:{i}".encode()).hexdigest()[:32],
                    user_id=document["user_id"], document_id=doc_id, title=document["name"], vector=vector)
                    for i, (part, vector) in enumerate(zip(chunks, vectors))]
                await self.vectors.upsert("documents", rows)
                async with self.db.connection() as conn:
                    await conn.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
                    await conn.executemany("INSERT INTO chunks(id,document_id,user_id,text,locator,vector) VALUES(?,?,?,?,?,?)",
                        [(row["id"], doc_id, document["user_id"], row["text"], row["locator"],
                          json.dumps(row["vector"]) if self.settings.demo_mode else "[]") for row in rows])
                    await conn.execute("UPDATE documents SET status='ready',error='' WHERE id=?", (doc_id,))
                    await conn.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc) if isinstance(exc, ServiceError) else "资料导入失败，请检查文件及服务配置"
                await self.db.execute("UPDATE documents SET status='failed',error=? WHERE id=?", (message, doc_id))
                raise

    async def search(self, user: str, queries: list[str], limit: int = 6) -> list[dict]:
        all_chunks = await self.db.rows("""SELECT c.*, d.name AS title FROM chunks c JOIN documents d
            ON c.document_id=d.id WHERE c.user_id=? AND d.status='ready'""", (user,))
        if not all_chunks:
            return []
        chunks_by_id = {row["id"]: row for row in all_chunks}
        corpus = [tokens(row["text"]) or ["_"] for row in all_chunks]
        bm25 = BM25Okapi(corpus)
        scores = {}
        for query in queries:
            query_tokens = tokens(query)
            lexical = bm25.get_scores(query_tokens)
            maximum = max(lexical) or 1
            for row, score in zip(all_chunks, lexical):
                entry = scores.setdefault(row["id"], {"row": row, "lexical": 0.0, "vector": 0.0})
                entry["lexical"] = max(entry["lexical"], float(score / maximum))
            vector = (await self.providers.embed([query]))[0]
            for hit in await self.vectors.search("documents", user, vector, limit=limit * 3):
                row = chunks_by_id.get(hit["id"])
                if not row:
                    continue
                entry = scores.setdefault(hit["id"], {"row": row, "lexical": 0.0, "vector": 0.0})
                entry["vector"] = max(entry["vector"], max(0.0, float(hit.get("score", 0))))
        ranked = sorted(scores.values(), key=lambda item: 0.65 * item["vector"] + 0.35 * item["lexical"], reverse=True)
        selected, selected_terms = [], []
        for item in ranked:
            row, terms = item["row"], set(tokens(item["row"]["text"]))
            similarity = max((len(terms & old) / max(1, len(terms | old)) for old in selected_terms), default=0.0)
            if similarity < 0.82:
                selected.append(dict(row, score=round(0.65 * item["vector"] + 0.35 * item["lexical"], 4),
                                     vector_score=round(item["vector"], 4), bm25_score=round(item["lexical"], 4)))
                selected_terms.append(terms)
            if len(selected) >= limit:
                break
        return selected

    async def delete(self, doc_id, user):
        await self.db.execute("UPDATE documents SET status='deleted' WHERE id=? AND user_id=?", (doc_id, user))
        await self.db.execute("DELETE FROM chunks WHERE document_id=? AND user_id=?", (doc_id, user))
        for directory in ("uploads", "quarantine"):
            path = self._path(directory, doc_id)
            if path.is_file():
                await asyncio.to_thread(path.unlink)
        await self.vectors.delete("documents", user, "document_id", doc_id)

    async def reindex(self, doc_id: str, user: str):
        document = await self.db.one("SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, user))
        if not document or document["status"] == "deleted":
            raise LookupError("资料不存在")
        if document["status"] not in {"ready", "failed"}:
            raise ServiceError("资料当前不可重建索引；隔离或扫描失败的文件不能进入资料库")
        path = self._path("uploads", doc_id)
        if not path.is_file():
            raise ServiceError("原始上传文件不存在，无法重建索引")
        await self.db.execute("UPDATE documents SET status='indexing',error='' WHERE id=?", (doc_id,))
        return await self.db.one("SELECT * FROM documents WHERE id=?", (doc_id,))

    async def close(self):
        return None
