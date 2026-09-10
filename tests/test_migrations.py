import sqlite3

import pytest

from backend.core.db import Database


@pytest.mark.asyncio
async def test_sqlite_upgrade_backfills_personal_owners_from_workspace_creator(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE runs(
              id TEXT PRIMARY KEY,user_id TEXT NOT NULL,thread_id TEXT NOT NULL,topic TEXT,mode TEXT,
              status TEXT,created_at TEXT,updated_at TEXT,report TEXT DEFAULT '',sources TEXT DEFAULT '[]',
              validation TEXT DEFAULT '{}',error TEXT DEFAULT '',client_request_id TEXT,
              UNIQUE(user_id,client_request_id));
            CREATE TABLE memories(
              id TEXT PRIMARY KEY,user_id TEXT,kind TEXT,content TEXT,run_id TEXT DEFAULT '',created_at TEXT,
              vector TEXT DEFAULT '[]',UNIQUE(user_id,kind,run_id));
            CREATE TABLE workspaces(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL);
            INSERT INTO workspaces VALUES('shared','Shared','alice','today');
            INSERT INTO runs(id,user_id,thread_id,topic,mode,status,created_at,updated_at,client_request_id)
              VALUES('run','shared','thread','topic','quick','completed','today','today','request');
            INSERT INTO memories(id,user_id,kind,content,run_id,created_at)
              VALUES('preference','shared','preference','legacy preference','preference','today');
        """)

    db = Database(path)
    await db.init()

    assert (await db.one("SELECT created_by FROM runs WHERE id='run'"))["created_by"] == "alice"
    assert (await db.one("SELECT owner_subject FROM memories WHERE id='preference'"))["owner_subject"] == "alice"
