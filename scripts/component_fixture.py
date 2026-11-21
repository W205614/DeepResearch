"""Explicit test entrypoints. Never imported by production application code."""
import asyncio
from backend.worker import DocumentWorkerSettings as DocumentWorkerSettings
from backend.worker import WorkerSettings as ProductionWorkerSettings
from backend.core.reliability import ServiceError, execution
from backend.domain.models import SupplementCoverage, SupplementSelection
from backend.infrastructure.providers import Providers
from backend.infrastructure.demo import generate, embed, search
from backend.research.graph import ResearchGraph


PG = "PostgreSQL 的全文检索怎么做？"
MILVUS = "比较两条路线按项目筛选和删除过期笔记的能力组合。"
MEMORY = "峰值内存的实测结果是多少？"
BUDGET = "冷启动实测结果是什么？"
PG_SOURCE = {"url": "https://example.com/product-fixture/postgresql", "name": "PostgreSQL 手册（夹具）",
             "summary": "PostgreSQL 可为全文检索建立 GIN 索引。"}
MILVUS_SOURCE = {"url": "https://example.com/product-fixture/milvus", "name": "Milvus 手册（夹具）",
                 "summary": "Milvus 支持按标量条件执行过滤查询和删除。"}
UNRELATED_SOURCE = {"url": "https://example.com/product-fixture/notes", "name": "笔记工具手册（夹具）",
                    "summary": "笔记应用可以按标签归档。"}
EDITED_QUERY = "Milvus 官方 Filtered Search Delete Entities"
NO_CITATIONS = "仅查找不存在的公开文档"
SEARCH_UNAVAILABLE = "复现检索服务不可用"
INTERNAL = "PaperBridge"
TRIAL = "试点方案记载完成名单只要求提交初始记录，未要求通过全部检查。"
RETEST = "复测记录记载完成名单只纳入通过全部检查的人员。"
RETEST_NEW = "复测记录记载完成名单只纳入通过全部检查并提交日志的人员。"
COMPARE = "根据所提供资料，试点方案与复测记录的完成名单口径不同，复测要求通过全部检查。"


def product_case(role, data, schema):
    if schema in (SupplementSelection, SupplementCoverage):
        selected = [i for i, claim in enumerate(data["claims"])
                    if data["selected_requirement"] == MILVUS and claim["text"] == MILVUS_SOURCE["summary"]]
        if schema is SupplementSelection:
            return {"claim_indices": selected}
        assert all(claim["text"] == MILVUS_SOURCE["summary"] for claim in data["claims"])
        return {"claim_indices": selected, "status": "partial" if selected else "insufficient",
                "gap": "缺少 PostgreSQL 按项目过滤和删除的证据" if selected else "缺少可读原文"}
    topic = data.get("topic", "")
    if role == "planner" and "PostgreSQL 与 Milvus" in topic:
        return {"title": "两种数据库的差异", "questions": [PG], "queries": ["PostgreSQL 与 Milvus"], "scope": "单机"}
    if role == "analyst" and INTERNAL in topic:
        sources = data["evidence"]
        trial = next((row for row in sources if TRIAL in row["text"]), None)
        retest = next((row for row in sources if RETEST in row["text"]), None)
        if trial and retest:
            return {"claims": [{"text": COMPARE, "kind": "inference", "source_ids": [trial["id"], retest["id"]]}],
                    "gaps": ["冷启动结果待测"]}
    if role != "coverage":
        return None
    claims = data["claims"]
    if "PostgreSQL 与 Milvus" in topic:
        index = next((i for i, claim in enumerate(claims) if claim["text"] == PG_SOURCE["summary"]), None)
        return {"answered_questions": [0] if index is not None else [], "original_request_answered": False,
                "requirements": [
                    {"requirement": PG, "status": "answered" if index is not None else "insufficient",
                     "claim_indices": [index] if index is not None else []},
                    {"requirement": MILVUS, "status": "insufficient", "gap": "缺少可读的 Milvus 官方说明"},
                    {"requirement": MEMORY, "status": "insufficient", "gap": "缺少实测材料"},
                    {"requirement": BUDGET, "status": "insufficient", "gap": "缺少冷启动记录"},
                ]}

    if INTERNAL in topic:
        comparisons = [i for i, claim in enumerate(claims) if claim["text"] == COMPARE]
        return {"original_request_answered": False, "comparison_claim_indices": comparisons,
                "requirements": [{"requirement": "两份资料的完成名单口径", "status": "partial", "claim_indices": list(range(len(claims))),
                                  "gap": "资料尚未给出冷启动结果"}]}
    return None


def install():
    original_hosted_search = Providers.deepseek_search
    original_post = Providers.post

    async def post(self, url, key, payload, label, retries=2):
        if url.endswith("/responses"):
            if payload.get("input") == NO_CITATIONS:
                return {"status": "completed", "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": "没有返回可引用的网页。", "annotations": []}]}]}
            if payload.get("input") == SEARCH_UNAVAILABLE:
                raise ServiceError("夹具检索服务返回 HTTP 503", code="provider_unavailable")
        return await original_post(self, url, key, payload, label, retries)

    async def structured(self, role, instruction, data, schema, run_id, **kwargs):
        if role == 'planner' and str(data.get('topic', '')).startswith('slow-recovery'):
            first = await self.db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES(?,?)", ('slow:' + run_id, '1'))
            if first:
                await asyncio.sleep(40)
        await asyncio.sleep(.05)
        await self.db.usage(run_id, 10, 5)
        case = product_case(role, data, schema)
        if case is not None:
            return schema.model_validate(case)
        if role == 'analyst':
            # Preserve the production local-source attribution contract. The
            # fixture emits exact excerpts; it does not relax production gates.
            claims = [{'text': ('根据所提供资料，' if row.get('kind') == 'local' else '') + row['text'][:240],
                       'source_ids': [row['id']]} for row in data['evidence'][:5]]
            return schema.model_validate({'claims': claims, 'gaps': []})
        if role == 'validator':
            checks = []
            for claim in data['claims']:
                evidence = ' '.join(data['sources'][key]['text'] for key in claim['source_ids'])
                supported = (claim['text'].removeprefix('根据所提供资料，') in evidence
                             or claim['text'] == COMPARE and TRIAL in evidence and RETEST in evidence)
                checks.append({'index': claim['index'], 'supported': supported,
                               'atomic_checks': [{'target_id': target['id'], 'supported': supported}
                                                 for target in claim['targets']],
                               'reason': '测试夹具：对限定来源的逐字摘录检查'})
            return schema.model_validate({'checks': checks})
        return schema.model_validate(generate(role, data))

    async def embeddings(self, texts, **kwargs):
        # The real Milvus schema/transport keeps the production target dimension.
        # Coordinates beyond the deterministic hash fixture carry zero weight.
        dimension = self.settings.embedding_dimension
        return [(embed(text) + [0.0] * dimension)[:dimension] for text in texts]

    async def web(self, query, run_id):
        if query in (NO_CITATIONS, SEARCH_UNAVAILABLE):
            return await original_hosted_search(self, query, run_id)
        if query.endswith(BUDGET):
            await execution.get().reserve(self.settings.max_run_reserved_tokens + 1)
        if query.endswith(MEMORY):
            return [UNRELATED_SOURCE]
        if query == EDITED_QUERY or query.endswith(MILVUS):
            return [MILVUS_SOURCE, UNRELATED_SOURCE]
        if query == "PostgreSQL 与 Milvus":
            return [PG_SOURCE]
        return search(query)

    async def read(self, url):
        self.agents.require('web_fetch')
        if url.endswith("/product-fixture/unavailable"):
            raise ServiceError("来源正文不可访问（HTTP 404）")
        return next(row['summary'] for row in [PG_SOURCE, MILVUS_SOURCE, UNRELATED_SOURCE, *search('')] if row['url'] == url)

    from backend.api import source_rechecks

    read_current = source_rechecks.fetch_text

    async def recheck(url):
        if url == PG_SOURCE["url"]:
            return "PostgreSQL 可为全文检索建立 GIN 索引。夹具正文已追加安装说明。"
        if url == MILVUS_SOURCE["url"]:
            raise ServiceError("夹具网页当前无法读取")
        return await read_current(url)

    source_rechecks.fetch_text = recheck
    Providers.post = post
    Providers.structured = structured
    Providers.embed = embeddings
    Providers.deepseek_search = web
    Providers.bocha_search = web
    Providers.openai_search = web
    ResearchGraph.read_web = read


install()


def create_app():
    from backend.api.app import create_app as production_app
    return production_app()




WorkerSettings = ProductionWorkerSettings


if __name__ == '__main__':
    from backend.worker import main
    main()
