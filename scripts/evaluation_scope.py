"""Explicit CLI-only accounting context; never installs a transport or resets a budget."""
from functools import wraps

from backend.core.usage_governance import paid_scope


def scoped_evaluation(function):
    @wraps(function)
    async def evaluate(*args, **kwargs):
        with paid_scope("evaluator", "evaluator", "evaluation"):
            return await function(*args, **kwargs)
    return evaluate


def configure_evaluator_limits(budget):
    """CLI only: derived caps cannot exceed the reconciled transport allowance.

    Product defaults remain unchanged. Every actual HTTP still passes both the
    normal product governor and the append-only transport ledger.
    """
    import os
    report=budget.report()
    policy=report['policy']
    if not policy.get('ci_series_id'):
        return {}
    calls=min(policy['model_http']-sum(row['kind'] in {'llm','search'} for row in report['requests']),
              policy['total_http']-report['http_requests'])
    tokens=policy['input_tokens']-report['charged_input_tokens']+policy['output_tokens']-report['charged_output_tokens']
    if report['halt'] or calls<=0 or tokens<=0:
        raise ValueError('Reconciled evaluator allowance is exhausted or halted')
    # Settings validation has a 1000 token minimum. The outer ledger continues
    # enforcing the exact smaller input/output balance before any HTTP.
    limits={key:str(min(calls,1000000)) for key in
            ('PLATFORM_PERIOD_CALLS','SUBJECT_PERIOD_CALLS','WORKSPACE_PERIOD_CALLS','ACCOUNT_PERIOD_CALLS')}
    limits.update({key:str(min(max(tokens,1000),2000000000)) for key in
                   ('PLATFORM_PERIOD_TOKENS','SUBJECT_PERIOD_TOKENS','WORKSPACE_PERIOD_TOKENS','ACCOUNT_PERIOD_TOKENS')})
    os.environ.update(limits)
    return limits
