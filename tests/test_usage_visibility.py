import time


async def test_usage_only_exposes_current_subject_and_workspace_aggregates(api_client):
    app,client=api_client
    settings=app.state.runtime.settings
    period=int(time.time())//settings.usage_period_seconds*settings.usage_period_seconds
    for key,calls in [('subject:alice',3),('subject:bob',999),('workspace:alice',5),('account:secret-fingerprint',777)]:
        await app.state.runtime.db.execute('INSERT INTO usage_periods(scope_key,period_start,calls) VALUES(?,?,?)',(key,period,calls))
    response=await client.get('/api/usage?subject=bob')
    assert response.status_code==200
    rows=response.json()['scopes']
    assert [(row['scope'],row['calls']) for row in rows]==[('personal',3),('workspace',5)]
    assert 'bob' not in response.text and 'secret-fingerprint' not in response.text
    assert len(rows)==2
