"""Small shared helpers; each test keeps its own runtime and assertions."""
import httpx

from backend.core.config import Settings
from backend.core.db import Database
from backend.infrastructure.providers import Providers
from backend.core.usage_governance import paid_scope


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
    class GovernedFixtureProviders(Providers):
        async def post(self, *args, **kwargs):
            # Mock transport tests have an explicit offline accounting identity;
            # production adapters continue to reject missing identities.
            with paid_scope("provider-fixture", "fixture-operator", "evaluation"):
                return await super().post(*args, **kwargs)
    return GovernedFixtureProviders(settings, db, client)
