from ..core.consistency import affected
import asyncio
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from rank_bm25 import BM25Plus

from ..core.clamav import ScanRejected, ScanUnavailable, scan
from ..core.db import Database, now, uid
from ..core.metrics import RAG_RETRIEVAL_SECONDS, RAG_VECTOR_CANDIDATES, RAG_VECTOR_REFILLS
from ..infrastructure.providers import ServiceError
from ..infrastructure.object_store import ObjectStore
from .document_parsing import (IMAGE_SUFFIXES as IMAGE_SUFFIXES, IMAGE_MEDIA_TYPES as IMAGE_MEDIA_TYPES,
                               validate_upload as validate_upload, semantic_chunks as semantic_chunks,
                               split_document as split_document, tokens)
from .retrieval_contract import RetrievalResults, valid_chunk, normalize_hits, score
from .evidence_lifecycle import EvidenceLifecycle
from .document_access import access_sql, access_args, register_owner, require_document
from .lexical_retrieval import LexicalRetrieval, CONTRACT as LEXICAL_CONTRACT




class Documents:
    def __init__(self, settings, db: Database, providers, vectors, reranker=None):
        self.settings, self.db, self.providers, self.vectors, self.reranker = settings, db, providers, vectors, reranker
        self.lifecycle = EvidenceLifecycle(db)
        self.gate = asyncio.Semaphore(1)
        self.lexical_gate = asyncio.Semaphore(2)
        self.lexical_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rag-lexical")
        self.store = ObjectStore(settings)
        self._corpora: dict[str, tuple[str, list[dict], dict[str, dict], BM25Plus]] = {}
        self.lexical_index = LexicalRetrieval(self)

    async def start(self) -> None:
        await self.store.start()
        await self.lifecycle.archive_current_versions()

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
        rows = await self.db.rows(f"SELECT d.id,d.hash,d.index_version FROM documents d WHERE d.user_id=? AND d.status IN ('ready','rebuilding') AND {access_sql()} ORDER BY d.id", (user, *access_args(user)))
        current = hashlib.sha256(("rag-v4|" + "|".join(f"{r['id']}:{r['hash']}:{r['index_version']}" for r in rows)).encode()).hexdigest()
        if current != expected:
            raise ServiceError("检索期间资料发生变化，请重新检索", code="source_changed")

    @staticmethod
    async def _storage_mutation(operation):
        # Cancelling asyncio.to_thread does not stop its underlying S3/filesystem
        # mutation. Keep the document lock until it finishes, so a late write or
        # delete cannot race the next replacement or lose its cleanup intent.
        task=asyncio.create_task(operation)
        cancelled=None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                cancelled=exc
        result=task.result()
        if cancelled is not None:
            raise cancelled
        return result

    async def add(self, user: str, name: str, content: bytes):
        try:
            async with self.db.guard("documents:" + user):
                result = await self._add(user, name, content)
                await register_owner(self.db, user, result['id'])
                return result
        finally:
            await self.cleanup_pending()

    async def _add(self, user: str, name: str, content: bytes):
        name = Path(name.replace("\\", "/")).name[:160]
        if len(content) > 10 * 1024 * 1024:
            raise ServiceError("文件不能超过 10 MB")
        validate_upload(name, content, decode_images=False)
        digest = hashlib.sha256(content).hexdigest()
        existing = await self.db.one("""SELECT * FROM documents WHERE user_id=? AND status!='deleted'
            AND (hash=? OR pending_hash=?)""", (user, digest, digest))
        if existing and (existing["pending_hash"] == digest
                         or existing["status"] in {"ready", "rebuilding", "indexing", "scanning"}):
            from ..core.usage_governance import paid_identity
            if paid_identity():
                await require_document(self.db,user,existing['id'])
            return existing
        if not existing:
            existing = await self.db.one("SELECT * FROM documents WHERE user_id=? AND hash=? AND status='deleted'",
                                         (user, digest))
        if existing:
            from ..core.usage_governance import paid_identity
            if paid_identity():
                await require_document(self.db,user,existing['id'],write=True)
        if not existing or existing["status"] == "deleted":
            capacity = await self.capacity(user)
            if capacity["documents"] >= capacity["document_limit"]:
                raise ServiceError(f"工作空间资料数已达上限 {capacity['documents']}/{capacity['document_limit']}，请先归档或清理资料",
                                   code="document_capacity", retryable=False)
            if capacity["chunks"] >= capacity["chunk_limit"]:
                raise ServiceError(f"工作空间片段已达上限 {capacity['chunks']}/{capacity['chunk_limit']}，请先归档或清理资料",
                                   code="corpus_capacity", retryable=False)
        doc_id = existing["id"] if existing else uid()
        version = int(existing["index_version"]) + 1 if existing else 1
        object_key = f"{doc_id}.v{version}"
        timestamp = now()
        if existing:
            await self.db.execute("""INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version,object_key)
                VALUES(?,?,?,?)""", (doc_id, user, existing["index_version"], existing["object_key"] or doc_id))
            await self.db.execute("""UPDATE documents SET name=?,hash=?,status='scanning',error='',index_version=?,
                pending_version=0,pending_name='',pending_hash='',pending_object_key='',object_key=?,updated_at=? WHERE id=?""",
                (name, digest, version, object_key, timestamp, doc_id))
        else:
            await self.db.execute("""INSERT INTO documents(id,user_id,name,hash,status,created_at,updated_at,index_version,object_key)
                VALUES(?,?,?,?,?,?,?,?,?)""", (doc_id, user, name, digest, "scanning", timestamp, timestamp, version, object_key))
        # Record the uploader before storage/scanning can fail or be interrupted;
        # a recovered indexing job must charge the original subject.
        await register_owner(self.db, user, doc_id)
        await self.db.execute("""INSERT INTO document_scan_jobs(document_id,user_id,object_key,state,attempts,error_code,updated_at)
            VALUES(?,?,?,'pending',0,'',?) ON CONFLICT(document_id) DO UPDATE SET
            object_key=excluded.object_key,state='pending',attempts=0,error_code='',updated_at=excluded.updated_at""",
            (doc_id, user, object_key, timestamp))
        try:
            await self._storage_mutation(self.store.put("quarantine", object_key, content))
        except Exception as exc:
            await self.db.execute("UPDATE documents SET status='scan_failed',error=?,updated_at=? WHERE id=?",
                                  ("原文存储未完成，请重传文件；已保留可恢复记录", now(), doc_id))
            await self.db.execute("UPDATE document_scan_jobs SET error_code='upload_storage_failed',updated_at=? WHERE document_id=?", (now(), doc_id))
            exc.document_id = doc_id
            raise
        await self._finish_scan(doc_id)
        return await self.db.one("SELECT * FROM documents WHERE id=?", (doc_id,))

    async def _finish_scan(self, doc_id, *, recovery=False):
        """Caller holds the workspace document lock; every recovery rescans bytes."""
        job = await self.db.one("SELECT * FROM document_scan_jobs WHERE document_id=?", (doc_id,))
        document = await self.db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
        if not job or job['state']!='pending' or not document or document["status"] not in {"scanning", "scan_failed"}:
            return
        if recovery and job['attempts']:
            # Persisted cooldown is rechecked under the workspace lock. Several
            # runtime recovery loops may have selected the same pending row before
            # locking; only the first due retry can spend an attempt.
            delay=min(300,20*(2**min(job['attempts']-1,10)))
            try:
                stamp=datetime.fromisoformat(job['updated_at']).astimezone(timezone.utc).timestamp()
            except (TypeError,ValueError):
                stamp=0
            if time.time()-stamp<delay:
                return
        if job["attempts"] >= self.settings.max_job_retries + 1:
            await self.db.execute("UPDATE document_scan_jobs SET state='failed',error_code='scan_budget_exhausted',updated_at=? WHERE document_id=?", (now(), doc_id))
            await self.db.execute("UPDATE documents SET status='scan_failed',error=?,updated_at=? WHERE id=?", ("扫描恢复次数已耗尽，请检查服务后重传文件", now(), doc_id))
            return
        await self.db.execute("UPDATE document_scan_jobs SET attempts=attempts+1,updated_at=? WHERE document_id=?", (now(), doc_id))
        try:
            await self.store.ping()
            area = "quarantine" if await self.store.exists("quarantine", job["object_key"]) else "uploads"
            if not await self.store.exists(area, job["object_key"]):
                await self.db.execute("UPDATE document_scan_jobs SET state='failed',error_code='upload_missing',updated_at=? WHERE document_id=?", (now(), doc_id))
                await self.db.execute("UPDATE documents SET status='scan_failed',error=?,updated_at=? WHERE id=?", ("原文上传未完成，请重新上传相同文件", now(), doc_id))
                return
            content = await self.store.get(area, job["object_key"])
            if hashlib.sha256(content).hexdigest() != document["hash"]:
                raise ScanRejected("隔离原文校验失败，请重新上传")
            await scan(self.settings, content)
            if area == "quarantine":
                await self._storage_mutation(self.store.move("quarantine", "uploads", job["object_key"]))
            async with self.db.connection() as conn:
                await conn.execute("UPDATE documents SET status='indexing',error='',updated_at=? WHERE id=? AND status IN ('scanning','scan_failed')", (now(), doc_id))
                await conn.execute("UPDATE document_scan_jobs SET state='completed',error_code='',updated_at=? WHERE document_id=?", (now(), doc_id))
                await conn.commit()
            await self.invalidate_search_cache(document["user_id"])
        except Exception as exc:
            rejected = isinstance(exc, ScanRejected)
            code = "scan_rejected" if rejected else "scan_unavailable" if isinstance(exc, ScanUnavailable) else "scan_storage_failed"
            message = str(exc) if isinstance(exc, (ScanRejected, ScanUnavailable)) else "扫描或存储暂不可用，原件保持隔离并等待有界恢复"
            await self.db.execute("UPDATE documents SET status=?,error=?,updated_at=? WHERE id=?",
                                  ("quarantined" if rejected else "scan_failed", message, now(), doc_id))
            await self.db.execute("UPDATE document_scan_jobs SET state=?,error_code=?,updated_at=? WHERE document_id=?",
                                  ("failed" if rejected else "pending", code, now(), doc_id))
            exc.document_id = doc_id
            raise

    async def recover_scans(self):
        # Upgrade compatibility for an upload interrupted before the durable job
        # record was added. No bytes are considered clean based on old state.
        await self.db.execute("""INSERT OR IGNORE INTO document_scan_jobs(document_id,user_id,object_key,state,updated_at)
            SELECT id,user_id,CASE WHEN object_key='' THEN id ELSE object_key END,'pending',updated_at
            FROM documents WHERE status='scanning'""")
        batch = getattr(self.settings, "retention_cleanup_batch_size", 100)
        rows = await self.db.rows("""SELECT j.document_id,j.user_id FROM document_scan_jobs j
            JOIN documents d ON d.id=j.document_id WHERE j.state='pending' AND d.status IN ('scanning','scan_failed')
            ORDER BY j.updated_at,j.document_id LIMIT ?""", (batch,))
        for row in rows:
            try:
                async with self.db.guard("documents:" + row["user_id"]):
                    await self._finish_scan(row["document_id"],recovery=True)
            except Exception:
                continue  # Persisted state and bounded attempts are authoritative.

    async def capacity(self, user):
        documents = await self.db.one("SELECT COUNT(*) AS total FROM documents WHERE user_id=? AND status!='deleted'", (user,))
        chunks = await self.db.one("SELECT COUNT(*) AS total FROM chunks WHERE user_id=?", (user,))
        return {"documents": documents['total'], "chunks": chunks['total'],
                "document_limit": getattr(self.settings, 'workspace_document_limit', 500),
                "chunk_limit": getattr(self.settings, 'workspace_chunk_limit', 50000),
                "max_file_bytes": 10 * 1024 * 1024, "max_chunks_per_document": 400,
                "large_corpus_retrieval": "bounded_database_candidates"}

    async def replace(self, doc_id: str, user: str, name: str, content: bytes):
        name = Path(name.replace("\\", "/")).name[:160]
        if len(content) > 10 * 1024 * 1024:
            raise ServiceError("文件不能超过 10 MB")
        validate_upload(name, content, decode_images=False)
        digest = hashlib.sha256(content).hexdigest()
        failure = None
        # Keep the document/user lock through staging and scanning. Otherwise two
        # concurrent replacements can choose the same versioned object key and
        # the losing request may delete the winner's staged object.
        async with self.db.guard("documents:" + user):
            document = await self.db.one("SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, user))
            if not document or document["status"] == "deleted":
                raise LookupError("资料不存在")
            if document["status"] != "ready":
                raise ServiceError("资料正在处理，完成后才能替换", code="document_busy", retryable=False)
            if digest == document["hash"]:
                raise ServiceError("文件内容未变化；如需应用新解析规则，请使用重建索引", code="document_unchanged", retryable=False)
            duplicate = await self.db.one("""SELECT id FROM documents WHERE user_id=? AND id!=? AND status!='deleted'
                AND (hash=? OR pending_hash=?)""", (user, doc_id, digest, digest))
            if duplicate:
                raise ServiceError("相同内容已存在于资料库", code="document_duplicate", retryable=False)
            target_version = int(document["index_version"]) + 1
            object_key = f"{doc_id}.v{target_version}"
            await self.db.execute("""INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version,object_key)
                VALUES(?,?,?,?)""",(doc_id,user,target_version,object_key))
            try:
                await self._storage_mutation(self.store.put("quarantine", object_key, content))
                await scan(self.settings, content)
                await self._storage_mutation(self.store.move("quarantine", "uploads", object_key))
                await self.db.execute("""UPDATE documents SET status='rebuilding',error='',pending_version=?,
                    pending_name=?,pending_hash=?,pending_object_key=?,updated_at=? WHERE id=? AND status='ready' AND index_version=?""",
                    (target_version, name, digest, object_key, now(), doc_id, document["index_version"]))
            except asyncio.CancelledError:
                await self.db.execute("UPDATE documents SET error=?,updated_at=? WHERE id=? AND status='ready' AND index_version=?",
                                      ("替换预处理已中断，旧版仍可用；请重传替换文件",now(),doc_id,document['index_version']))
                raise
            except Exception as exc:
                message = str(exc) if isinstance(exc, (ServiceError, ScanRejected, ScanUnavailable)) else "资料替换预处理失败，请稍后重试"
                await self.db.execute("""INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version,object_key)
                    VALUES(?,?,?,?)""", (doc_id, user, target_version, object_key))
                await self.db.execute("UPDATE documents SET status='ready',error=?,updated_at=? WHERE id=? AND status='ready' AND index_version=?",
                                      (message, now(), doc_id, document["index_version"]))
                failure = exc
        if failure:
            await self.cleanup_pending()
            raise failure
        return await self.db.one("SELECT * FROM documents WHERE id=?", (doc_id,))

    async def invalidate_search_cache(self, user: str) -> None:
        self._corpora.pop(user, None)
        await self.db.execute("DELETE FROM document_search_cache WHERE user_id=?", (user,))

    async def record_retrieval(self, run_id: str, data: dict) -> None:
        if run_id:
            await self.db.event(run_id, "local_retrieval", data)

    async def split_for_index(self, name: str, content: bytes) -> list[dict]:
        from .parser_process import parse_document
        blocks = await parse_document(name, content, self.settings)
        assets = [block for block in blocks if block.image]
        if len(assets) > 20 or sum(len(block.image) for block in assets) > 20 * 1024 * 1024:
            raise ServiceError("资料图片超过 20 张或 20 MB，请拆分文件")
        chunks = []
        for block in blocks:
            text = block.text
            if block.image:
                # Images were decoded/verified or rendered inside the bounded parser process.
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
        document = await self.db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
        if not document:
            return
        policy = await self.db.one("SELECT owner_subject FROM document_policies WHERE document_id=?", (doc_id,))
        workspace = await self.db.one("SELECT created_by FROM workspaces WHERE id=?", (document["user_id"],))
        subject = (policy["owner_subject"] if policy else None) or (workspace["created_by"] if workspace else document["user_id"])
        from ..core.usage_governance import paid_scope
        with paid_scope(document["user_id"], subject, "indexing"):
            return await self._ingest(doc_id)

    async def _ingest(self, doc_id: str):
        document = await self.db.one("SELECT * FROM documents WHERE id=? AND status IN ('indexing','rebuilding')", (doc_id,))
        if not document:
            return
        target_version = document["pending_version"] or document["index_version"]
        rebuilding = document["status"] == "rebuilding"
        target_name = document["pending_name"] if rebuilding else document["name"]
        target_hash = document["pending_hash"] if rebuilding else document["hash"]
        target_object_key = (document["pending_object_key"] if rebuilding else document["object_key"]) or doc_id
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
                content = await self.store.get("uploads", target_object_key)
                if hashlib.sha256(content).hexdigest() != target_hash:
                    raise ServiceError("原文内容已变化，请重新上传", code="source_changed", retryable=False)
                chunks = await self.split_for_index(target_name, content)
                vectors = await self.providers.embed([part["text"] for part in chunks])
                if len(vectors) != len(chunks):
                    raise ServiceError("嵌入结果数量与片段不一致，资料未发布，请重试导入")
                rows = [dict(part, ordinal=i, id=hashlib.sha256(f"{doc_id}:{target_version}:{i}".encode()).hexdigest()[:32],
                    user_id=document["user_id"], document_id=doc_id, index_version=target_version, title=target_name, vector=vector)
                    for i, (part, vector) in enumerate(zip(chunks, vectors))]
                async with self.db.guard("documents:" + document["user_id"]):
                    current = await self.db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
                    if (not current or current["status"] != document["status"]
                            or (current["pending_version"] or current["index_version"]) != target_version
                            or (rebuilding and current["pending_hash"] != target_hash)):
                        return
                    count = await self.db.one("SELECT COUNT(*) AS total FROM chunks WHERE user_id=? AND document_id!=?", (document["user_id"], doc_id))
                    chunk_limit = getattr(self.settings, 'workspace_chunk_limit', 50000)
                    if count['total'] + len(rows) > chunk_limit:
                        raise ServiceError(f"索引将超过工作空间片段上限 {chunk_limit}，旧可用版本保持不变，请拆分或清理资料",
                                           code='corpus_capacity', retryable=False)
                    await self.db.execute("""INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version,object_key)
                        VALUES(?,?,?,?)""", (doc_id, document["user_id"], target_version, target_object_key))
                    await self.vectors.upsert("documents", rows)
                    async with self.db.connection() as conn:
                        if rebuilding:
                            result = await conn.execute("""UPDATE documents SET status='ready',error='',index_version=?,pending_version=0,
                                name=?,hash=?,object_key=?,pending_name='',pending_hash='',pending_object_key='',updated_at=?
                                WHERE id=? AND status='rebuilding' AND index_version=? AND pending_version=? AND pending_hash=?""",
                                (target_version, target_name, target_hash, target_object_key, now(), doc_id,
                                 document["index_version"], target_version, target_hash))
                        else:
                            result = await conn.execute("""UPDATE documents SET status='ready',error='',index_version=?,pending_version=0,
                                updated_at=? WHERE id=? AND status='indexing' AND index_version=?""",
                                (target_version, now(), doc_id, document["index_version"]))
                        if affected(result):
                            # Archive old current text before replacing the mutable retrieval projection.
                            old_chunks = await self.db.rows("SELECT id,text,locator,ordinal FROM chunks WHERE document_id=? ORDER BY ordinal", (doc_id,))
                            if rebuilding:
                                await conn.execute("""INSERT OR IGNORE INTO document_versions(document_id,user_id,version,hash,name,object_key,status,blocks_json,chunks_json,created_at)
                                    VALUES(?,?,?,?,?,?,'ready',?,?,?)""", (doc_id, document["user_id"], document["index_version"], document["hash"], document["name"], document["object_key"] or doc_id,
                                    json.dumps([{k: r[k] for k in ("locator","text")} for r in old_chunks], ensure_ascii=False), json.dumps(old_chunks, ensure_ascii=False), now()))
                            snapshots = [{k: row[k] for k in ("id","text","locator","ordinal")} for row in rows]
                            await conn.execute("""INSERT OR IGNORE INTO document_versions(document_id,user_id,version,hash,name,object_key,status,blocks_json,chunks_json,parser_config,index_config,created_at)
                                VALUES(?,?,?,?,?,?,'ready',?,?,?,?,?)""", (doc_id, document["user_id"], target_version, target_hash, target_name, target_object_key,
                                json.dumps(chunks, ensure_ascii=False), json.dumps(snapshots, ensure_ascii=False),
                                json.dumps({"contract":"structured-v1","visual":"on_demand"}), json.dumps({"embedding_model":self.settings.embedding_model,"dimension":self.settings.embedding_dimension}), now()))
                            await self.lifecycle.changed(conn, document["user_id"], doc_id, document["index_version"] if rebuilding else 0, target_version, "updated" if rebuilding else "added")
                            await conn.execute("DELETE FROM document_cleanup WHERE document_id=? AND version=?",
                                               (doc_id, target_version))
                            if rebuilding:
                                old_object_key = (document["object_key"] or doc_id) if (document["object_key"] or doc_id) != target_object_key else ""
                                await conn.execute("""INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version,object_key)
                                    VALUES(?,?,?,?)""", (doc_id, document["user_id"], document["index_version"], old_object_key))
                            await conn.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
                            await self.lexical_index.remove_document(conn, doc_id)
                            await conn.executemany("INSERT INTO chunks(id,document_id,user_id,text,locator,vector,ordinal) VALUES(?,?,?,?,?,?,?)",
                                [(row["id"], doc_id, document["user_id"], row["text"], row["locator"],
                                  json.dumps(row["vector"]) if self.settings.demo_mode else "[]", row["ordinal"]) for row in rows])
                            await self.lexical_index.write(conn, rows)
                        else:
                            await conn.execute("""INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version,object_key)
                                VALUES(?,?,?,?)""", (doc_id, document["user_id"], target_version, target_object_key))
                        await conn.commit()
                await self.cleanup_pending()
                await self.invalidate_search_cache(document["user_id"])
                await self.lifecycle.process_events()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc) if isinstance(exc, ServiceError) else "资料导入失败，请检查文件及服务配置"
                if rebuilding:
                    await self.db.execute("""INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version,object_key)
                        VALUES(?,?,?,?)""", (doc_id, document["user_id"], target_version, target_object_key))
                    await self.db.execute("""UPDATE documents SET status='ready',pending_version=0,pending_name='',pending_hash='',
                        pending_object_key='',error=?,updated_at=? WHERE id=? AND status='rebuilding' AND index_version=? AND pending_version=?""",
                        (message, now(), doc_id, document["index_version"], target_version))
                    await self.cleanup_pending()
                else:
                    await self.db.execute("UPDATE documents SET status='failed',pending_version=0,error=?,updated_at=? WHERE id=? AND status='indexing' AND index_version=?",
                                          (message, now(), doc_id, document["index_version"]))
                raise

    async def search(self, user: str, queries: list[str], limit: int = 6, run_id: str = "",
                     rerank_query: str = "") -> list[dict]:
        started = time.monotonic()
        if not isinstance(queries, list) or any(not isinstance(q, str) or len(q) > 4000 for q in queries):
            raise ServiceError("检索查询字段无效，请提供不超过 4000 字的文本")
        queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))
        if len(queries) > 8 or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ServiceError("单次检索支持最多 8 个查询、1–20 个返回片段，请拆分问题")
        if not queries:
            return RetrievalResults()
        reasons = set()
        documents = await self.db.rows(f"SELECT d.id,d.hash,d.index_version FROM documents d WHERE d.user_id=? AND d.status IN ('ready','rebuilding') AND {access_sql()} ORDER BY d.id", (user, *access_args(user)))
        # Invalidate old cache entries when extraction/ranking contracts change.
        corpus_hash = hashlib.sha256(("rag-v4|" + "|".join(f"{r['id']}:{r['hash']}:{r['index_version']}" for r in documents)).encode()).hexdigest()
        original_query = rerank_query.strip() or queries[0]
        ranking_contract = self.reranker.contract_key if self.reranker else "fusion-v1"
        query_hash = hashlib.sha256(json.dumps({"queries": queries, "rerank_query": original_query,
            "ranking": ranking_contract, "lexical": LEXICAL_CONTRACT}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        corpus_started = time.monotonic()
        count = await self.db.one(f"""SELECT COUNT(*) AS total FROM chunks c JOIN documents d ON d.id=c.document_id
            WHERE c.user_id=? AND d.user_id=? AND d.status IN ('ready','rebuilding') AND {access_sql()}""", (user, user, *access_args(user)))
        corpus_count = count["total"]
        indexed = corpus_count > min(getattr(self.settings, "rag_small_corpus_chunks", 1000), self.settings.rag_max_corpus_chunks)
        sql_lexical = {}
        cached_corpus = self._corpora.get(user) if not indexed else None
        corpus_cache_hit = bool(cached_corpus and cached_corpus[0] == corpus_hash)
        if indexed:
            await self.lexical_index.ensure(user)
            if (await self.lexical_index.stats(user))["total"] < corpus_count:
                reasons.add("invalid_records")
            candidate_limit = getattr(self.settings, "rag_db_candidate_limit", 128)
            sql_lexical = {query: await self.lexical_index.scores(user, query, candidate_limit) for query in queries}
            ids = {key for values in sql_lexical.values() for key in values}
            all_chunks = await self.lexical_index.hydrate(user, ids)
            chunks_by_id = {row["id"]: row for row in all_chunks}
            bm25 = None
        elif corpus_cache_hit:
            _, all_chunks, chunks_by_id, bm25 = cached_corpus
        else:
            raw = await self.db.rows(f"""SELECT c.*,d.name AS title,d.index_version,d.hash AS document_hash FROM chunks c JOIN documents d ON c.document_id=d.id
                WHERE c.user_id=? AND d.user_id=? AND d.status IN ('ready','rebuilding') AND {access_sql()} LIMIT ?""", (user, user, *access_args(user), self.settings.rag_max_corpus_chunks + 1))
            if len(raw) > self.settings.rag_max_corpus_chunks:
                raise ServiceError("工作空间资料超过安全检索容量，请归档资料或联系管理员", code="corpus_capacity", retryable=False)
            all_chunks = [{**row,"ranking_text": self.ranking_text(row)} for row in raw if valid_chunk(row, user)]
            if len(all_chunks) != len(raw):
                reasons.add("invalid_records")
            if raw and not all_chunks:
                raise ServiceError("资料索引字段无效，请重新索引资料")
            chunks_by_id = {row["id"]: row for row in all_chunks}
            build_started = time.monotonic()
            bm25 = await self.lexical(lambda: BM25Plus([tokens(row["ranking_text"]) or ["_"] for row in all_chunks], delta=0)) if all_chunks else None
            RAG_RETRIEVAL_SECONDS.labels(stage="bm25_build").observe(time.monotonic() - build_started)
            if not reasons:
                while len(self._corpora) >= self.settings.rag_corpus_cache_users:
                    self._corpora.pop(next(iter(self._corpora)))
                self._corpora[user] = (corpus_hash, all_chunks, chunks_by_id, bm25)
        corpus_ms = round((time.monotonic() - corpus_started) * 1000, 2)
        RAG_RETRIEVAL_SECONDS.labels(stage="corpus_load").observe(corpus_ms / 1000)
        if not all_chunks and not indexed:
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
                    if source is None and indexed:
                        hydrated = await self.lexical_index.hydrate(user, [row["id"]])
                        if hydrated:
                            source = hydrated[0]
                            chunks_by_id[source["id"]] = source
                            all_chunks.append(source)
                    if not source or row["id"] in seen:
                        raise ValueError("cache provenance")
                    seen.add(row["id"])
                    scores = {key: score(row[key]) for key in ("score", "vector_score", "bm25_score")}
                    stage = row.get("ranking_stage", "fusion")
                    if stage not in {"fusion", "rerank"}:
                        raise ValueError("cache stage")
                    rerank_value = row.get("rerank_score")
                    if rerank_value is not None:
                        if isinstance(rerank_value, bool) or not isinstance(rerank_value, (int, float)) or not math.isfinite(float(rerank_value)):
                            raise ValueError("cache rerank score")
                        rerank_value = float(rerank_value)
                    result.append({**source, **scores, "rerank_score": rerank_value, "ranking_stage": stage})
                await self.record_retrieval(run_id, {"cache_hit": True, "queries": len(queries),
                    "corpus_documents": len(documents), "corpus_chunks": corpus_count, "cache_ms": cache_ms,
                    "total_ms": round((time.monotonic() - started) * 1000, 2)})
                RAG_RETRIEVAL_SECONDS.labels(stage="total").observe(time.monotonic() - started)
                await self.check_corpus(user, corpus_hash)
                context_rows = await self.lexical_index.neighbors(user, result) if indexed else all_chunks
                return RetrievalResults(self.with_context(result, context_rows))
            except (TypeError, ValueError, KeyError):
                reasons.add("invalid_records")
                await self.db.execute("DELETE FROM document_search_cache WHERE user_id=? AND corpus_hash=? AND query_hash=? AND limit_value=?", cache_key)

        fusion_candidate_limit = max(
            limit, self.settings.reranker_candidate_limit if self.reranker and self.reranker.enabled else limit)
        vector_candidate_limit = max(limit * 3, fusion_candidate_limit)

        vector_observations = []
        async def retrieve_query(query):
            lexical_started = time.monotonic()
            lexical = sql_lexical.get(query, {}) if indexed else await self.lexical(bm25.get_scores, tokens(query))
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
                        fetch_limit = vector_candidate_limit
                        refills = 0
                        cap = min(200, vector_candidate_limit * 4)
                        while True:
                            raw_hits = await self.vectors.search("documents", user, embeddings[0], limit=fetch_limit)
                            if indexed and isinstance(raw_hits, list):
                                hydrated = await self.lexical_index.hydrate(user,
                                    [hit.get("id") for hit in raw_hits[:fetch_limit] if isinstance(hit, dict)])
                                for source in hydrated:
                                    if source["id"] not in chunks_by_id:
                                        chunks_by_id[source["id"]] = source
                                        all_chunks.append(source)
                            hits, rejected = normalize_hits(raw_hits, chunks_by_id, fetch_limit)
                            if len(hits) >= vector_candidate_limit or len(raw_hits) < fetch_limit or fetch_limit >= cap:
                                break
                            fetch_limit = min(cap, fetch_limit * 2)
                            refills += 1
                        vector_observations.append({"requested":fetch_limit,"received":len(raw_hits),"valid":len(hits),
                            "invalid":max(0,len(raw_hits)-len(hits)),"refills":refills})
                        RAG_VECTOR_CANDIDATES.labels(state="valid").inc(len(hits))
                        RAG_VECTOR_CANDIDATES.labels(state="invalid").inc(max(0,len(raw_hits)-len(hits)))
                        RAG_VECTOR_REFILLS.inc(refills)
                        if rejected:
                            failures.add("invalid_records")
                        if len(hits) < vector_candidate_limit and fetch_limit >= cap and rejected:
                            failures.add("valid_candidates_exhausted")
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
            maximum = (max(lexical.values(), default=0) if indexed else max(lexical)) or 1
            lexical_rows = ((chunks_by_id[key], value) for key, value in lexical.items() if key in chunks_by_id) if indexed else zip(all_chunks, lexical)
            for row, value in lexical_rows:
                if value > 0:
                    entry = scores.setdefault(row["id"], {"row": row, "lexical": 0.0, "vector": 0.0})
                    entry["lexical"] = max(entry["lexical"], float(value / maximum))
            for key, value in hits.items():
                if value >= self.settings.rag_min_vector_score:
                    entry = scores.setdefault(key, {"row": chunks_by_id[key], "lexical": 0.0, "vector": 0.0})
                    entry["vector"] = max(entry["vector"], value)
        fusion_started = time.monotonic()
        ranked = sorted(scores.values(), key=lambda item: 0.65 * item["vector"] + 0.35 * item["lexical"], reverse=True)
        candidates = []
        for item in ranked:
            row = item["row"]
            # Only exact duplicate text from the same document/version is safely redundant.
            if any(row["text"] == c["text"] and row["document_id"] == c["document_id"]
                   and row["index_version"] == c["index_version"] for c in candidates):
                continue
            candidates.append(dict(row, score=round(0.65 * item["vector"] + 0.35 * item["lexical"], 4),
                                   vector_score=round(item["vector"], 4), bm25_score=round(item["lexical"], 4)))
            if len(candidates) >= fusion_candidate_limit:
                break
        selected = [{**row, "rerank_score": None, "ranking_stage": "fusion"} for row in candidates[:limit]]
        reranker_ms = 0.0
        if candidates and self.reranker and self.reranker.enabled:
            rerank_started = time.monotonic()
            try:
                order = await self.reranker.rerank(original_query, candidates, min(limit, len(candidates)))
                selected = [{**candidates[index], "rerank_score": round(value, 6), "ranking_stage": "rerank"}
                            for index, value in order]
            except ServiceError:
                reasons.add("reranker_unavailable")
                selected = [{**row, "rerank_score": None, "ranking_stage": "fusion_fallback"}
                            for row in candidates[:limit]]
            finally:
                reranker_ms = (time.monotonic() - rerank_started) * 1000
                RAG_RETRIEVAL_SECONDS.labels(stage="rerank").observe(reranker_ms / 1000)
        if not selected and any(failures & {"vector_unavailable", "invalid_records"} for _, _, failures, _, _ in results):
            raise ServiceError("资料检索发生故障或返回无效字段，关键词检索也未找到证据，请重试或重建索引")
        RAG_RETRIEVAL_SECONDS.labels(stage="fusion").observe(time.monotonic() - fusion_started)
        RAG_RETRIEVAL_SECONDS.labels(stage="total").observe(time.monotonic() - started)
        # Never cache a degraded response: a repaired service must be retried on the next request.
        if not reasons:
            await self.db.execute("""INSERT OR REPLACE INTO document_search_cache
                (user_id,corpus_hash,query_hash,limit_value,result,created_at) VALUES(?,?,?,?,?,?)""",
                (*cache_key, json.dumps([{k: row.get(k) for k in ("id", "score", "vector_score", "bm25_score",
                    "rerank_score", "ranking_stage")} for row in selected]), now()))
        await self.record_retrieval(run_id, {"cache_hit": False, "corpus_cache_hit": corpus_cache_hit,
            "queries": len(queries), "corpus_documents": len(documents), "corpus_chunks": corpus_count,
            "hydrated_chunks": len(all_chunks), "lexical_backend": "database" if indexed else "small_corpus_bm25",
            "cache_ms": cache_ms, "corpus_ms": corpus_ms, "embedding_ms": round(embed_ms, 2),
            "vector_ms": round(vector_ms, 2), "reranker_ms": round(reranker_ms, 2),
            "ranking_stage": selected[0]["ranking_stage"] if selected else "fusion",
            "total_ms": round((time.monotonic() - started) * 1000, 2),
            "candidates": len(ranked), "selected": len(selected), "reasons": sorted(reasons),
            "vector_candidates": {key:sum(item[key] for item in vector_observations) for key in ("requested","received","valid","invalid","refills")}})
        await self.check_corpus(user, corpus_hash)
        context_rows = await self.lexical_index.neighbors(user, selected) if indexed else all_chunks
        selected = self.with_context(selected, context_rows)
        return RetrievalResults(selected, reasons=reasons)

    @staticmethod
    def ranking_text(row):
        # Metadata helps rank identical clauses from different documents. It is not
        # substituted for original evidence and does not require rewriting vectors.
        return f"标题：{str(row.get('title',''))[:160]}\n定位：{str(row.get('locator',''))[:400]}\n原文：{row['text'][:12000]}"

    @staticmethod
    def with_context(selected, all_chunks):
        lookup = {(row["document_id"], row["ordinal"]): row for row in all_chunks}
        count, chars, seen = 0, 0, {row["id"] for row in selected}
        output = []
        for row in selected:
            context = []
            incomplete = False
            for offset in (-1, 1):
                neighbor = lookup.get((row["document_id"], row["ordinal"] + offset))
                if not neighbor or neighbor["id"] in seen:
                    continue
                if count >= 12 or chars + len(neighbor["text"]) > 12000:
                    incomplete = True
                    continue
                context.append({k: neighbor[k] for k in ("id","document_id","index_version","document_hash","locator","text","title")})
                seen.add(neighbor["id"])
                count += 1
                chars += len(neighbor["text"])
            output.append({**row, "context_blocks": context, "context_incomplete": incomplete})
        return output

    async def delete(self, doc_id, user):
        async with self.db.guard("documents:" + user):
            document = await self.db.one("SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, user))
            if not document:
                return
            async with self.db.connection() as conn:
                await conn.execute("""INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version,object_key)
                    VALUES(?,?,?,?)""", (doc_id, user, document["index_version"], document["object_key"] or doc_id))
                if document["pending_version"]:
                    await conn.execute("""INSERT OR IGNORE INTO document_cleanup(document_id,user_id,version,object_key)
                        VALUES(?,?,?,?)""", (doc_id, user, document["pending_version"], document["pending_object_key"]))
                await conn.execute("""UPDATE documents SET status='deleted',index_version=index_version+1,pending_version=0,
                    pending_name='',pending_hash='',pending_object_key='',updated_at=? WHERE id=? AND user_id=?""", (now(), doc_id, user))
                if document["status"] in {"ready", "rebuilding"}:
                    old_chunks = await self.db.rows("SELECT id,text,locator,ordinal FROM chunks WHERE document_id=? ORDER BY ordinal", (doc_id,))
                    await conn.execute("""INSERT OR IGNORE INTO document_versions(document_id,user_id,version,hash,name,object_key,status,blocks_json,chunks_json,created_at)
                        VALUES(?,?,?,?,?,?,'ready',?,?,?)""", (doc_id,user,document["index_version"],document["hash"],document["name"],document["object_key"] or doc_id,
                        json.dumps([{k:r[k] for k in ("text","locator")} for r in old_chunks],ensure_ascii=False),json.dumps(old_chunks,ensure_ascii=False),now()))
                    await self.lifecycle.changed(conn,user,doc_id,document["index_version"],0,"deleted")
                await conn.execute("DELETE FROM chunks WHERE document_id=? AND user_id=?", (doc_id, user))
                await self.lexical_index.remove_document(conn, doc_id)
                await conn.execute("DELETE FROM document_scan_jobs WHERE document_id=?", (doc_id,))
                await conn.commit()
            await self.invalidate_search_cache(user)
        await self.cleanup_pending()
        await self.lifecycle.process_events()

    async def cleanup_pending(self):
        for row in await self.db.rows("SELECT * FROM document_cleanup"):
            async with self.db.guard("documents:" + row["user_id"]):
                document = await self.db.one("SELECT status,index_version,pending_version,object_key,pending_object_key FROM documents WHERE id=?", (row["document_id"],))
                if (document and document["status"] != "deleted"
                        and row["version"] in {document["index_version"], document["pending_version"]}):
                    continue
                try:
                    await self.vectors.delete_document_version(row["user_id"], row["document_id"], row["version"])
                    object_key = row.get("object_key", "")
                    archived = await self.db.one("SELECT document_id FROM document_versions WHERE user_id=? AND object_key=? LIMIT 1", (row["user_id"], object_key))
                    if object_key and not archived and (not document or document["status"] == "deleted"
                                       or object_key not in {document["object_key"], document["pending_object_key"]}):
                        for directory in ("uploads", "quarantine"):
                            await self._storage_mutation(self.store.delete(directory, object_key))
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
        object_key = document["object_key"] or doc_id
        if not await self.store.exists("uploads", object_key):
            raise ServiceError("原始上传文件不存在，无法重建索引")
        await self.db.execute("DELETE FROM metadata WHERE key=?", (f"index-attempt:{doc_id}:{document['index_version'] + 1}",))
        if document["status"] == "ready":
            await self.db.execute("""UPDATE documents SET status='rebuilding',error='',pending_version=index_version+1,
                pending_name=name,pending_hash=hash,pending_object_key=?,updated_at=? WHERE id=?""", (object_key, now(), doc_id))
        else:
            await self.db.execute("UPDATE documents SET status='indexing',error='',index_version=index_version+1,updated_at=? WHERE id=?", (now(), doc_id))
        return await self.db.one("SELECT * FROM documents WHERE id=?", (doc_id,))

    async def close(self):
        self.lexical_pool.shutdown(wait=False, cancel_futures=True)
