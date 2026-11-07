import asyncio
import hashlib
import json
import threading

from ..core.config import Settings
from ..core.db import Database
from .providers import ServiceError


class VectorIndex:
    def __init__(self, settings: Settings, db: Database):
        self.settings, self.db = settings, db
        self._client = None
        self._lock = threading.Lock()
        self._ready = set()
        self._active_prefix = settings.vector_collection_prefix

    async def fingerprint(self):
        config = json.dumps([self.settings.embedding_model, self.settings.embedding_base_url,
                             self.settings.embedding_dimension, self.settings.demo_mode])
        value = hashlib.sha256(config.encode()).hexdigest()
        await self.db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('embedding_fingerprint',?)", (value,))
        existing = await self.db.one("SELECT value FROM metadata WHERE key='embedding_fingerprint'")
        if existing["value"] != value:
            raise ServiceError("嵌入模型、端点或维度与活动索引不匹配；请恢复匹配配置或完成受控影子索引迁移，不能混用旧向量")
        active = await self.db.one("SELECT value FROM metadata WHERE key='embedding_active_config'")
        if active:
            config = json.loads(active["value"])
            if config["fingerprint"] != value:
                raise ServiceError("活动索引配置与嵌入指纹不匹配", code="embedding_index_mismatch", retryable=False)
            self._active_prefix = config["prefix"]

    def client(self):
        with self._lock:
            if self._client is None:
                from pymilvus import MilvusClient
                options = {"server_pem_path": self.settings.milvus_server_pem_path} if self.settings.milvus_server_pem_path else {}
                self._client = MilvusClient(uri=self.settings.milvus_uri,
                                            token=self.settings.milvus_token.get_secret_value(),
                                            db_name=self.settings.milvus_db_name, timeout=8, **options)
            return self._client

    def ensure(self, kind: str, *, prefix=None):
        client = self.client()
        name = f"{prefix or self._active_prefix}_{kind}"
        with self._lock:
            if name not in self._ready:
                if not client.has_collection(name):
                    client.create_collection(name, dimension=self.settings.embedding_dimension,
                        primary_field_name="id", id_type="string", max_length=64,
                        vector_field_name="vector", metric_type="COSINE", consistency_level="Strong")
                else:
                    fields = client.describe_collection(name)["fields"]
                    dim = next(int(f["params"]["dim"]) for f in fields if f["name"] == "vector")
                    if dim != self.settings.embedding_dimension:
                        raise ServiceError("Milvus 集合维度与当前嵌入模型不一致")
                self._ready.add(name)
        return client, name

    async def ping(self):
        if self.settings.demo_mode:
            return
        try:
            await asyncio.to_thread(lambda: self.client().list_collections(timeout=5))
        except Exception:
            raise ServiceError("Milvus 不可用，请检查容器健康状态和 MILVUS_URI") from None

    async def upsert(self, kind: str, records: list[dict]):
        if not records:
            return
        from ..services.embedding_migration import check_embedding_barrier
        await check_embedding_barrier(self.db)
        await self.fingerprint()
        if self.settings.demo_mode:
            return
        def operation():
            client, name = self.ensure(kind)
            client.upsert(collection_name=name, data=records, timeout=30)
        try:
            await asyncio.to_thread(operation)
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("Milvus 写入失败") from None

    async def search(self, kind: str, user: str, vector: list[float], limit=8, thread_id: str | None = None) -> list[dict]:
        await self.fingerprint()
        if self.settings.demo_mode:
            if kind == "documents":
                rows = await self.db.rows("""SELECT c.*, d.name AS title FROM chunks c JOIN documents d
                    ON c.document_id=d.id WHERE c.user_id=? AND d.status IN ('ready','rebuilding')""", (user,))
            else:
                rows = await self.db.rows("""SELECT m.*,m.content AS text FROM memories m JOIN runs r ON r.id=m.run_id
                    WHERE m.user_id=? AND m.kind='semantic' AND (? IS NULL OR r.thread_id=?)""",
                    (user, thread_id, thread_id))
            for row in rows:
                v = json.loads(row["vector"])
                row["score"] = sum(a * b for a, b in zip(v, vector))
            return sorted(rows, key=lambda r: r["score"], reverse=True)[:limit]
        def operation():
            client, name = self.ensure(kind)
            filter_expression = f"user_id == {json.dumps(user)}"
            if thread_id:
                filter_expression += f" and thread_id == {json.dumps(thread_id)}"
            hits = client.search(collection_name=name, data=[vector],
                filter=filter_expression, limit=limit,
                output_fields=["id", "text", "document_id", "locator", "title"], timeout=10)
            return [dict(hit["entity"], score=hit["distance"]) for hit in hits[0]]
        try:
            return await asyncio.to_thread(operation)
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("Milvus 检索失败，本次将使用其他可用来源") from None

    async def delete(self, kind: str, user: str, field: str, value: str):
        if self.settings.demo_mode:
            return
        from ..services.embedding_migration import check_embedding_barrier
        await check_embedding_barrier(self.db)
        await self.fingerprint()
        assert field in {"id", "document_id"}
        def operation():
            client, name = self.ensure(kind)
            client.delete(collection_name=name, filter=f"user_id == {json.dumps(user)} and {field} == {json.dumps(value)}", timeout=10)
        try:
            await asyncio.to_thread(operation)
        except Exception:
            # SQL tombstones are checked after vector retrieval; an orphan is never returned.
            raise ServiceError("资料已停止参与检索，但向量清理失败；可再次执行删除重试") from None

    async def delete_document_version(self, user, document_id, version):
        if self.settings.demo_mode:
            return
        from ..services.embedding_migration import check_embedding_barrier
        await check_embedding_barrier(self.db)
        await self.fingerprint()
        def operation():
            client, name = self.ensure("documents")
            # Legacy version zero has no dynamic version field; match its stable IDs.
            ids = [hashlib.sha256((f"{document_id}:{i}" if version == 0 else
                                  f"{document_id}:{version}:{i}").encode()).hexdigest()[:32]
                   for i in range(400)]
            client.delete(collection_name=name,
                          filter=f"user_id == {json.dumps(user)} and id in {json.dumps(ids)}", timeout=10)
        await asyncio.to_thread(operation)

    async def upsert_shadow(self, kind, records, prefix):
        """Write a dedicated prepared migration collection without changing the active fingerprint."""
        import re
        if kind not in {"documents", "memories"} or not re.fullmatch(r"dr_migration_[a-f0-9]{32}", prefix):
            raise ServiceError("迁移集合名称无效", code="migration_target_mismatch", retryable=False)
        from ..core.usage_governance import usage_scope
        scope = usage_scope.get()
        if not scope or scope.purpose != "embedding_migration":
            raise ServiceError("迁移写入缺少授权上下文", code="migration_operator_required", retryable=False)
        if self.settings.demo_mode:
            raise ServiceError("真实影子索引迁移不允许使用演示向量", code="migration_demo_forbidden", retryable=False)
        for record in records:
            if len(record["vector"]) != self.settings.embedding_dimension:
                raise ServiceError("影子向量维度不匹配", code="migration_dimension_mismatch", retryable=False)
        def operation():
            client, name = self.ensure(kind, prefix=prefix)
            client.upsert(collection_name=name, data=records, timeout=30)
        try:
            await asyncio.to_thread(operation)
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("影子向量写入未确认，可在检查后恢复迁移", code="migration_vector_unavailable",
                               retryable=True, result_unknown=True) from None

    async def verify_shadow(self, items, prefix):
        """Check every migrated ID, workspace and source hash against the actual remote index."""
        if self.settings.demo_mode:
            raise ServiceError("演示向量不能用于真实迁移放行", code="migration_demo_forbidden", retryable=False)
        for kind in ("documents", "memories"):
            expected = [item for item in items if item["kind"] == kind]
            if not expected:
                continue
            def operation():
                client, name = self.ensure(kind, prefix=prefix)
                fields = client.describe_collection(name)["fields"]
                dimension = next(int(field["params"]["dim"]) for field in fields if field["name"] == "vector")
                if dimension != self.settings.embedding_dimension:
                    raise ServiceError("影子集合实际维度与目标配置不一致", code="migration_dimension_mismatch", retryable=False)
                found = {}
                for offset in range(0, len(expected), 128):
                    ids = [row["item_id"] for row in expected[offset:offset + 128]]
                    rows = client.query(collection_name=name, filter="id in " + json.dumps(ids),
                                        output_fields=["id", "user_id", "migration_source_hash"],
                                        consistency_level="Strong", timeout=30)
                    found.update({row["id"]: row for row in rows})
                return found
            try:
                found = await asyncio.to_thread(operation)
            except ServiceError:
                raise
            except Exception:
                raise ServiceError("无法确认影子索引覆盖率", code="migration_vector_unavailable", retryable=False) from None
            if any(item["item_id"] not in found or found[item["item_id"]].get("user_id") != item["workspace_id"]
                   or found[item["item_id"]].get("migration_source_hash") != item["source_hash"] for item in expected):
                raise ServiceError("影子索引缺失、归属或来源指纹不一致", code="migration_coverage_mismatch", retryable=False)

    async def drop_collections(self):
        """Remove only this index instance's named collections (evaluation cleanup)."""
        if self.settings.demo_mode or not self._ready:
            return
        names = tuple(self._ready)
        def operation():
            client = self.client()
            for name in names:
                if client.has_collection(name):
                    client.drop_collection(name)
        await asyncio.to_thread(operation)
        self._ready.clear()
    async def close(self):
        if self._client:
            await asyncio.to_thread(self._client.close)
