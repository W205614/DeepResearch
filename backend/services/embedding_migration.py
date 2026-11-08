"""Explicit, budgeted shadow-index migration; ordinary runtime never starts it."""
from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json

from ..core.db import now, uid
from ..core.reliability import ServiceError, estimate_tokens
from ..core.usage_governance import paid_scope, usage_scope


async def check_embedding_barrier(db):
    if db is None:
        raise ServiceError("费用账本不可用，已阻止外发", code="usage_unavailable", retryable=False)
    scope = usage_scope.get()
    if scope and scope.purpose == "embedding_migration":
        return
    row = await db.one("SELECT value FROM metadata WHERE key='embedding_migration_barrier'")
    if row and row["value"]:
        raise ServiceError("知识库正在执行受控索引迁移，暂不接受修改或新的模型调用",
                           code="embedding_migration_active", retryable=True)


def embedding_config(settings):
    return {"model": settings.embedding_model, "base_url": settings.embedding_base_url,
            "dimension": settings.embedding_dimension, "demo_mode": settings.demo_mode}


def embedding_fingerprint(settings):
    return hashlib.sha256(json.dumps([settings.embedding_model, settings.embedding_base_url,
                                      settings.embedding_dimension, settings.demo_mode]).encode()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def read_rows(reader, sql, args=()):
    if hasattr(reader, "rows"):
        return await reader.rows(sql, args)
    cursor = await reader.execute(sql, args)
    return [dict(row) for row in await cursor.fetchall()]


async def read_one(reader, sql, args=()):
    rows = await read_rows(reader, sql, args)
    return rows[0] if rows else None


SOURCE_QUERIES = {
    "documents": ("""SELECT c.id,c.user_id,c.document_id,c.text,c.locator,c.ordinal,
        d.name AS title,d.index_version,d.hash AS document_hash
        FROM chunks c JOIN documents d ON d.id=c.document_id AND d.user_id=c.user_id
        WHERE d.status='ready'""", "c.id"),
    "memories": ("""SELECT m.id,m.user_id,m.content AS text,m.run_id,m.owner_subject,
        r.thread_id,r.topic AS title,r.created_by,r.sources FROM memories m JOIN runs r ON r.id=m.run_id AND r.user_id=m.user_id
        WHERE m.kind='semantic'""", "m.id"),
}


async def source_items(reader):
    for kind, (query, column) in SOURCE_QUERIES.items():
        cursor = ""
        while True:
            rows = await read_rows(reader, query + f" AND {column}>? ORDER BY {column} LIMIT 256", (cursor,))
            if not rows:
                break
            for row in rows:
                yield {"kind": kind, "item_id": row["id"], "workspace_id": row["user_id"],
                       "source_hash": digest(row), "source": row}
            cursor = rows[-1]["id"]


async def source_snapshot(reader):
    items = [{key: item[key] for key in ("kind", "item_id", "workspace_id", "source_hash")}
             async for item in source_items(reader)]
    acl = {}
    for table, query in {
        "workspaces": "SELECT id,created_by,owner_subject FROM workspaces ORDER BY id",
        "memberships": "SELECT workspace_id,subject,role FROM memberships ORDER BY workspace_id,subject",
        "platform_users": "SELECT subject,status,revoked_before FROM platform_users ORDER BY subject",
        "document_policies": "SELECT * FROM document_policies ORDER BY document_id",
        "collections": "SELECT * FROM collections ORDER BY id",
        "collection_grants": "SELECT * FROM collection_grants ORDER BY collection_id,subject",
    }.items():
        acl[table] = await read_rows(reader, query)
    return items, digest({"items": [{k: row[k] for k in ("kind", "item_id", "workspace_id", "source_hash")}
                                   for row in items], "acl": acl})


async def assert_idle(reader):
    runs = await read_one(reader, "SELECT COUNT(*) AS total FROM runs WHERE status IN ('queued','running','interrupted')")
    documents = await read_one(reader, "SELECT COUNT(*) AS total FROM documents WHERE status IN ('scanning','indexing','rebuilding')")
    if runs["total"] or documents["total"]:
        raise ServiceError("索引迁移需要维护窗口，仍有研究或文档任务未结束",
                           code="migration_not_idle", retryable=False)


@asynccontextmanager
async def frozen_sources(db):
    async with db.connection() as conn:
        if hasattr(db, "dsn"):
            await conn.execute("""LOCK TABLE documents,chunks,memories,runs,workspaces,memberships,
                platform_users,document_policies,collections,collection_grants,
                embedding_migration_items,metadata IN SHARE ROW EXCLUSIVE MODE""")
        else:
            await conn.execute("BEGIN IMMEDIATE")
        yield conn


class EmbeddingMigration:
    def __init__(self, db, source_settings, target_settings, providers, vectors):
        self.db, self.source_settings, self.target_settings = db, source_settings, target_settings
        self.providers, self.vectors = providers, vectors

    async def inspect(self):
        counts, estimated_tokens = {"documents": 0, "memories": 0}, 0
        async for item in source_items(self.db):
            counts[item["kind"]] += 1
            estimated_tokens += estimate_tokens(item["source"]["text"])
        if hasattr(self.db, "dsn"):
            columns = await self.db.rows("""SELECT table_name,column_name FROM information_schema.columns
                WHERE table_schema=current_schema() AND table_name IN
                ('workspaces','platform_users','embedding_migrations','embedding_migration_items')""")
            schema = {(row["table_name"], row["column_name"]) for row in columns}
        else:
            schema = set()
            for table in ("workspaces", "platform_users", "embedding_migrations", "embedding_migration_items"):
                schema.update((table, row["name"]) for row in await self.db.rows(f"PRAGMA table_info({table})"))
        upgraded = {("workspaces", "owner_subject"), ("platform_users", "status"),
                    ("embedding_migrations", "source_snapshot"), ("embedding_migration_items", "reserved_tokens")} <= schema
        snapshot = (await source_snapshot(self.db))[1] if upgraded else None
        row = await self.db.one("SELECT value FROM metadata WHERE key='embedding_fingerprint'")
        document_statuses = await self.db.rows("SELECT status,COUNT(*) AS total FROM documents GROUP BY status ORDER BY status")
        memory_kinds = await self.db.rows("SELECT kind,COUNT(*) AS total FROM memories GROUP BY kind ORDER BY kind")
        chunks = await self.db.one("SELECT COUNT(*) AS total FROM chunks")
        return {"read_only": True, "source_fingerprint": row["value"] if row else "",
                "source_snapshot": snapshot, "target_fingerprint": embedding_fingerprint(self.target_settings),
                "target": embedding_config(self.target_settings), "eligible_items": counts,
                "requires_schema_upgrade": not upgraded,
                "document_statuses": {item["status"]: item["total"] for item in document_statuses},
                "memory_kinds": {item["kind"]: item["total"] for item in memory_kinds},
                "total_chunks": chunks["total"],
                "estimated_input_tokens": estimated_tokens,
                "estimate_is_billable_token_count": False, "provider_calls": 0}

    async def prepare(self, *, maintenance_acknowledged):
        if not maintenance_acknowledged:
            raise ServiceError("必须显式确认维护窗口", code="maintenance_required", retryable=False)
        async with self.db.guard("embedding-migration"), frozen_sources(self.db) as conn:
            await assert_idle(conn)
            barrier = await read_one(conn, "SELECT value FROM metadata WHERE key='embedding_migration_barrier'")
            if barrier and barrier["value"]:
                raise ServiceError("已有未结束的索引迁移", code="migration_already_active", retryable=False)
            items, snapshot = await source_snapshot(conn)
            source = await read_one(conn, "SELECT value FROM metadata WHERE key='embedding_fingerprint'")
            source_fingerprint = source["value"] if source else embedding_fingerprint(self.source_settings)
            if source_fingerprint != embedding_fingerprint(self.source_settings):
                raise ServiceError("源配置不匹配当前索引", code="embedding_source_mismatch", retryable=False)
            target_fingerprint = embedding_fingerprint(self.target_settings)
            if target_fingerprint == source_fingerprint:
                raise ServiceError("目标配置与当前索引相同，无需迁移", code="embedding_target_unchanged", retryable=False)
            migration_id, stamp = uid(), now()
            prefix = "dr_migration_" + migration_id
            await conn.execute("""INSERT INTO embedding_migrations(id,source_fingerprint,source_snapshot,target_fingerprint,
                target_config_json,target_prefix,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'prepared',?,?)""",
                               (migration_id, source_fingerprint, snapshot, target_fingerprint,
                                json.dumps(embedding_config(self.target_settings), sort_keys=True), prefix, stamp, stamp))
            for item in items:
                await conn.execute("""INSERT INTO embedding_migration_items(migration_id,kind,item_id,workspace_id,
                    source_hash,status,attempts,reserved_tokens,error_code,updated_at) VALUES(?,?,?,?,?,'pending',0,0,'',?)""",
                                   (migration_id, item["kind"], item["item_id"], item["workspace_id"], item["source_hash"], stamp))
            await conn.execute("INSERT INTO metadata(key,value) VALUES('embedding_migration_barrier',?) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
                               (migration_id,))
            await conn.commit()
        return await self.progress(migration_id)

    async def progress(self, migration_id):
        row = await self.db.one("SELECT * FROM embedding_migrations WHERE id=?", (migration_id,))
        if not row:
            raise ServiceError("迁移不存在", code="migration_not_found", retryable=False)
        statuses = await self.db.rows("SELECT status,COUNT(*) AS total FROM embedding_migration_items WHERE migration_id=? GROUP BY status", (migration_id,))
        budget = await self.db.one("SELECT COALESCE(SUM(attempts),0) AS requests,COALESCE(SUM(reserved_tokens),0) AS reserved_tokens FROM embedding_migration_items WHERE migration_id=?", (migration_id,))
        budget = {key: int(value) for key, value in budget.items()}
        return {"migration_id": migration_id, "status": row["status"], "target_prefix": row["target_prefix"],
                "target": json.loads(row["target_config_json"]), "counts": {value["status"]: value["total"] for value in statuses},
                "budget": budget, "activated_at": row["activated_at"]}

    async def _validate(self, migration_id):
        migration = await self.db.one("SELECT * FROM embedding_migrations WHERE id=?", (migration_id,))
        if not migration:
            raise ServiceError("迁移不存在", code="migration_not_found", retryable=False)
        if migration["status"] in {"activated", "aborted"}:
            raise ServiceError("该迁移已经结束", code="migration_finished", retryable=False)
        if migration["target_fingerprint"] != embedding_fingerprint(self.target_settings):
            raise ServiceError("目标配置与已准备迁移不一致", code="migration_target_mismatch", retryable=False)
        barrier = await self.db.one("SELECT value FROM metadata WHERE key='embedding_migration_barrier'")
        if not barrier or barrier["value"] != migration_id:
            raise ServiceError("迁移维护屏障已失效", code="migration_barrier_lost", retryable=False)
        current = await self.db.one("SELECT value FROM metadata WHERE key='embedding_fingerprint'")
        if current and current["value"] != migration["source_fingerprint"]:
            raise ServiceError("当前索引配置已变化", code="embedding_source_mismatch", retryable=False)
        await assert_idle(self.db)
        items, snapshot = await source_snapshot(self.db)
        if snapshot != migration["source_snapshot"]:
            raise ServiceError("源资料或权限已变化，不能继续旧迁移", code="migration_source_changed", retryable=False)
        return migration, items

    async def _require_operator(self, subject, workspace):
        if subject not in self.source_settings.operator_subjects:
            raise ServiceError("迁移需要明确配置的平台运维账号", code="migration_operator_required", retryable=False)
        member = await self.db.membership(workspace, subject)
        if not member or member["role"] not in {"admin", "researcher"}:
            raise ServiceError("迁移账号缺少当前工作空间写权限", code="permission_revoked", retryable=False)
        user = await self.db.one("SELECT status FROM platform_users WHERE subject=?", (subject,))
        if user and user["status"] != "active":
            raise ServiceError("迁移账号已停用", code="account_disabled", retryable=False)

    async def _require_source_access(self, subject, item):
        from .document_access import require_document, require_sources
        source, workspace = item["source"], item["workspace_id"]
        with paid_scope(workspace, subject, "embedding_migration"):
            if item["kind"] == "documents":
                await require_document(self.db, workspace, source["document_id"], write=True)
            else:
                member = await self.db.membership(workspace, subject)
                if not member or (member["role"] != "admin" and source["created_by"] != subject):
                    raise ServiceError("迁移账号没有该历史记忆的当前权限", code="source_permission_denied", retryable=False)
                try:
                    sources = json.loads(source["sources"] or "[]")
                except (TypeError, ValueError):
                    raise ServiceError("历史记忆来源权限信息无效", code="source_permission_denied", retryable=False) from None
                await require_sources(self.db, workspace, sources)

    async def run(self, migration_id, *, subject, max_requests, max_reserved_tokens,
                  allow_paid=False, retry_failed=False, retry_unknown=False):
        if not allow_paid or max_requests < 1 or max_reserved_tokens < 1:
            raise ServiceError("迁移外发需要明确授权和累计调用／Token 上限", code="migration_budget_required", retryable=False)
        if not self.target_settings.allow_internal_model_processing:
            raise ServiceError("历史资料重新嵌入需要批准内部资料模型处理", code="egress_denied", retryable=False)
        async with self.db.guard("embedding-migration"):
            migration, sources = await self._validate(migration_id)
            source_map = {(item["kind"], item["item_id"]): item for item in sources}
            rows = await self.db.rows("SELECT * FROM embedding_migration_items WHERE migration_id=? ORDER BY kind,item_id", (migration_id,))
            for row in rows:
                if row["status"] == "done" or (row["status"] == "failed" and not retry_failed):
                    continue
                if row["status"] in {"unknown", "processing"} and not retry_unknown:
                    continue
                item = source_map[(row["kind"], row["item_id"])]
                query, column = SOURCE_QUERIES[row["kind"]]
                source = await self.db.one(query + f" AND {column}=?", (row["item_id"],))
                if source is None or digest(source) != row["source_hash"]:
                    raise ServiceError("源资料已变化，不能继续外发", code="migration_source_changed", retryable=False)
                item = {**item, "source": source}
                await self._require_operator(subject, row["workspace_id"])
                await self._require_source_access(subject, item)
                cost = self.providers.context_cost({"model": self.target_settings.embedding_model,
                                                   "input": [item["source"]["text"]], "encoding_format": "float",
                                                   "dimensions": self.target_settings.embedding_dimension})
                budget = await self.db.one("SELECT COALESCE(SUM(attempts),0) AS requests,COALESCE(SUM(reserved_tokens),0) AS tokens FROM embedding_migration_items WHERE migration_id=?", (migration_id,))
                if budget["requests"] >= max_requests or budget["tokens"] + cost > max_reserved_tokens:
                    break
                await self.db.execute("""UPDATE embedding_migration_items SET status='processing',attempts=attempts+1,
                    reserved_tokens=reserved_tokens+?,error_code='',updated_at=? WHERE migration_id=? AND kind=? AND item_id=?""",
                                      (cost, now(), migration_id, row["kind"], row["item_id"]))
                try:
                    with paid_scope(row["workspace_id"], subject, "embedding_migration"):
                        vector = (await self.providers.embed([item["source"]["text"]], retries=0))[0]
                        source = item["source"]
                        record = {"id": row["item_id"], "user_id": row["workspace_id"], "text": source["text"],
                                  "title": source["title"], "document_id": source.get("document_id", ""),
                                  "locator": source.get("locator", "历史研究摘要"), "thread_id": source.get("thread_id", ""),
                                  "index_version": source.get("index_version", 0), "vector": vector,
                                  "migration_source_hash": row["source_hash"]}
                        await self.vectors.upsert_shadow(row["kind"], [record], migration["target_prefix"])
                    await self.db.execute("UPDATE embedding_migration_items SET status='done',error_code='',updated_at=? WHERE migration_id=? AND kind=? AND item_id=?",
                                          (now(), migration_id, row["kind"], row["item_id"]))
                except ServiceError as error:
                    status = "unknown" if error.result_unknown else "failed"
                    await self.db.execute("UPDATE embedding_migration_items SET status=?,error_code=?,updated_at=? WHERE migration_id=? AND kind=? AND item_id=?",
                                          (status, error.code, now(), migration_id, row["kind"], row["item_id"]))
                    break
            await self.db.execute("UPDATE embedding_migrations SET status='building',updated_at=? WHERE id=?", (now(), migration_id))
        return await self.progress(migration_id)

    async def activate(self, migration_id, *, maintenance_acknowledged):
        if not maintenance_acknowledged:
            raise ServiceError("切换需要再次确认维护窗口", code="maintenance_required", retryable=False)
        async with self.db.guard("embedding-migration"):
            migration, items = await self._validate(migration_id)
            pending = await self.db.one("SELECT COUNT(*) AS total FROM embedding_migration_items WHERE migration_id=? AND status!='done'", (migration_id,))
            if pending["total"]:
                raise ServiceError("索引覆盖未完成，不能切换", code="migration_incomplete", retryable=False)
            await self.vectors.verify_shadow(items, migration["target_prefix"])
            async with frozen_sources(self.db) as conn:
                await assert_idle(conn)
                barrier = await read_one(conn, "SELECT value FROM metadata WHERE key='embedding_migration_barrier'")
                pending = await read_one(conn, "SELECT COUNT(*) AS total FROM embedding_migration_items WHERE migration_id=? AND status!='done'", (migration_id,))
                if not barrier or barrier["value"] != migration_id:
                    raise ServiceError("迁移维护屏障已失效", code="migration_barrier_lost", retryable=False)
                if pending["total"]:
                    raise ServiceError("索引覆盖未完成，不能切换", code="migration_incomplete", retryable=False)
                _, snapshot = await source_snapshot(conn)
                if snapshot != migration["source_snapshot"]:
                    raise ServiceError("源资料或权限已变化，不能切换", code="migration_source_changed", retryable=False)
                current = await read_one(conn, "SELECT value FROM metadata WHERE key='embedding_fingerprint'")
                if current and current["value"] != migration["source_fingerprint"]:
                    raise ServiceError("当前索引已被其他部署切换", code="embedding_source_mismatch", retryable=False)
                active = {**embedding_config(self.target_settings), "fingerprint": migration["target_fingerprint"],
                          "prefix": migration["target_prefix"], "migration_id": migration_id}
                for key, value in (("embedding_fingerprint", migration["target_fingerprint"]),
                                   ("embedding_active_config", json.dumps(active, sort_keys=True)),
                                   ("embedding_migration_barrier", "")):
                    await conn.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", (key, value))
                await conn.execute("UPDATE embedding_migrations SET status='activated',updated_at=?,activated_at=? WHERE id=?", (now(), now(), migration_id))
                await conn.commit()
        return await self.progress(migration_id)

    async def abort(self, migration_id):
        async with self.db.guard("embedding-migration"), self.db.connection() as conn:
            migration = await read_one(conn, "SELECT status FROM embedding_migrations WHERE id=?", (migration_id,))
            if not migration or migration["status"] == "activated":
                raise ServiceError("迁移不存在或已经生效，不能中止", code="migration_finished", retryable=False)
            await conn.execute("UPDATE metadata SET value='' WHERE key='embedding_migration_barrier' AND value=?", (migration_id,))
            await conn.execute("UPDATE embedding_migrations SET status='aborted',updated_at=? WHERE id=?", (now(), migration_id))
            await conn.commit()
        return await self.progress(migration_id)
