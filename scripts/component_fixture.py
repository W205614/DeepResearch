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
        if role == 'analyst':
            # Preserve the production local-source attribution contract. The
            # fixture emits exact excerpts; it does not relax production gates.
            claims = [{'text': ('根据所提供资料，' if row.get('kind') == 'local' else '') + row['text'][:240],
                       'source_ids': [row['id']]} for row in data['evidence'][:5]]
            return schema.model_validate({'claims': claims, 'gaps': []})
        if role == 'validator':
            checks = []
            for claim in data['claims']:
                supported = claim['text'].removeprefix('根据所提供资料，') in ' '.join(
                    data['sources'][key]['text'] for key in claim['source_ids'])
                checks.append({'index': claim['index'], 'supported': supported,
                               'atomic_checks': [{'target_id': target['id'], 'supported': supported}
                                                 for target in claim['targets']],
                               'reason': '测试夹具：对限定来源的逐字摘录检查'})
            return schema.model_validate({'checks': checks})
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
    Providers.bocha_search = web
    ResearchGraph.read_web = read


install()


def create_app():
    from backend.api.app import create_app as production_app
    return production_app()




WorkerSettings = ProductionWorkerSettings
