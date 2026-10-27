"""The PostgreSQL delete path must work with SELECT/DELETE-only thread grants."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.services.runtime import Runtime


class DeleteOnlyTransaction:
    def __init__(self):
        self.thread_exists = True
        self.committed = False
        self.rolled_back = False

    async def execute(self, sql, args=()):
        if 'FOR UPDATE' in sql and 'threads' in sql:
            raise PermissionError('dr_python has no UPDATE privilege on threads')
        if sql.startswith('DELETE FROM threads'):
            assert args == ('thread-id', 'workspace')
            self.thread_exists = False
        elif sql.startswith('DELETE FROM runs'):
            assert not self.thread_exists

    async def rows(self, sql, args=()):
        assert not self.thread_exists
        # A Java admission that committed before the delete lock was acquired
        # must be included in the fresh transaction-local run set.
        return [{'id': 'existing-run'}, {'id': 'admitted-before-delete'}]

    async def commit(self):
        self.committed = True


def configured_runtime():
    runtime = object.__new__(Runtime)
    transaction = DeleteOnlyTransaction()

    @asynccontextmanager
    async def connection():
        try:
            yield transaction
        except BaseException:
            transaction.thread_exists = True
            transaction.rolled_back = True
            raise

    @asynccontextmanager
    async def guards(conn, run_ids):
        assert conn is transaction
        assert run_ids == ['existing-run', 'admitted-before-delete']
        yield

    runtime.settings = SimpleNamespace(database_url='postgresql://isolated-not-connected')
    runtime.lock = asyncio.Lock()
    runtime.tasks = {}
    runtime.find_thread = AsyncMock(return_value={'id': 'thread-id', 'thread_key': 'thread01'})
    runtime.db = SimpleNamespace(connection=connection,
        one=AsyncMock(side_effect=[None, None, {'total': 0}]),
        rows=AsyncMock(return_value=[{'id': 'existing-run', 'status': 'completed'}]))
    runtime._delete_execution_guards = guards
    runtime._delete_run_records = AsyncMock()
    runtime.cleanup_runs = AsyncMock()
    return runtime, transaction


async def test_thread_delete_uses_existing_privilege_and_latest_admitted_runs():
    runtime, transaction = configured_runtime()
    result = await runtime.delete_thread('thread01', 'workspace')
    assert result['deleted_runs'] == 2 and result['thread_id'] == 'thread01'
    assert transaction.committed and not transaction.thread_exists
    assert [call.args[1:] for call in runtime._delete_run_records.await_args_list] == [
        ('existing-run', 'workspace'), ('admitted-before-delete', 'workspace')]


async def test_cleanup_failure_rolls_back_early_thread_delete():
    runtime, transaction = configured_runtime()
    runtime._delete_run_records.side_effect = RuntimeError('cleanup staging failed')
    with pytest.raises(RuntimeError, match='cleanup staging failed'):
        await runtime.delete_thread('thread01', 'workspace')
    assert transaction.thread_exists and transaction.rolled_back
    assert not transaction.committed
    runtime.cleanup_runs.assert_not_awaited()
