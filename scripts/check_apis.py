"""Read-only provider configuration check; live OpenAI probes use one durable ledger.

The historical --web-search/--inspect-web-search flags are accepted for callers,
but never send requests. Compatible providers need a separately authorized test;
this tool does not silently spend the OpenAI four-request allowance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import Settings  # noqa: E402


def inspect_configuration(settings):
    official = settings.llm_base_url == 'https://api.openai.com/v1'
    valid_destination = settings.llm_provider != 'openai' or official
    return {'configuration_present': not settings.missing() and valid_destination,
            'missing_variables': settings.missing(), 'llm_provider': settings.llm_provider,
            'model': settings.llm_model_id, 'vision_model': settings.vision_model_id,
            'search_provider': settings.web_search_provider, 'embedding_model': settings.embedding_model,
            'embedding_dimension': settings.embedding_dimension,
            'openai_destination_valid': valid_destination,
            'supplier_http_requests': 0, 'live_connectivity': 'not_checked',
            'next_step': 'python scripts/openai_smoke.py --env-file <private_env_file>',
            'ledger': '.cache/openai-smoke/requests.sqlite3',
            'message': 'OpenAI真实验证仅使用既有四次持久ledger；其他兼容服务需要另行授权的验证，不在此命令外发。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--web-search', action='store_true', help='历史参数；只检查配置，不发请求')
    parser.add_argument('--inspect-web-search', action='store_true', help='历史参数；只检查配置，不发请求')
    args = parser.parse_args()
    report = inspect_configuration(Settings(_env_file=args.env_file))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['configuration_present'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
