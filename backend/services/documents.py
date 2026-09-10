from ..core.consistency import affected
import asyncio
import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path

from docx import Document as WordDocument
from pypdf import PdfReader
from rank_bm25 import BM25Okapi

from ..core.clamav import ScanRejected, ScanUnavailable, scan
from ..core.db import Database, now, uid
from ..core.metrics import RAG_RETRIEVAL_SECONDS
from ..infrastructure.providers import ServiceError
from ..infrastructure.object_store import ObjectStore


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
ALLOWED_SUFFIXES = {".txt", ".md", ".pdf", ".docx", *IMAGE_SUFFIXES}
IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
}


def image_media_type(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.lower())


def validate_upload(name: str, content: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ServiceError("仅支持 TXT、Markdown、DOCX、文字型 PDF 与 JPG/PNG/GIF/WebP 图片")
    if not content:
        raise ServiceError("文件内容不能为空")
    if suffix in IMAGE_SUFFIXES:
        if image_media_type(content) != IMAGE_MEDIA_TYPES[suffix]:
            raise ServiceError("图片文件签名无效或与扩展名不匹配")
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

    def flush() -> str:
        nonlocal start
        output.append({"text": current, "locator": f"{locator} · 字符 {start}–{start + len(current) - 1}"})
        overlap = current[-180:]
        start += len(current) - len(overlap)
        return overlap

    for sentence in sentences:
        if not sentence:
            continue
        while sentence:
            available = 1100 - len(current)
            if available <= 0:
                current = flush()
                continue
            if len(sentence) <= available:
                current += sentence
                break
            current += sentence[:available]
            sentence = sentence[available:]
            current = flush()
    if current:
        output.append({"text": current, "locator": f"{locator} · 字符 {start}–{start + len(current) - 1}"})
    return output


def split_document(name: str, content: bytes) -> list[dict]:
    suffix = validate_upload(name, content)
    if suffix in IMAGE_SUFFIXES:
        return []
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
            for index, table in enumerate(document.tables, start=1):
                rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
                rows = [row for row in rows if any(row)]
                if rows:
                    header = " | ".join(rows[0])
                    body = [" | ".join(row) for row in rows[1:]]
                    pages.append((f"表格 {index}", "表头：" + header + "\n" + "\n".join(body)))
            if buffer:
                pages.append((heading, "\n".join(buffer)))
        except Exception:
            raise ServiceError("DOCX 无法解析") from None
    output = []
    for locator, text in pages:
        output.extend(semantic_chunks(locator, text))
    if not output:
        raise ServiceError("未提取到文字；扫描件需要可提取的图片内容")
    if len(output) > 400:
        raise ServiceError("资料超过 400 个片段限制，请拆分文件")
    return output


def visual_assets(name: str, content: bytes) -> list[tuple[str, str, bytes]]:
    """Return only images whose actual bytes match a supported vision media type."""
    suffix = Path(name).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return [("图片", IMAGE_MEDIA_TYPES[suffix], content)]
    if suffix != ".pdf":
        return []
    try:
        reader = PdfReader(io.BytesIO(content))
        assets = []
        for page_index, page in enumerate(reader.pages, start=1):
            for image_index, image in enumerate(page.images, start=1):
                data = image.data
                media_type = image_media_type(data)
                if media_type:
                    assets.append((f"第 {page_index} 页图片 {image_index}", media_type, data))
        return assets
    except Exception:
        return []


class Documents:
    def __init__(self, settings, db: Database, providers, vectors):
        self.settings, self.db, self.providers, self.vectors = settings, db, providers, vectors
        self.gate = asyncio.Semaphore(1)
        self.store = ObjectStore(settings)
        self._corpora: dict[str, tuple[str, list[dict], dict[str, dict], BM25Okapi]] = {}

    async def start(self) -> None:
        await self.store.start()

    async def add(self, user: str, name: str, content: bytes):
        async with self.db.guard("documents:" + user):
            return await self._add(user, name, content)

    async def _add(self, user: str, name: str, content: bytes):
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
            await self.db.execute("UPDATE documents SET status='scanning',error='',index_version=index_version+1 WHERE id=?", (doc_id,))
        else:
            await self.db.execute("INSERT INTO documents(id,user_id,name,hash,status,created_at,index_version) VALUES(?,?,?,?,?,?,1)",
                                  (doc_id, user, name, digest, "scanning", now()))
        await self.store.put("quarantine", doc_id, content)
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
        await self.store.move("quarantine", "uploads", doc_id)
        await self.invalidate_search_cache(user)
        await self.db.execute("UPDATE documents SET status='indexing',error='' WHERE id=?", (doc_id,))
        return await self.db.one("SELECT * FROM documents WHERE id=?", (doc_id,))

    async def invalidate_search_cache(self, user: str) -> None:
        self._corpora.pop(user, None)
        await self.db.execute("DELETE FROM document_search_cache WHERE user_id=?", (user,))

    async def record_retrieval(self, run_id: str, data: dict) -> None:
        if run_id:
            await self.db.event(run_id, "local_retrieval", data)

    async def split_for_index(self, name: str, content: bytes) -> list[dict]:
        assets = await asyncio.to_thread(visual_assets, name, content)
        try:
            chunks = await asyncio.to_thread(split_document, name, content)
        except ServiceError as exc:
            if not assets or not str(exc).startswith("未提取到文字"):
                raise
            chunks = []
        if len(assets) > 20:
            raise ServiceError("资料中的图片超过 20 张限制，请拆分文件")
        if sum(len(asset[2]) for asset in assets) > 20 * 1024 * 1024:
            raise ServiceError("资料中的图片总大小超过 20 MB 限制")
        for locator, media_type, image in assets:
            extracted = await self.providers.describe_image(image, media_type)
            chunks.extend(semantic_chunks(locator, "图片解析：" + extracted))
        if not chunks:
            raise ServiceError("未提取到可检索文字或图片内容")
        if len(chunks) > 400:
            raise ServiceError("资料超过 400 个片段限制，请拆分文件")
        return chunks

    async def ingest(self, doc_id: str):
        document = await self.db.one("SELECT * FROM documents WHERE id=? AND status='indexing'", (doc_id,))
        if not document:
            return
        async with self.gate:
            try:
                content = await self.store.get("uploads", doc_id)
                chunks = await self.split_for_index(document["name"], content)
                vectors = await self.providers.embed([part["text"] for part in chunks])
                rows = [dict(part, id=hashlib.sha256(f"{doc_id}:{document['index_version']}:{i}".encode()).hexdigest()[:32],
                    user_id=document["user_id"], document_id=doc_id, index_version=document["index_version"], title=document["name"], vector=vector)
                    for i, (part, vector) in enumerate(zip(chunks, vectors))]
                async with self.db.guard("documents:" + document["user_id"]):
                    current = await self.db.one("SELECT status,index_version FROM documents WHERE id=?", (doc_id,))
                    if not current or current["status"] != "indexing" or current["index_version"] != document["index_version"]:
                        return
                    await self.db.execute("INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version) VALUES(?,?,?)",
                                          (doc_id, document["user_id"], document["index_version"]))
                    await self.vectors.upsert("documents", rows)
                    async with self.db.connection() as conn:
                        result = await conn.execute("""UPDATE documents SET status='ready',error=''
                            WHERE id=? AND status='indexing' AND index_version=?""", (doc_id, document["index_version"]))
                        if affected(result):
                            await conn.execute("DELETE FROM document_cleanup WHERE document_id=? AND version=?",
                                               (doc_id, document["index_version"]))
                            await conn.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
                            await conn.executemany("INSERT INTO chunks(id,document_id,user_id,text,locator,vector) VALUES(?,?,?,?,?,?)",
                                [(row["id"], doc_id, document["user_id"], row["text"], row["locator"],
                                  json.dumps(row["vector"]) if self.settings.demo_mode else "[]") for row in rows])
                        else:
                            await conn.execute("INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version) VALUES(?,?,?)",
                                               (doc_id, document["user_id"], document["index_version"]))
                        await conn.commit()
                await self.cleanup_pending()
                await self.invalidate_search_cache(document["user_id"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc) if isinstance(exc, ServiceError) else "资料导入失败，请检查文件及服务配置"
                await self.db.execute("UPDATE documents SET status='failed',error=? WHERE id=? AND status='indexing' AND index_version=?",
                                      (message, doc_id, document["index_version"]))
                raise

    async def search(self, user: str, queries: list[str], limit: int = 6, run_id: str = "") -> list[dict]:
        started = time.monotonic()
        normalized_queries = [query.strip() for query in queries if query.strip()]
        if not normalized_queries:
            return []
        documents = await self.db.rows("SELECT id,hash,index_version FROM documents WHERE user_id=? AND status='ready' ORDER BY id", (user,))
        corpus_hash = hashlib.sha256("|".join(f"{row['id']}:{row['hash']}:{row['index_version']}" for row in documents).encode()).hexdigest()
        query_hash = hashlib.sha256(json.dumps(normalized_queries, ensure_ascii=False).encode()).hexdigest()
        cache_started = time.monotonic()
        cached = await self.db.one("""SELECT result FROM document_search_cache
            WHERE user_id=? AND corpus_hash=? AND query_hash=? AND limit_value=?""",
                                   (user, corpus_hash, query_hash, limit))
        cache_ms = round((time.monotonic() - cache_started) * 1000, 2)
        RAG_RETRIEVAL_SECONDS.labels(stage="cache_lookup").observe(cache_ms / 1000)
        if cached:
            try:
                result = json.loads(cached["result"])
                if isinstance(result, list):
                    total_ms = round((time.monotonic() - started) * 1000, 2)
                    RAG_RETRIEVAL_SECONDS.labels(stage="total").observe(total_ms / 1000)
                    await self.record_retrieval(run_id, {"cache_hit": True, "queries": len(normalized_queries),
                        "corpus_documents": len(documents), "corpus_chunks": None, "cache_ms": cache_ms,
                        "total_ms": total_ms})
                    return result
            except (TypeError, ValueError, json.JSONDecodeError):
                await self.db.execute("DELETE FROM document_search_cache WHERE user_id=? AND corpus_hash=? AND query_hash=? AND limit_value=?",
                                      (user, corpus_hash, query_hash, limit))
        corpus_started = time.monotonic()
        cached_corpus = self._corpora.get(user)
        if cached_corpus and cached_corpus[0] == corpus_hash:
            _, all_chunks, chunks_by_id, bm25 = cached_corpus
            corpus_cache_hit = True
        else:
            all_chunks = await self.db.rows("""SELECT c.*, d.name AS title FROM chunks c JOIN documents d
                ON c.document_id=d.id WHERE c.user_id=? AND d.status='ready'""", (user,))
            chunks_by_id = {row["id"]: row for row in all_chunks}
            build_started = time.monotonic()
            bm25 = BM25Okapi([tokens(row["text"]) or ["_"] for row in all_chunks]) if all_chunks else None
            build_ms = round((time.monotonic() - build_started) * 1000, 2)
            RAG_RETRIEVAL_SECONDS.labels(stage="bm25_build").observe(build_ms / 1000)
            self._corpora[user] = (corpus_hash, all_chunks, chunks_by_id, bm25)
            corpus_cache_hit = False
        corpus_ms = round((time.monotonic() - corpus_started) * 1000, 2)
        RAG_RETRIEVAL_SECONDS.labels(stage="corpus_load").observe(corpus_ms / 1000)
        if not all_chunks or not bm25:
            return []

        async def retrieve_query(query: str):
            lexical_started = time.monotonic()
            lexical_scores = bm25.get_scores(tokens(query))
            lexical_ms = round((time.monotonic() - lexical_started) * 1000, 2)
            embed_started = time.monotonic()
            vector = (await self.providers.embed([query]))[0]
            embed_ms = round((time.monotonic() - embed_started) * 1000, 2)
            vector_started = time.monotonic()
            hits = await self.vectors.search("documents", user, vector, limit=limit * 3)
            vector_ms = round((time.monotonic() - vector_started) * 1000, 2)
            return lexical_scores, hits, lexical_ms, embed_ms, vector_ms

        rows_by_query = await asyncio.gather(*(retrieve_query(query) for query in normalized_queries))
        scores = {}
        lexical_ms, embed_ms, vector_ms = 0.0, 0.0, 0.0
        for lexical, hits, lexical_time, embed_time, vector_time in rows_by_query:
            lexical_ms += lexical_time
            embed_ms += embed_time
            vector_ms += vector_time
            maximum = max(lexical) or 1
            for row, score in zip(all_chunks, lexical):
                entry = scores.setdefault(row["id"], {"row": row, "lexical": 0.0, "vector": 0.0})
                entry["lexical"] = max(entry["lexical"], float(score / maximum))
            for hit in hits:
                row = chunks_by_id.get(hit["id"])
                if row:
                    entry = scores.setdefault(hit["id"], {"row": row, "lexical": 0.0, "vector": 0.0})
                    entry["vector"] = max(entry["vector"], max(0.0, float(hit.get("score", 0))))
        RAG_RETRIEVAL_SECONDS.labels(stage="bm25_score").observe(lexical_ms / 1000)
        RAG_RETRIEVAL_SECONDS.labels(stage="query_embedding").observe(embed_ms / 1000)
        RAG_RETRIEVAL_SECONDS.labels(stage="vector_search").observe(vector_ms / 1000)

        fusion_started = time.monotonic()
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
        fusion_ms = round((time.monotonic() - fusion_started) * 1000, 2)
        total_ms = round((time.monotonic() - started) * 1000, 2)
        RAG_RETRIEVAL_SECONDS.labels(stage="fusion").observe(fusion_ms / 1000)
        RAG_RETRIEVAL_SECONDS.labels(stage="total").observe(total_ms / 1000)
        await self.db.execute("""INSERT OR REPLACE INTO document_search_cache
            (user_id,corpus_hash,query_hash,limit_value,result,created_at) VALUES(?,?,?,?,?,?)""",
                              (user, corpus_hash, query_hash, limit, json.dumps(selected, ensure_ascii=False), now()))
        await self.record_retrieval(run_id, {"cache_hit": False, "corpus_cache_hit": corpus_cache_hit,
            "queries": len(normalized_queries), "corpus_documents": len(documents), "corpus_chunks": len(all_chunks),
            "cache_ms": cache_ms, "corpus_ms": corpus_ms, "bm25_ms": round(lexical_ms, 2),
            "embedding_ms": round(embed_ms, 2), "vector_ms": round(vector_ms, 2), "fusion_ms": fusion_ms,
            "total_ms": total_ms})
        return selected

    async def delete(self, doc_id, user):
        async with self.db.guard("documents:" + user):
            document = await self.db.one("SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, user))
            if not document:
                return
            async with self.db.connection() as conn:
                await conn.execute("INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version) VALUES(?,?,?)",
                                   (doc_id, user, document["index_version"]))
                await conn.execute("UPDATE documents SET status='deleted',index_version=index_version+1 WHERE id=? AND user_id=?", (doc_id, user))
                await conn.execute("DELETE FROM chunks WHERE document_id=? AND user_id=?", (doc_id, user))
                await conn.commit()
            await self.invalidate_search_cache(user)
        await self.cleanup_pending()

    async def cleanup_pending(self):
        for row in await self.db.rows("SELECT * FROM document_cleanup"):
            async with self.db.guard("documents:" + row["user_id"]):
                document = await self.db.one("SELECT status,index_version FROM documents WHERE id=?", (row["document_id"],))
                if document and document["status"] in {"ready", "indexing"} and document["index_version"] == row["version"]:
                    continue
                try:
                    await self.vectors.delete_document_version(row["user_id"], row["document_id"], row["version"])
                    if not document or document["status"] == "deleted":
                        for directory in ("uploads", "quarantine"):
                            await self.store.delete(directory, row["document_id"])
                    await self.db.execute("DELETE FROM document_cleanup WHERE document_id=? AND version=?",
                                          (row["document_id"], row["version"]))
                except Exception:
                    # Tombstone remains authoritative; reconciliation retries the cleanup.
                    continue

    async def reindex(self, doc_id: str, user: str):
        async with self.db.guard("documents:" + user):
            return await self._reindex(doc_id, user)

    async def _reindex(self, doc_id: str, user: str):
        document = await self.db.one("SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, user))
        if not document or document["status"] == "deleted":
            raise LookupError("资料不存在")
        if document["status"] not in {"ready", "failed"}:
            raise ServiceError("资料当前不可重建索引；隔离或扫描失败的文件不能进入资料库")
        if not await self.store.exists("uploads", doc_id):
            raise ServiceError("原始上传文件不存在，无法重建索引")
        await self.db.execute("INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version) VALUES(?,?,?)",
                              (doc_id, user, document["index_version"]))
        await self.db.execute("UPDATE documents SET status='indexing',error='',index_version=index_version+1 WHERE id=?", (doc_id,))
        return await self.db.one("SELECT * FROM documents WHERE id=?", (doc_id,))

    async def close(self):
        return None
