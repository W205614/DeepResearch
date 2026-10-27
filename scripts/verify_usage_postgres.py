"""Explicit isolated PostgreSQL accounting probe; no supplier or HTTP transport is used."""
import argparse
import asyncio
import json
import time
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.config import Settings  # noqa: E402
from backend.core.postgres import PostgresDatabase  # noqa: E402
from backend.core.reliability import ServiceError  # noqa: E402
from backend.core.usage_governance import UsageGovernance, paid_scope  # noqa: E402


async def verify(workspace):
    settings = Settings()
    if 'dr-opt-acceptance' not in settings.database_url and not __import__('os').environ.get('ISOLATED_USAGE_PROBE') == 'dr-opt-acceptance':
        raise ValueError('Explicit isolated candidate marker required')
    db = PostgresDatabase(settings.database_url)
    await db.init()
    try:
        if (await db.one('SELECT current_user AS role'))['role'] != 'dr_python':
            raise ValueError('Probe must run with the actual least-privilege Python identity')
        if not await db.membership(workspace, workspace):
            raise ValueError('Use the isolated author fixture workspace')
        policy = settings.model_copy(update={'platform_period_calls': 1, 'subject_period_calls': 1,
            'workspace_period_calls': 1, 'account_period_calls': 1, 'usage_period_seconds': 60})
        # A synthetic period cannot contaminate current production/fixture UI accounting.
        fake_time = 10_000_000_000 + time.time_ns() % 100_000_000
        budget = UsageGovernance(db, policy)
        async def reserve():
            try:
                return await budget.reserve('isolated-no-http-account', 'llm', 400)
            except ServiceError as error:
                assert error.code == 'usage_budget_exhausted'
                return None
        with patch('backend.core.usage_governance.time.time', return_value=fake_time), paid_scope(workspace, workspace, 'evaluation'):
            results = await asyncio.gather(*(reserve() for _ in range(8)))
            keys = [key for key in results if key]
            assert len(keys) == 1
            await budget.settle(keys[0], 'isolated_fixture', {'usage': {'prompt_tokens': 4, 'completion_tokens': 6}})
            await budget.settle(keys[0], 'duplicate_fixture', {'usage': {'prompt_tokens': 99, 'completion_tokens': 99}})
            reopened = UsageGovernance(db, policy)
            try:
                await reopened.reserve('isolated-no-http-account', 'llm', 1)
                raise AssertionError('Restart reset accounting')
            except ServiceError as error:
                assert error.code == 'usage_budget_exhausted'
        period = int(fake_time) // 60 * 60
        row = await db.one("SELECT calls,pending_tokens,actual_input,actual_output FROM usage_periods WHERE scope_key='platform' AND period_start=?", (period,))
        assert row == {'calls': 1, 'pending_tokens': 0, 'actual_input': 4, 'actual_output': 6}
        return {'passed': True, 'database_role': 'dr_python', 'attempts': 8, 'accepted': 1, 'rejected': 7,
                'settlement_idempotent': True, 'restart_preserves_budget': True, 'http_requests': 0,
                'synthetic_period_start': period, 'scope': 'real isolated PG transactions and runtime grants; invented usage units, no bill or provider call'}
    finally:
        await db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(verify(args.workspace))))
