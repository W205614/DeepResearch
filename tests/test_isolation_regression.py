import json

import pytest

from backend.core.db import Database
from backend.infrastructure.vectors import VectorIndex


@pytest.mark.asyncio
async def test_demo_vector_retrieval_cannot_cross_user_documents(settings, tmp_path):
    db = Database(tmp_path / "vectors.sqlite3")
    await db.init()
    await db.execute("INSERT INTO documents(id,user_id,name,hash,status,created_at) VALUES(?,?,?,?,?,?)", ("a-doc", "alice", "A", "a", "ready", "now"))
    await db.execute("INSERT INTO documents(id,user_id,name,hash,status,created_at) VALUES(?,?,?,?,?,?)", ("b-doc", "bob", "B", "b", "ready", "now"))
    await db.execute("INSERT INTO chunks(id,document_id,user_id,text,locator,vector) VALUES(?,?,?,?,?,?)", ("a", "a-doc", "alice", "alice only", "1", json.dumps([1.0, 0.0])))
    await db.execute("INSERT INTO chunks(id,document_id,user_id,text,locator,vector) VALUES(?,?,?,?,?,?)", ("b", "b-doc", "bob", "bob only", "1", json.dumps([1.0, 0.0])))
    hits = await VectorIndex(settings, db).search("documents", "alice", [1.0, 0.0])
    assert [hit["id"] for hit in hits] == ["a"]


@pytest.mark.asyncio
async def test_demo_semantic_memory_retrieval_is_thread_scoped(settings, tmp_path):
    db = Database(tmp_path / "memories.sqlite3")
    await db.init()
    for run_id, thread_id in (("r1", "thread-a"), ("r2", "thread-b")):
        await db.execute("INSERT INTO runs(id,user_id,thread_id,topic,mode,status,created_at,updated_at,client_request_id) VALUES(?,?,?,?,?,?,?,?,?)", (run_id, "alice", thread_id, "topic", "quick", "completed", "now", "now", run_id))
    await db.execute("INSERT INTO memories(id,user_id,kind,content,run_id,created_at,vector) VALUES(?,?,?,?,?,?,?)", ("m1", "alice", "semantic", "thread a", "r1", "now", json.dumps([1.0, 0.0])))
    await db.execute("INSERT INTO memories(id,user_id,kind,content,run_id,created_at,vector) VALUES(?,?,?,?,?,?,?)", ("m2", "alice", "semantic", "thread b", "r2", "now", json.dumps([1.0, 0.0])))
    hits = await VectorIndex(settings, db).search("memories", "alice", [1.0, 0.0], thread_id="thread-a")
    assert [hit["id"] for hit in hits] == ["m1"]
