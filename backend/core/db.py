"""SQLite is the source of truth; vectors are replaceable indexes."""
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import aiosqlite


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return uuid4().hex


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,title TEXT,created_at TEXT);
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL,thread_id TEXT NOT NULL,topic TEXT,mode TEXT,
 status TEXT,created_at TEXT,updated_at TEXT,report TEXT DEFAULT '',sources TEXT DEFAULT '[]',
 validation TEXT DEFAULT '{}',error TEXT DEFAULT '',client_request_id TEXT,
 created_by TEXT NOT NULL DEFAULT '',
 UNIQUE(user_id,client_request_id));
CREATE UNIQUE INDEX IF NOT EXISTS one_active_thread ON runs(thread_id)
 WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,type TEXT,data TEXT,created_at TEXT);
CREATE INDEX IF NOT EXISTS event_run ON events(run_id,id);
CREATE TABLE IF NOT EXISTS documents(
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT,hash TEXT,status TEXT,error TEXT DEFAULT '',
 created_at TEXT,UNIQUE(user_id,hash));
CREATE TABLE IF NOT EXISTS chunks(
 id TEXT PRIMARY KEY,document_id TEXT,user_id TEXT,text TEXT,locator TEXT,vector TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS memories(
 id TEXT PRIMARY KEY,user_id TEXT,kind TEXT,content TEXT,run_id TEXT DEFAULT '',created_at TEXT,
 vector TEXT DEFAULT '[]',owner_subject TEXT NOT NULL DEFAULT '',UNIQUE(user_id,kind,run_id));
CREATE TABLE IF NOT EXISTS counters(
 run_id TEXT PRIMARY KEY,search_calls INTEGER DEFAULT 0,llm_calls INTEGER DEFAULT 0,
 prompt_tokens INTEGER DEFAULT 0,completion_tokens INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS search_cache(
 run_id TEXT,query TEXT,result TEXT,PRIMARY KEY(run_id,query));
CREATE TABLE IF NOT EXISTS web_cache(
 url TEXT PRIMARY KEY,text TEXT NOT NULL,fetched_at TEXT NOT NULL,access TEXT NOT NULL,error TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS document_search_cache(
 user_id TEXT NOT NULL,corpus_hash TEXT NOT NULL,query_hash TEXT NOT NULL,limit_value INTEGER NOT NULL,
 result TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(user_id,corpus_hash,query_hash,limit_value));
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memberships(workspace_id TEXT NOT NULL,subject TEXT NOT NULL,role TEXT NOT NULL,
 created_at TEXT NOT NULL,PRIMARY KEY(workspace_id,subject),CHECK(role IN ('admin','researcher','viewer')));
CREATE TABLE IF NOT EXISTS workspace_limits(workspace_id TEXT PRIMARY KEY,daily_search_limit INTEGER NOT NULL,
 daily_token_limit INTEGER NOT NULL,concurrent_run_limit INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS dead_letter_runs(run_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,category TEXT NOT NULL,message TEXT NOT NULL,failed_at TEXT NOT NULL,recovered_at TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS dead_letter_user ON dead_letter_runs(user_id,failed_at DESC);
CREATE TABLE IF NOT EXISTS audit_logs(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,actor_subject TEXT NOT NULL,
 action TEXT NOT NULL,target_type TEXT NOT NULL,target_id TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS audit_workspace ON audit_logs(workspace_id,created_at DESC);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    @asynccontextmanager
    async def connection(self):
        async with aiosqlite.connect(self.path, timeout=30) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA busy_timeout=30000")
            yield conn

    async def init(self):
        async with self.connection() as conn:
            await conn.executescript(SCHEMA)
            columns = {row[1] for row in await (await conn.execute("PRAGMA table_info(threads)")).fetchall()}
            run_columns = {row[1] for row in await (await conn.execute("PRAGMA table_info(runs)")).fetchall()}
            memory_columns = {row[1] for row in await (await conn.execute("PRAGMA table_info(memories)")).fetchall()}
            if "attempt_count" not in run_columns:
                await conn.execute("ALTER TABLE runs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0")
                await conn.execute("ALTER TABLE runs ADD COLUMN last_attempt_at TEXT DEFAULT ''")
            if "thread_key" not in columns:
                await conn.execute("ALTER TABLE threads ADD COLUMN thread_key TEXT")
            if "created_by" not in run_columns:
                await conn.execute("ALTER TABLE runs ADD COLUMN created_by TEXT NOT NULL DEFAULT ''")
            if "owner_subject" not in memory_columns:
                await conn.execute("ALTER TABLE memories ADD COLUMN owner_subject TEXT NOT NULL DEFAULT ''")
            await conn.execute("""UPDATE runs SET created_by=COALESCE(
                (SELECT created_by FROM workspaces WHERE id=runs.user_id),user_id)
                WHERE created_by=''""")
            await conn.execute("""UPDATE memories SET owner_subject=COALESCE(
                (SELECT created_by FROM workspaces WHERE id=memories.user_id),user_id)
                WHERE owner_subject='' AND kind IN ('profile','preference')""")
            await conn.execute("CREATE INDEX IF NOT EXISTS personal_memory_owner ON memories(owner_subject,kind,created_at)")
            rows = await (await conn.execute(
                "SELECT id,user_id FROM threads WHERE thread_key IS NULL OR thread_key='' ORDER BY created_at,id")).fetchall()
            assigned: dict[str, set[str]] = {}
            for row in rows:
                user = row["user_id"]
                if user not in assigned:
                    current = await (await conn.execute(
                        "SELECT thread_key FROM threads WHERE user_id=? AND thread_key IS NOT NULL", (user,))).fetchall()
                    assigned[user] = {item["thread_key"] for item in current}
                index = 1
                while f"thread{index:02d}" in assigned[user]:
                    index += 1
                thread_key = f"thread{index:02d}"
                assigned[user].add(thread_key)
                await conn.execute("UPDATE threads SET thread_key=? WHERE id=?", (thread_key, row["id"]))
            await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_thread_key_per_user ON threads(user_id,thread_key)")
            await conn.execute("UPDATE runs SET status='interrupted' WHERE status IN ('running','queued')")
            await conn.commit()

    async def execute(self, sql: str, args=()):
        async with self.connection() as conn:
            cur = await conn.execute(sql, args)
            await conn.commit()
            return cur.rowcount

    async def rows(self, sql: str, args=()) -> list[dict]:
        async with self.connection() as conn:
            cur = await conn.execute(sql, args)
            return [dict(row) for row in await cur.fetchall()]

    async def one(self, sql: str, args=()) -> dict | None:
        rows = await self.rows(sql, args)
        return rows[0] if rows else None

    async def event(self, run_id: str, event_type: str, data: dict):
        await self.execute("INSERT INTO events(run_id,type,data,created_at) VALUES(?,?,?,?)",
                           (run_id, event_type, json.dumps(data, ensure_ascii=False), now()))

    async def usage(self, run_id: str, prompt=0, completion=0):
        if not run_id:
            return
        await self.execute("""INSERT INTO counters(run_id,llm_calls,prompt_tokens,completion_tokens)
            VALUES(?,1,?,?) ON CONFLICT(run_id) DO UPDATE SET llm_calls=llm_calls+1,
            prompt_tokens=prompt_tokens+excluded.prompt_tokens,
            completion_tokens=completion_tokens+excluded.completion_tokens""", (run_id, prompt, completion))

    async def reserve_search(self, run_id: str, limit: int) -> bool:
        async with self.connection() as conn:
            await conn.execute("INSERT OR IGNORE INTO counters(run_id) VALUES(?)", (run_id,))
            cur = await conn.execute("UPDATE counters SET search_calls=search_calls+1 WHERE run_id=? AND search_calls<?",
                                     (run_id, limit))
            await conn.commit()
            return cur.rowcount == 1

    async def owned_run(self, run_id: str, user: str) -> dict | None:
        row = await self.one("SELECT * FROM runs WHERE id=? AND user_id=?", (run_id, user))
        if row:
            row["sources"] = json.loads(row["sources"])
            row["validation"] = json.loads(row["validation"])
            row["usage"] = await self.one("SELECT * FROM counters WHERE run_id=?", (run_id,)) or {}
        return row
    async def create_workspace(self, subject: str, name: str, limits: tuple[int, int], workspace_id: str | None = None) -> dict:
        workspace = {"id": workspace_id or uid(), "name": name.strip()[:100], "created_by": subject, "created_at": now()}
        async with self.connection() as conn:
            await conn.execute("INSERT INTO workspaces(id,name,created_by,created_at) VALUES(?,?,?,?)",
                               tuple(workspace.values()))
            await conn.execute("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES(?,?,?,?)",
                               (workspace["id"], subject, "admin", workspace["created_at"]))
            await conn.execute("""INSERT INTO workspace_limits(workspace_id,daily_search_limit,daily_token_limit,concurrent_run_limit)
                               VALUES(?,?,0,?)""", (workspace["id"], *limits))
            await conn.commit()
        return workspace

    async def ensure_personal_workspace(self, subject: str, name: str, limits: tuple[int, int]) -> dict:
        """Idempotently bootstrap the subject's default workspace."""
        workspace = {"id": subject, "name": name.strip()[:100], "created_by": subject, "created_at": now()}
        async with self.connection() as conn:
            await conn.execute("INSERT OR IGNORE INTO workspaces(id,name,created_by,created_at) VALUES(?,?,?,?)",
                               tuple(workspace.values()))
            await conn.execute("INSERT OR IGNORE INTO memberships(workspace_id,subject,role,created_at) VALUES(?,?,?,?)",
                               (subject, subject, "admin", workspace["created_at"]))
            await conn.execute("""INSERT OR IGNORE INTO workspace_limits(
                workspace_id,daily_search_limit,daily_token_limit,concurrent_run_limit) VALUES(?,?,0,?)""",
                (subject, *limits))
            await conn.commit()
        return await self.membership(subject, subject)

    async def memberships(self, subject: str) -> list[dict]:
        return await self.rows("""SELECT w.id,w.name,m.role,w.created_at FROM workspaces w JOIN memberships m
                               ON w.id=m.workspace_id WHERE m.subject=? ORDER BY w.created_at""", (subject,))

    async def membership(self, workspace_id: str, subject: str) -> dict | None:
        return await self.one("""SELECT w.id,w.name,m.role FROM workspaces w JOIN memberships m ON w.id=m.workspace_id
                              WHERE w.id=? AND m.subject=?""", (workspace_id, subject))

    async def upsert_membership(self, workspace_id: str, subject: str, role: str) -> None:
        await self.execute("""INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES(?,?,?,?)
                           ON CONFLICT(workspace_id,subject) DO UPDATE SET role=excluded.role""",
                           (workspace_id, subject, role, now()))

    async def audit(self, workspace_id: str, actor: str, action: str, target_type: str,
                    target_id: str, result: str = "success") -> None:
        await self.execute("""INSERT INTO audit_logs(id,workspace_id,actor_subject,action,target_type,target_id,result,created_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                           (uid(), workspace_id, actor, action, target_type, target_id, result, now()))

    async def audit_rows(self, workspace_id: str, limit: int = 100) -> list[dict]:
        return await self.rows("""SELECT action,target_type,target_id,result,actor_subject,created_at FROM audit_logs
                               WHERE workspace_id=? ORDER BY created_at DESC LIMIT ?""", (workspace_id, limit))

    async def workspace_limits(self, workspace_id: str) -> dict | None:
        return await self.one("SELECT daily_search_limit,concurrent_run_limit FROM workspace_limits WHERE workspace_id=?", (workspace_id,))
