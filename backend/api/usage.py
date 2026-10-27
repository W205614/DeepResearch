"""Authenticated budget visibility; only the caller and selected workspace aggregates."""
import time
from fastapi import Depends, Request


def register_usage(app, rt, identity):
    @app.get('/api/usage')
    async def usage(request: Request, runtime=Depends(rt), user=Depends(identity)):
        settings = runtime.settings
        subject = request.state.principal.subject
        start = int(time.time()) // settings.usage_period_seconds * settings.usage_period_seconds
        dimensions = [('personal', 'subject:' + subject, settings.subject_period_calls, settings.subject_period_tokens),
                      ('workspace', 'workspace:' + user, settings.workspace_period_calls, settings.workspace_period_tokens)]
        if subject in settings.operator_subjects:
            dimensions.append(('platform', 'platform', settings.platform_period_calls, settings.platform_period_tokens))
        output = []
        for label, key, call_limit, token_limit in dimensions:
            row = await runtime.db.one('SELECT calls,reserved_tokens,pending_tokens,unknown_tokens,actual_input,actual_output FROM usage_periods WHERE scope_key=? AND period_start=?', (key, start))
            row = row or dict(calls=0, reserved_tokens=0, pending_tokens=0, unknown_tokens=0, actual_input=0, actual_output=0)
            charged = row['actual_input'] + row['actual_output'] + row['pending_tokens'] + row['unknown_tokens']
            output.append({'scope': label, **row, 'call_limit': call_limit, 'token_limit': token_limit,
                           'remaining_calls': max(0, call_limit - row['calls']),
                           'remaining_token_exposure': max(0, token_limit - charged)})
        return {'period_start': start, 'period_end': start + settings.usage_period_seconds,
                'period_basis': 'UTC epoch intervals', 'scopes': output,
                'meaning': '累计预留不是账单；已知Token、待结算和未知消耗分别记录，不推算金额'}
