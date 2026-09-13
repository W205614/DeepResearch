"""Explicit test entrypoints. Never imported by production application code."""
import asyncio
from backend.worker import DocumentWorkerSettings as DocumentWorkerSettings
from backend.worker import WorkerSettings as ProductionWorkerSettings
from backend.infrastructure.providers import Providers
from backend.infrastructure.demo import generate, embed, search
from backend.research.graph import ResearchGraph


def install():
    async def structured(self, role, instruction, data, schema, run_id, **kwargs):
        if role == 'planner' and str(data.get('topic', '')).startswith('slow-recovery'):
            first = await self.db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES(?,?)", ('slow:' + run_id, '1'))
            if first:
                await asyncio.sleep(40)
        await asyncio.sleep(.05)
        await self.db.usage(run_id, 10, 5)
        return schema.model_validate(generate(role, data))

    async def embeddings(self, texts):
        return [embed(text) for text in texts]

    async def web(self, query, run_id):
        return search(query)

    async def read(self, url):
        self.agents.require('web_fetch')
        return next(row['summary'] for row in search('') if row['url'] == url)

    Providers.structured = structured
    Providers.embed = embeddings
    Providers.deepseek_search = web
    ResearchGraph.read_web = read


install()


def create_app():
    from backend.api.app import create_app as production_app
    return production_app()




WorkerSettings = ProductionWorkerSettings
