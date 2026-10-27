import hashlib
import json
import pytest
from fastapi import HTTPException

from backend.api import source_rechecks
from backend.core.db import now
from backend.core.reliability import ServiceError
from backend.core.usage_governance import paid_scope


async def publication(runtime):
    await runtime.db.ensure_personal_workspace('alice', 'workspace', (0, 2))
    sources=[{'id':key,'kind':'web','url':f'https://example.org/{key}', 'text':key*100,'access':'fulltext','retrieved_at':now()}
             for key in ('same','changed','unavailable')]
    await runtime.db.execute('''INSERT INTO report_publications(id,workspace_id,source_run_id,author_subject,topic,report_markdown,sources_json,validation_json,content_sha256,status,submitted_at)
        VALUES('report','alice','run','alice','scope','immutable report',?,'{}','frozen-hash','published',?)''', (json.dumps(sources),now()))
    return sources


async def test_explicit_recheck_keeps_snapshot_and_distinguishes_change_from_unavailable(runtime,monkeypatch):
    sources=await publication(runtime)
    before=await runtime.db.one("SELECT * FROM report_publications WHERE id='report'")
    async def fetch(url):
        if url.endswith('unavailable'):
            raise ServiceError('network unavailable')
        return 'changed new text' if url.endswith('changed') else sources[0]['text']
    monkeypatch.setattr(source_rechecks,'fetch_text',fetch)
    with paid_scope('alice','alice'):
        result=await source_rechecks.recheck(runtime,'alice','alice','report')
    assert [item['status'] for item in result['checks']]==['unchanged','changed','unavailable']
    after=await runtime.db.one("SELECT * FROM report_publications WHERE id='report'")
    for field in ('report_markdown','sources_json','content_sha256'):
        assert after[field]==before[field]
    assert after['evidence_status']=='needs_review'
    row=await runtime.db.one("SELECT snapshot_json FROM source_rechecks WHERE source_id='changed'")
    assert json.loads(row['snapshot_json'])['text']=='changed new text'
    with pytest.raises(HTTPException) as failure:
        await source_rechecks.recheck(runtime,'alice','alice','report')
    assert failure.value.status_code==429


async def test_recheck_does_not_treat_summary_expansion_as_changed_truth(runtime,monkeypatch):
    sources=await publication(runtime)
    sources=sources[:1]
    sources[0]['access']='summary'
    await runtime.db.execute("UPDATE report_publications SET sources_json=? WHERE id='report'",(json.dumps(sources),))
    async def fulltext(url):return 'new full body with additional conditions'
    monkeypatch.setattr(source_rechecks,'fetch_text',fulltext)
    with paid_scope('alice','alice'):
        result=await source_rechecks.recheck(runtime,'alice','alice','report')
    assert result['checks'][0]['status']=='comparison_unavailable'
    assert result['checks'][0]['new_hash']==hashlib.sha256((await fulltext('')).encode()).hexdigest()
