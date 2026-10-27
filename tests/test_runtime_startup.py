from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from backend.services.runtime import Runtime


@pytest.mark.asyncio
@pytest.mark.parametrize('managed',[False,True])
async def test_checkpoint_connection_opens_inside_database_guard(monkeypatch,managed):
    events = []

    class FakeDatabase:
        @asynccontextmanager
        async def migration_guard(self, key):
            events.append(("lock", key))
            yield
            events.append(("unlock", key))

    class FakeCheckpointer:
        async def setup(self):
            events.append(("setup", None))

    class FakeStack:
        async def enter_async_context(self, context):
            events.append(("open", context))
            return FakeCheckpointer()

    runtime = object.__new__(Runtime)
    runtime.settings = SimpleNamespace(checkpoint_schema_managed=managed)
    runtime.db = FakeDatabase()
    runtime.stack = FakeStack()
    monkeypatch.setattr(
        "backend.services.runtime.AsyncPostgresSaver.from_conn_string",
        lambda url: ("checkpointer", url),
    )

    await runtime._open_postgres_checkpointer("postgresql://database/checkpoints")

    assert events == ([("open", ("checkpointer", "postgresql://database/checkpoints"))] if managed else [
        ("lock", "langgraph-checkpoint-schema"),
        ("open", ("checkpointer", "postgresql://database/checkpoints")),
        ("setup", None),
        ("unlock", "langgraph-checkpoint-schema"),
    ])
