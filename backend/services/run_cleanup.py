"""Committed deletion intents separate transactional SQL from external cleanup.

Runtime owns authorization, admission/thread locks, cancellation and COMMIT.
This service can stage SQL only on its caller's transaction. Reconciliation reads
committed intents and owns idempotent checkpoint/object cleanup afterwards.
"""
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
import hashlib
from typing import Any, Protocol

from ..core.db import now


class SqlWriter(Protocol):
    # Deliberately no commit: stage() cannot choose a transaction boundary.
    async def execute(self, sql: str, args: tuple = ()) -> Any: ...


class CleanupDatabase(Protocol):
    async def rows(self, sql: str, args: tuple = ()) -> list[dict]: ...
    async def one(self, sql: str, args: tuple = ()) -> dict | None: ...
    async def execute(self, sql: str, args: tuple = ()) -> Any: ...
    def guard(self, key: str) -> AbstractAsyncContextManager: ...


class DeleteCheckpoints(Protocol):
    async def adelete_thread(self, thread_id: str) -> None: ...


class DeleteObjects(Protocol):
    async def delete(self, area: str, object_key: str) -> None: ...


@dataclass(frozen=True)
class RunDeletion:
    run_id: str
    workspace_id: str

    def __post_init__(self):
        if not self.run_id or not self.workspace_id:
            raise ValueError("Deletion requires a persisted run and workspace")


@dataclass(frozen=True)
class CleanupResult:
    examined: int = 0
    cleaned: int = 0
    live_run_deferred: int = 0
    already_cleaned: int = 0
    failed: int = 0


async def _transaction_row(transaction: SqlWriter, sql: str, args: tuple) -> dict | None:
    # PostgreSQL exposes transaction-local rows; SQLite exposes its own cursor.
    # Neither branch borrows another pool connection or reads uncommitted state
    # through a different connection.
    rows = getattr(transaction, "rows", None)
    if rows:
        values = await rows(sql, args)
        return values[0] if values else None
    cursor = await transaction.execute(sql, args)
    try:
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await cursor.close()


class RunCleanup:
    def __init__(self, db: CleanupDatabase, checkpoint: Callable[[], DeleteCheckpoints],
                 objects: DeleteObjects, *, postgres: bool):
        self.db, self.checkpoint, self.objects, self.postgres = db, checkpoint, objects, postgres

    async def stage(self, transaction: SqlWriter, target: RunDeletion) -> bool:
        """Same transaction as run deletion; never commits or calls external I/O."""
        owner = await _transaction_row(transaction, "SELECT user_id FROM runs WHERE id=?", (target.run_id,))
        if owner is None:
            return False  # Another completed deletion is already authoritative.
        if owner["user_id"] != target.workspace_id:
            raise PermissionError("Deletion scope differs from the persisted run owner")
        intent = await _transaction_row(transaction, "SELECT workspace_id FROM run_cleanup WHERE run_id=?", (target.run_id,))
        if intent and intent["workspace_id"] != target.workspace_id:
            raise PermissionError("An existing deletion intent belongs to another workspace")
        await transaction.execute("""INSERT OR IGNORE INTO run_cleanup(run_id,workspace_id,created_at)
            VALUES(?,?,?)""", (target.run_id, target.workspace_id, now()))
        await transaction.execute("UPDATE attachments SET status='deleted' WHERE run_id=? AND user_id=?",
                                  (target.run_id, target.workspace_id))
        for table in ("run_vision", "events", "counters", "search_cache", "verified_claims", "call_attempts",
                      "run_artifacts", "dead_letter_runs"):
            await transaction.execute(f"DELETE FROM {table} WHERE run_id=?", (target.run_id,))
        await transaction.execute("DELETE FROM retention_archives WHERE kind='research' AND source_id=? AND workspace_id=?",
                                  (target.run_id, target.workspace_id))
        return True

    @asynccontextmanager
    async def execution_guards(self, transaction: SqlWriter, run_ids: Iterable[str]) -> AsyncIterator[None]:
        ids = sorted(set(run_ids))
        if self.postgres:
            # All transaction guards share one connection even for long histories.
            for run_id in ids:
                lock_id = int.from_bytes(hashlib.sha256(('execution:' + run_id).encode()).digest()[:8], 'big', signed=True)
                await transaction.execute('SELECT pg_advisory_xact_lock(?)', (lock_id,))
            yield
        else:
            async with AsyncExitStack() as locks:
                for run_id in ids:
                    await locks.enter_async_context(self.db.guard('execution:' + run_id))
                yield

    async def reconcile(self, *, batch: int) -> CleanupResult:
        """Only durable, committed intents may trigger irreversible external I/O."""
        if not 1 <= batch <= 1000:
            raise ValueError("Cleanup batch must be bounded to 1..1000")
        rows = await self.db.rows("SELECT run_id,workspace_id FROM run_cleanup ORDER BY created_at,run_id LIMIT ?", (batch,))
        cleaned = deferred = absent = failed = 0
        for row in rows:
            target = RunDeletion(row["run_id"], row["workspace_id"])
            try:
                async with self.db.guard("execution:" + target.run_id):
                    # Other runtime loops can have selected this intent before
                    # locking. Reread it to avoid repeating external operations.
                    if not await self.db.one("SELECT run_id FROM run_cleanup WHERE run_id=? AND workspace_id=?",
                                             (target.run_id, target.workspace_id)):
                        absent += 1
                        continue
                    if await self.db.one("SELECT id FROM runs WHERE id=?", (target.run_id,)):
                        deferred += 1
                        continue  # A recreated/live run always protects its data.
                    await self.checkpoint().adelete_thread(target.run_id)
                    attachments = await self.db.rows("""SELECT id FROM attachments
                        WHERE run_id=? AND user_id=? AND status='deleted'""", (target.run_id, target.workspace_id))
                    for attachment in attachments:
                        for area in ("uploads", "quarantine"):
                            await self.objects.delete(area, attachment["id"])
                        await self.db.execute("DELETE FROM attachments WHERE id=? AND run_id=? AND status='deleted'",
                                              (attachment["id"], target.run_id))
                    await self.db.execute("DELETE FROM run_cleanup WHERE run_id=? AND workspace_id=?",
                                          (target.run_id, target.workspace_id))
                    cleaned += 1
            except Exception:
                failed += 1
                await self.db.execute("""UPDATE run_cleanup SET attempts=attempts+1,error_code='external_cleanup_pending'
                    WHERE run_id=? AND workspace_id=?""", (target.run_id, target.workspace_id))
        return CleanupResult(len(rows), cleaned, deferred, absent, failed)
