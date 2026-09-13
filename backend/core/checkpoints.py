"""Fence persisted graph writes using the same lock as execution claiming."""
from langgraph.checkpoint.base import BaseCheckpointSaver
from .reliability import execution


class FencedCheckpointer(BaseCheckpointSaver):
    def __init__(self, inner):
        super().__init__(serde=inner.serde)
        self.inner = inner

    async def aget_tuple(self, config):
        return await self.inner.aget_tuple(config)

    async def alist(self, config, **kwargs):
        async for item in self.inner.alist(config, **kwargs):
            yield item

    async def _write(self, method, *args, **kwargs):
        ctx = execution.get()
        if not ctx:
            return await method(*args, **kwargs)
        async with ctx.db.guard("execution:" + ctx.run_id):
            await ctx.check()
            return await method(*args, **kwargs)

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await self._write(self.inner.aput, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await self._write(self.inner.aput_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id):
        return await self.inner.adelete_thread(thread_id)

    def get_next_version(self, current, channel):
        return self.inner.get_next_version(current, channel)


class FencedDatabase:
    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def execute(self, *args, **kwargs):
        ctx = execution.get()
        if not ctx:
            return await self.inner.execute(*args, **kwargs)
        async with self.inner.guard("execution:" + ctx.run_id):
            await ctx.check()
            return await self.inner.execute(*args, **kwargs)

    async def event(self, run_id, event_type, data):
        import json
        from .db import now
        await self.execute("INSERT INTO events(run_id,type,data,created_at) VALUES(?,?,?,?)",
                           (run_id, event_type, json.dumps(data, ensure_ascii=False), now()))
