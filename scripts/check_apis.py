"""Small live checks. Never prints API responses, credentials or private documents."""
import asyncio
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.config import Settings
from backend.core.db import Database
from backend.domain.models import Route
from backend.infrastructure.providers import Providers, ServiceError


def response_shape(value, depth=0):
    """Return types, field names and text lengths only; never output provider content."""
    if depth > 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(key): response_shape(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [response_shape(item, depth + 1) for item in value[:5]] + (["…"] if len(value) > 5 else [])
    if isinstance(value, str):
        return f"string(length={len(value)})"
    return type(value).__name__


async def main(include_web_search: bool, inspect_web_search: bool):
    settings = Settings(request_timeout_seconds=30)
    path = Path('.cache/api-check')
    path.mkdir(parents=True, exist_ok=True)
    db = Database(path / 'checks.sqlite3')
    await db.init()
    providers = Providers(settings, db)
    results = {}
    try:
        try:
            await providers.structured('router', '只输出 mode=quick 和 reason=连接测试 的 JSON 对象。',
                                       {'topic': '连接测试'}, Route, '')
            results['llm'] = 'ok'
        except ServiceError as exc:
            results['llm'] = str(exc)
        try:
            vector = (await providers.embed(['DeepResearch connection test']))[0]
            results['embedding'] = 'ok'
            results['actual_dimension'] = len(vector)
        except ServiceError as exc:
            results['embedding'] = str(exc)
        results['web_search_provider'] = settings.web_search_provider
        results['search_configured'] = bool(settings.llm_api_key.get_secret_value()) if settings.web_search_provider == 'deepseek' else bool(settings.bocha_api_key.get_secret_value())
        if include_web_search:
            try:
                sources = await providers.search('DeepSeek Responses API web_search official documentation', 'web-search-check')
                results['web_search'] = {'status': 'ok', 'verifiable_url_citations': len(sources)}
            except ServiceError as exc:
                results['web_search'] = {'status': 'error', 'message': str(exc)}
        if inspect_web_search:
            payload = {
                'model': settings.llm_model_id, 'input': 'DeepSeek Responses API web_search official documentation',
                'tools': [{'type': 'web_search'}], 'tool_choice': {'type': 'web_search'}, 'max_output_tokens': 300,
            }
            raw = await providers.post(f'{settings.llm_base_url}/responses', settings.llm_api_key.get_secret_value(), payload,
                                       'DeepSeek 联网搜索', retries=0)
            results['web_search_response_shape'] = response_shape(raw)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        await providers.close()


parser = argparse.ArgumentParser(description='检查已配置服务；不输出密钥或原始响应。')
parser.add_argument('--web-search', action='store_true', help='额外执行一次真实联网搜索并只统计可验证引用')
parser.add_argument('--inspect-web-search', action='store_true', help='额外检查联网响应结构；不输出正文、URL 或密钥')
args = parser.parse_args()
asyncio.run(main(args.web_search, args.inspect_web_search))
