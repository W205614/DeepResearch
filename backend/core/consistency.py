"""Shared persistence invariants; no external service calls inside transactions."""
import asyncio
import weakref
from contextlib import asynccontextmanager

DAILY_SCHEMA = """CREATE TABLE IF NOT EXISTS workspace_daily_usage(
 workspace_id TEXT NOT NULL,day TEXT NOT NULL,search_calls INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(workspace_id,day));
CREATE TABLE IF NOT EXISTS document_cleanup(
 document_id TEXT NOT NULL,user_id TEXT NOT NULL,version INTEGER NOT NULL,
 PRIMARY KEY(document_id,version));"""

_locks = weakref.WeakValueDictionary()


@asynccontextmanager
async def local_guard(key):
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        yield


def affected(result):
    return result.rowcount if hasattr(result, "rowcount") else int(result.split()[-1])
