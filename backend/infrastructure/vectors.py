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

    async def fingerprint(self):
        config = json.dumps([self.settings.embedding_model, self.settings.embedding_base_url,
                             self.settings.embedding_dimension, self.settings.demo_mode])
        value = hashlib.sha256(config.encode()).hexdigest()
        await self.db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('embedding_fingerprint',?)", (value,))
        existing = await self.db.one("SELECT value FROM metadata WHERE key='embedding_fingerprint'")
        if existing["value"] != value:
            raise ServiceError("嵌入模型或维度已改变。请使用独立 DATA_DIR/数据卷并重新导入资料，不能混用旧向量")

    def client(self):
        with self._lock:
            if self._client is None:
                from pymilvus import MilvusClient
                self._client = MilvusClient(uri=self.settings.milvus_uri, timeout=8)
            return self._client

    def ensure(self, kind: str):
        client = self.client()
        name = f"dr_{kind}"
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
                    ON c.document_id=d.id WHERE c.user_id=? AND d.status='ready'""", (user,))
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
        assert field in {"id", "document_id"}
        def operation():
            client, name = self.ensure(kind)
            client.delete(collection_name=name, filter=f"user_id == {json.dumps(user)} and {field} == {json.dumps(value)}", timeout=10)
        try:
            await asyncio.to_thread(operation)
        except Exception:
            # SQL tombstones are checked after vector retrieval; an orphan is never returned.
            raise ServiceError("资料已停止参与检索，但向量清理失败；可再次执行删除重试") from None

    async def close(self):
        if self._client:
            await asyncio.to_thread(self._client.close)
