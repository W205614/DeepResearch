"""Small shared helpers; each test keeps its own runtime and assertions."""
import httpx

from backend.core.config import Settings
from backend.core.db import Database
from backend.infrastructure.providers import Providers


async def finish(rt, run):
    task = rt.tasks.get(run["id"])
    if task:
        await task
    return await rt.db.owned_run(run["id"], run["user_id"])


async def make_provider(tmp_path, response):
    db = Database(tmp_path / 'test.sqlite3')
    await db.init()
    settings = Settings(_env_file=None, llm_api_key='test-key', embedding_api_key='test-key', embedding_dimension=3)
    client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    return Providers(settings, db, client)
