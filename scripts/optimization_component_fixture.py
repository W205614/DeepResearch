"""Explicit isolated acceptance fault controls. Never imported by daily production entrypoints."""
from scripts.component_fixture import create_app, WorkerSettings, DocumentWorkerSettings  # noqa: F401
from backend.services.documents import Documents
from backend.core.reliability import ServiceError
from backend.api import source_rechecks
from backend.research.graph import ResearchGraph

original_search = Documents.search
original_read = ResearchGraph.read_web


async def search(self, user, queries, *args, **kwargs):
    if any('恢复取证' in query for query in queries):
        await self.db.execute("INSERT INTO metadata(key,value) VALUES('optimization-retrieval-attempts','1') ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(metadata.value AS INTEGER)+1 AS TEXT)")
        failure = await self.db.one("SELECT value FROM metadata WHERE key='optimization-retrieval-offline'")
        if failure and failure['value'] == '1':
            raise ServiceError('isolated deterministic retrieval outage')
    return await original_search(self, user, queries, *args, **kwargs)


async def checked_public_page(url):
    # Only source-fetch is substituted; real role authorization, immutable report
    # records, HTTP routes and persistence remain production implementations.
    return '受控公开来源的新版本正文。新增了适用范围和限制条件，须重新研究并审核。' * 10


async def read(self, url):
    version = await self.db.one("SELECT value FROM metadata WHERE key='optimization-public-version'")
    if version and version['value'] == '2':
        self.agents.require('web_fetch')
        return await checked_public_page(url)
    return await original_read(self, url)


Documents.search = search
source_rechecks.fetch_text = checked_public_page
ResearchGraph.read_web = read
