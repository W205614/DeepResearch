from ..core.consistency import affected
import asyncio
import hashlib
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from rank_bm25 import BM25Plus

from ..core.clamav import ScanRejected, ScanUnavailable, scan
from ..core.db import Database, now, uid
from ..core.metrics import RAG_RETRIEVAL_SECONDS
from ..infrastructure.providers import ServiceError
from ..infrastructure.object_store import ObjectStore
from .document_parsing import (IMAGE_SUFFIXES as IMAGE_SUFFIXES, IMAGE_MEDIA_TYPES as IMAGE_MEDIA_TYPES,
                               validate_upload as validate_upload, semantic_chunks as semantic_chunks,
                               split_document as split_document, extract_blocks, tokens)
from .retrieval_contract import RetrievalResults, valid_chunk, normalize_hits, score




class Documents:
    def __init__(self, settings, db: Database, providers, vectors):
        self.settings, self.db, self.providers, self.vectors = settings, db, providers, vectors
        self.gate = asyncio.Semaphore(1)
        self.lexical_gate = asyncio.Semaphore(2)
        self.lexical_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rag-lexical")
        self.store = ObjectStore(settings)
        self._corpora: dict[str, tuple[str, list[dict], dict[str, dict], BM25Plus]] = {}

    async def start(self) -> None:
        await self.store.start()

    async def lexical(self, func, *args):
        # A cancelled caller cannot release its slot while native work still runs.
        await self.lexical_gate.acquire()
        try:
            future = asyncio.get_running_loop().run_in_executor(self.lexical_pool, func, *args)
        except BaseException:
            self.lexical_gate.release()
            raise
        future.add_done_callback(lambda _: self.lexical_gate.release())
        return await asyncio.shield(future)

    async def check_corpus(self, user, expected):
        rows = await self.db.rows("SELECT id,hash,index_version FROM documents WHERE user_id=? AND status IN ('ready','rebuilding') ORDER BY id", (user,))
        current = hashlib.sha256(("rag-v2|" + "|".join(f"{r['id']}:{r['hash']}:{r['index_version']}" for r in rows)).encode()).hexdigest()
        if current != expected:
            raise ServiceError("检索期间资料发生变化，请重新检索", code="source_changed")

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
        blocks = await asyncio.to_thread(extract_blocks, name, content)
        assets = [block for block in blocks if block.image]
        if len(assets) > 20 or sum(len(block.image) for block in assets) > 20 * 1024 * 1024:
            raise ServiceError("资料图片超过 20 张或 20 MB，请拆分文件")
        chunks = []
        for block in blocks:
            text = block.text
            if block.image:
                suffix = next(key for key, value in IMAGE_MEDIA_TYPES.items() if value == block.media_type)
                await asyncio.to_thread(validate_upload, "image" + suffix, block.image)
                try:
                    extracted = await self.providers.describe_image(block.image, block.media_type)
                except ServiceError as exc:
                    raise ServiceError(f"{block.locator}：{exc}") from None
                if not isinstance(extracted, str) or not extracted.strip():
                    raise ServiceError(f"{block.locator}：视觉解析为空，请上传清晰页面")
                text = "图片解析（图中显示，未经外部核实）：" + extracted
            chunks.extend(semantic_chunks(block.locator, text))
            if len(chunks) > 400:
                raise ServiceError("资料超过 400 个片段限制，请拆分文件")
        if not chunks:
            raise ServiceError("未提取到可检索文字或图片内容")
        return chunks

    async def ingest(self, doc_id: str):
        document = await self.db.one("SELECT * FROM documents WHERE id=? AND status IN ('indexing','rebuilding')", (doc_id,))
        if not document:
            return
        target_version = document["pending_version"] or document["index_version"]
        async with self.gate:
            try:
                async with self.db.guard("documents:" + document["user_id"]):
                    budget_key = f"index-attempt:{doc_id}:{target_version}"
                    budget = await self.db.one("SELECT value FROM metadata WHERE key=?", (budget_key,))
                    attempts = int(budget["value"]) if budget else 0
                    if attempts >= self.settings.max_job_retries + 1:
                        raise ServiceError("资料索引恢复次数已耗尽，请检查文件并显式重建", code="index_budget_exhausted", retryable=False)
                    await self.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)", (budget_key, str(attempts + 1)))
                if not self.settings.demo_mode and not self.settings.allow_internal_model_processing:
                    raise ServiceError("资料外发解析与嵌入尚未获准", code="egress_denied", retryable=False)
                content = await self.store.get("uploads", doc_id)
                if hashlib.sha256(content).hexdigest() != document["hash"]:
                    raise ServiceError("原文内容已变化，请重新上传", code="source_changed", retryable=False)
                chunks = await self.split_for_index(document["name"], content)
                vectors = await self.providers.embed([part["text"] for part in chunks])
                if len(vectors) != len(chunks):
                    raise ServiceError("嵌入结果数量与片段不一致，资料未发布，请重试导入")
                rows = [dict(part, ordinal=i, id=hashlib.sha256(f"{doc_id}:{target_version}:{i}".encode()).hexdigest()[:32],
                    user_id=document["user_id"], document_id=doc_id, index_version=target_version, title=document["name"], vector=vector)
                    for i, (part, vector) in enumerate(zip(chunks, vectors))]
                async with self.db.guard("documents:" + document["user_id"]):
                    current = await self.db.one("SELECT status,index_version,pending_version FROM documents WHERE id=?", (doc_id,))
                    if not current or current["status"] != document["status"] or (current["pending_version"] or current["index_version"]) != target_version:
                        return
                    await self.db.execute("INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version) VALUES(?,?,?)",
                                          (doc_id, document["user_id"], target_version))
                    await self.vectors.upsert("documents", rows)
                    async with self.db.connection() as conn:
                        result = await conn.execute("""UPDATE documents SET status='ready',error='',index_version=?,pending_version=0
                            WHERE id=? AND status=? AND index_version=?""", (target_version, doc_id, document["status"], document["index_version"]))
                        if affected(result):
                            await conn.execute("DELETE FROM document_cleanup WHERE document_id=? AND version=?",
                                               (doc_id, target_version))
                            if document["status"] == "rebuilding":
                                await conn.execute("INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version) VALUES(?,?,?)",
                                                   (doc_id, document["user_id"], document["index_version"]))
                            await conn.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
                            await conn.executemany("INSERT INTO chunks(id,document_id,user_id,text,locator,vector,ordinal) VALUES(?,?,?,?,?,?,?)",
                                [(row["id"], doc_id, document["user_id"], row["text"], row["locator"],
                                  json.dumps(row["vector"]) if self.settings.demo_mode else "[]", row["ordinal"]) for row in rows])
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
                retain = document["status"] == "rebuilding" and not (isinstance(exc, ServiceError) and exc.code == "source_changed")
                await self.db.execute("UPDATE documents SET status=?,pending_version=0,error=? WHERE id=? AND status=? AND index_version=?",
                                      ("ready" if retain else "failed", message, doc_id, document["status"], document["index_version"]))
                raise

    async def search(self, user: str, queries: list[str], limit: int = 6, run_id: str = "") -> list[dict]:
        started = time.monotonic()
        if not isinstance(queries, list) or any(not isinstance(q, str) or len(q) > 4000 for q in queries):
            raise ServiceError("检索查询字段无效，请提供不超过 4000 字的文本")
        queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))
        if len(queries) > 8 or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ServiceError("单次检索支持最多 8 个查询、1–20 个返回片段，请拆分问题")
        if not queries:
            return RetrievalResults()
        reasons = set()
        documents = await self.db.rows("SELECT id,hash,index_version FROM documents WHERE user_id=? AND status IN ('ready','rebuilding') ORDER BY id", (user,))
        # Invalidate old cache entries when extraction/ranking contracts change.
        corpus_hash = hashlib.sha256(("rag-v2|" + "|".join(f"{r['id']}:{r['hash']}:{r['index_version']}" for r in documents)).encode()).hexdigest()
        query_hash = hashlib.sha256(json.dumps(queries, ensure_ascii=False).encode()).hexdigest()
        corpus_started = time.monotonic()
        cached_corpus = self._corpora.get(user)
        corpus_cache_hit = bool(cached_corpus and cached_corpus[0] == corpus_hash)
        if corpus_cache_hit:
            _, all_chunks, chunks_by_id, bm25 = cached_corpus
        else:
            raw = await self.db.rows("""SELECT c.*,d.name AS title,d.index_version,d.hash AS document_hash FROM chunks c JOIN documents d ON c.document_id=d.id
                WHERE c.user_id=? AND d.user_id=? AND d.status IN ('ready','rebuilding') LIMIT ?""", (user, user, self.settings.rag_max_corpus_chunks + 1))
            if len(raw) > self.settings.rag_max_corpus_chunks:
                raise ServiceError("工作空间资料超过安全检索容量，请归档资料或联系管理员", code="corpus_capacity", retryable=False)
            all_chunks = [row for row in raw if valid_chunk(row, user)]
            if len(all_chunks) != len(raw):
                reasons.add("invalid_records")
            if raw and not all_chunks:
                raise ServiceError("资料索引字段无效，请重新索引资料")
            chunks_by_id = {row["id"]: row for row in all_chunks}
            build_started = time.monotonic()
            bm25 = await self.lexical(lambda: BM25Plus([tokens(row["text"]) or ["_"] for row in all_chunks], delta=0)) if all_chunks else None
            RAG_RETRIEVAL_SECONDS.labels(stage="bm25_build").observe(time.monotonic() - build_started)
            if not reasons:
                while len(self._corpora) >= self.settings.rag_corpus_cache_users:
                    self._corpora.pop(next(iter(self._corpora)))
                self._corpora[user] = (corpus_hash, all_chunks, chunks_by_id, bm25)
        corpus_ms = round((time.monotonic() - corpus_started) * 1000, 2)
        RAG_RETRIEVAL_SECONDS.labels(stage="corpus_load").observe(corpus_ms / 1000)
        if not all_chunks:
            return RetrievalResults()
        cache_started = time.monotonic()
        cache_key = (user, corpus_hash, query_hash, limit)
        cached = await self.db.one("""SELECT result FROM document_search_cache
            WHERE user_id=? AND corpus_hash=? AND query_hash=? AND limit_value=?""", cache_key)
        cache_ms = round((time.monotonic() - cache_started) * 1000, 2)
        RAG_RETRIEVAL_SECONDS.labels(stage="cache_lookup").observe(cache_ms / 1000)
        if cached and not reasons:
            try:
                rows = json.loads(cached["result"])
                if not isinstance(rows, list) or len(rows) > limit:
                    raise ValueError("cache contract")
                result, seen = [], set()
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                        raise ValueError("cache id")
                    source = chunks_by_id.get(row["id"])
                    if not source or row["id"] in seen:
                        raise ValueError("cache provenance")
                    seen.add(row["id"])
                    scores = {key: score(row[key]) for key in ("score", "vector_score", "bm25_score")}
                    result.append({**source, **scores})
                await self.record_retrieval(run_id, {"cache_hit": True, "queries": len(queries),
                    "corpus_documents": len(documents), "corpus_chunks": len(all_chunks), "cache_ms": cache_ms,
                    "total_ms": round((time.monotonic() - started) * 1000, 2)})
                RAG_RETRIEVAL_SECONDS.labels(stage="total").observe(time.monotonic() - started)
                await self.check_corpus(user, corpus_hash)
                return RetrievalResults(result)
            except (TypeError, ValueError, KeyError):
                reasons.add("invalid_records")
                await self.db.execute("DELETE FROM document_search_cache WHERE user_id=? AND corpus_hash=? AND query_hash=? AND limit_value=?", cache_key)

        async def retrieve_query(query):
            lexical_started = time.monotonic()
            lexical = await self.lexical(bm25.get_scores, tokens(query))
            RAG_RETRIEVAL_SECONDS.labels(stage="bm25_score").observe(time.monotonic() - lexical_started)
            hits, failures = {}, set()
            embed_started = time.monotonic()
            embed_ms, vector_ms = 0.0, 0.0
            try:
                if not self.settings.demo_mode and not self.settings.allow_internal_model_processing:
                    raise ServiceError("内部查询未获准外发，使用关键词检索", code="embedding_policy_blocked", retryable=False)
                async with asyncio.timeout(self.settings.rag_vector_timeout_seconds):
                    embeddings = await self.providers.embed([query])
                embed_ms = (time.monotonic() - embed_started) * 1000
                if len(embeddings) != 1:
                    raise ServiceError("查询嵌入结果格式无效")
                vector_started = time.monotonic()
                try:
                    remaining = self.settings.rag_vector_timeout_seconds - (time.monotonic() - embed_started)
                    async with asyncio.timeout(max(0, remaining)):
                        raw_hits = await self.vectors.search("documents", user, embeddings[0], limit=limit * 3)
                    hits, rejected = normalize_hits(raw_hits, chunks_by_id, limit * 3)
                    if rejected:
                        failures.add("invalid_records")
                finally:
                    vector_ms = (time.monotonic() - vector_started) * 1000
            except (ServiceError, TypeError, ValueError, KeyError, IndexError, TimeoutError) as exc:
                if isinstance(exc, ServiceError) and exc.code in {"execution_lost", "permission_revoked", "budget_exhausted", "egress_denied"}:
                    raise
                failures.add("policy_blocked" if isinstance(exc, ServiceError) and exc.code == "embedding_policy_blocked" else "vector_unavailable")
            RAG_RETRIEVAL_SECONDS.labels(stage="query_embedding").observe(embed_ms / 1000)
            RAG_RETRIEVAL_SECONDS.labels(stage="vector_search").observe(vector_ms / 1000)
            return lexical, hits, failures, embed_ms, vector_ms

        results = await asyncio.gather(*(retrieve_query(query) for query in queries))
        scores, embed_ms, vector_ms = {}, 0.0, 0.0
        for lexical, hits, failures, embed_time, vector_time in results:
            reasons.update(failures)
            embed_ms += embed_time
            vector_ms += vector_time
            maximum = max(lexical) or 1
            for row, value in zip(all_chunks, lexical):
                if value > 0:
                    entry = scores.setdefault(row["id"], {"row": row, "lexical": 0.0, "vector": 0.0})
                    entry["lexical"] = max(entry["lexical"], float(value / maximum))
            for key, value in hits.items():
                if value >= self.settings.rag_min_vector_score:
                    entry = scores.setdefault(key, {"row": chunks_by_id[key], "lexical": 0.0, "vector": 0.0})
                    entry["vector"] = max(entry["vector"], value)
        fusion_started = time.monotonic()
        ranked = sorted(scores.values(), key=lambda item: 0.65 * item["vector"] + 0.35 * item["lexical"], reverse=True)
        selected, terms_seen = [], []
        for item in ranked:
            row, terms = item["row"], set(tokens(item["row"]["text"]))
            similarity = max((len(terms & old) / max(1, len(terms | old)) for old in terms_seen), default=0)
            if similarity >= 0.82:
                continue
            selected.append(dict(row, score=round(0.65 * item["vector"] + 0.35 * item["lexical"], 4),
                                 vector_score=round(item["vector"], 4), bm25_score=round(item["lexical"], 4)))
            terms_seen.append(terms)
            if len(selected) >= limit:
                break
        if not selected and any(failures & {"vector_unavailable", "invalid_records"} for _, _, failures, _, _ in results):
            raise ServiceError("资料检索发生故障或返回无效字段，关键词检索也未找到证据，请重试或重建索引")
        RAG_RETRIEVAL_SECONDS.labels(stage="fusion").observe(time.monotonic() - fusion_started)
        RAG_RETRIEVAL_SECONDS.labels(stage="total").observe(time.monotonic() - started)
        # Never cache a degraded response: a repaired service must be retried on the next request.
        if not reasons:
            await self.db.execute("""INSERT OR REPLACE INTO document_search_cache
                (user_id,corpus_hash,query_hash,limit_value,result,created_at) VALUES(?,?,?,?,?,?)""",
                (*cache_key, json.dumps([{k: row[k] for k in ("id", "score", "vector_score", "bm25_score")} for row in selected]), now()))
        await self.record_retrieval(run_id, {"cache_hit": False, "corpus_cache_hit": corpus_cache_hit,
            "queries": len(queries), "corpus_documents": len(documents), "corpus_chunks": len(all_chunks),
            "cache_ms": cache_ms, "corpus_ms": corpus_ms, "embedding_ms": round(embed_ms, 2),
            "vector_ms": round(vector_ms, 2), "total_ms": round((time.monotonic() - started) * 1000, 2),
            "candidates": len(ranked), "selected": len(selected), "reasons": sorted(reasons)})
        await self.check_corpus(user, corpus_hash)
        return RetrievalResults(selected, reasons=reasons)

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
                if document and document["status"] in {"ready", "indexing", "rebuilding"} and document["index_version"] == row["version"]:
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
        await self.db.execute("DELETE FROM metadata WHERE key=?", (f"index-attempt:{doc_id}:{document['index_version'] + 1}",))
        if document["status"] == "ready":
            await self.db.execute("UPDATE documents SET status='rebuilding',error='',pending_version=index_version+1 WHERE id=?", (doc_id,))
        else:
            await self.db.execute("UPDATE documents SET status='indexing',error='',index_version=index_version+1 WHERE id=?", (doc_id,))
        return await self.db.one("SELECT * FROM documents WHERE id=?", (doc_id,))

    async def close(self):
        self.lexical_pool.shutdown(wait=False, cancel_futures=True)
