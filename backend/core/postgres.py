"""PostgreSQL source-of-truth adapter for the enterprise Compose profile."""
from contextlib import asynccontextmanager
import json
import re

import asyncpg

from .db import now, uid


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,title TEXT,created_at TEXT,thread_key TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_thread_key_per_user ON threads(user_id,thread_key);
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL,thread_id TEXT NOT NULL,topic TEXT,mode TEXT,status TEXT,
 created_at TEXT,updated_at TEXT,report TEXT DEFAULT '',sources TEXT DEFAULT '[]',validation TEXT DEFAULT '{}',
 error TEXT DEFAULT '',client_request_id TEXT,attempt_count INTEGER NOT NULL DEFAULT 0,last_attempt_at TEXT DEFAULT '',
 UNIQUE(user_id,client_request_id));
CREATE UNIQUE INDEX IF NOT EXISTS one_active_thread ON runs(thread_id) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS events(id BIGSERIAL PRIMARY KEY,run_id TEXT NOT NULL,type TEXT,data TEXT,created_at TEXT);
CREATE INDEX IF NOT EXISTS event_run ON events(run_id,id);
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT,hash TEXT,status TEXT,error TEXT DEFAULT '',created_at TEXT,UNIQUE(user_id,hash));
CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,document_id TEXT,user_id TEXT,text TEXT,locator TEXT,vector TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,user_id TEXT,kind TEXT,content TEXT,run_id TEXT DEFAULT '',created_at TEXT,vector TEXT DEFAULT '[]',UNIQUE(user_id,kind,run_id));
CREATE TABLE IF NOT EXISTS counters(run_id TEXT PRIMARY KEY,search_calls INTEGER DEFAULT 0,llm_calls INTEGER DEFAULT 0,prompt_tokens INTEGER DEFAULT 0,completion_tokens INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS search_cache(run_id TEXT,query TEXT,result TEXT,PRIMARY KEY(run_id,query));
CREATE TABLE IF NOT EXISTS web_cache(url TEXT PRIMARY KEY,text TEXT NOT NULL,fetched_at TEXT NOT NULL,access TEXT NOT NULL,error TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memberships(workspace_id TEXT NOT NULL,subject TEXT NOT NULL,role TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(workspace_id,subject),CHECK(role IN ('admin','researcher','viewer')));
CREATE TABLE IF NOT EXISTS workspace_limits(workspace_id TEXT PRIMARY KEY,daily_search_limit INTEGER NOT NULL,daily_token_limit INTEGER NOT NULL,concurrent_run_limit INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS audit_logs(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,actor_subject TEXT NOT NULL,action TEXT NOT NULL,target_type TEXT NOT NULL,target_id TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS audit_workspace ON audit_logs(workspace_id,created_at DESC);
"""


class _Connection:
    def __init__(self, db, connection):
        self.db, self.connection = db, connection

    async def execute(self, sql, args=()):
        return await self.db._execute(self.connection, sql, args)

    async def executemany(self, sql, rows):
        await self.connection.executemany(self.db._sql(sql), rows)

    async def commit(self):
        return None


class PostgresDatabase:
    def __init__(self, dsn: str):
        self.dsn, self.pool = dsn, None

    def _sql(self, sql: str) -> str:
        ignore_conflict = "INSERT OR IGNORE INTO" in sql
        sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        replacements = {"search_cache": "run_id,query", "web_cache": "url", "metadata": "key", "memories": "id"}
        match = re.match(r"\s*INSERT OR REPLACE INTO (\w+)\(([^)]+)\) VALUES\(([^)]+)\)", sql, re.S)
        if match:
            table, columns, values = match.groups()
            keys = replacements[table].split(",")
            fields = [field.strip() for field in columns.split(",")]
            updates = ",".join(f"{field}=EXCLUDED.{field}" for field in fields if field not in keys)
            sql = f"INSERT INTO {table}({columns}) VALUES({values}) ON CONFLICT({','.join(keys)}) DO UPDATE SET {updates}"
        elif ignore_conflict:
            sql += " ON CONFLICT DO NOTHING"
        index = 0
        def placeholder(_):
            nonlocal index
            index += 1
            return f"${index}"
        return re.sub(r"\?", placeholder, sql)

    async def init(self):
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=8)
        async with self.pool.acquire() as conn:
            await conn.execute(POSTGRES_SCHEMA)

    @asynccontextmanager
    async def connection(self):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                yield _Connection(self, conn)

    async def _execute(self, conn, sql, args=()):
        return await conn.execute(self._sql(sql), *args)

    async def execute(self, sql, args=()):
        async with self.connection() as conn:
            result = await conn.execute(sql, args)
        match = re.search(r"(\d+)$", result or "")
        return int(match.group(1)) if match else 0

    async def rows(self, sql, args=()):
        async with self.pool.acquire() as conn:
            records = await conn.fetch(self._sql(sql), *args)
        return [dict(row) for row in records]

    async def one(self, sql, args=()):
        rows = await self.rows(sql, args)
        return rows[0] if rows else None

    async def event(self, run_id, event_type, data):
        await self.execute("INSERT INTO events(run_id,type,data,created_at) VALUES(?,?,?,?)",
                           (run_id, event_type, json.dumps(data, ensure_ascii=False), now()))

    async def usage(self, run_id, prompt=0, completion=0):
        if run_id:
            await self.execute("""INSERT INTO counters(run_id,llm_calls,prompt_tokens,completion_tokens) VALUES(?,1,?,?)
                               ON CONFLICT(run_id) DO UPDATE SET llm_calls=counters.llm_calls+1,
                               prompt_tokens=counters.prompt_tokens+EXCLUDED.prompt_tokens,
                               completion_tokens=counters.completion_tokens+EXCLUDED.completion_tokens""",
                               (run_id, prompt, completion))

    async def reserve_search(self, run_id, limit):
        async with self.connection() as conn:
            await conn.execute("INSERT INTO counters(run_id) VALUES(?) ON CONFLICT DO NOTHING", (run_id,))
            result = await conn.execute("UPDATE counters SET search_calls=search_calls+1 WHERE run_id=? AND search_calls<?",
                                        (run_id, limit))
        return result.endswith("1")

    async def owned_run(self, run_id, user):
        row = await self.one("SELECT * FROM runs WHERE id=? AND user_id=?", (run_id, user))
        if row:
            row["sources"] = json.loads(row["sources"])
            row["validation"] = json.loads(row["validation"])
            row["usage"] = await self.one("SELECT * FROM counters WHERE run_id=?", (run_id,)) or {}
        return row

    async def create_workspace(self, subject, name, limits, workspace_id=None):
        workspace = {"id": workspace_id or uid(), "name": name.strip()[:100], "created_by": subject, "created_at": now()}
        async with self.connection() as conn:
            await conn.execute("INSERT INTO workspaces(id,name,created_by,created_at) VALUES(?,?,?,?)", tuple(workspace.values()))
            await conn.execute("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES(?,?,?,?)",
                               (workspace["id"], subject, "admin", workspace["created_at"]))
            await conn.execute("INSERT INTO workspace_limits(workspace_id,daily_search_limit,daily_token_limit,concurrent_run_limit) VALUES(?,?,?,?)",
                               (workspace["id"], *limits))
        return workspace

    async def memberships(self, subject):
        return await self.rows("SELECT w.id,w.name,m.role,w.created_at FROM workspaces w JOIN memberships m ON w.id=m.workspace_id WHERE m.subject=? ORDER BY w.created_at", (subject,))

    async def membership(self, workspace_id, subject):
        return await self.one("SELECT w.id,w.name,m.role FROM workspaces w JOIN memberships m ON w.id=m.workspace_id WHERE w.id=? AND m.subject=?", (workspace_id, subject))

    async def upsert_membership(self, workspace_id, subject, role):
        await self.execute("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES(?,?,?,?) ON CONFLICT(workspace_id,subject) DO UPDATE SET role=EXCLUDED.role", (workspace_id, subject, role, now()))

    async def audit(self, workspace_id, actor, action, target_type, target_id, result="success"):
        await self.execute("INSERT INTO audit_logs(id,workspace_id,actor_subject,action,target_type,target_id,result,created_at) VALUES(?,?,?,?,?,?,?,?)", (uid(), workspace_id, actor, action, target_type, target_id, result, now()))

    async def audit_rows(self, workspace_id, limit=100):
        return await self.rows("SELECT action,target_type,target_id,result,actor_subject,created_at FROM audit_logs WHERE workspace_id=? ORDER BY created_at DESC LIMIT ?", (workspace_id, limit))

    async def workspace_limits(self, workspace_id):
        return await self.one("SELECT * FROM workspace_limits WHERE workspace_id=?", (workspace_id,))

    async def close(self):
        if self.pool:
            await self.pool.close()
