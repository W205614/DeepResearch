"""Explicit web-source checks; neither report text nor its immutable evidence is overwritten."""
import hashlib
import asyncio
import json
import time
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from ..core.db import now, uid
from ..core.security import fetch_text
from ..core.reliability import ServiceError
from ..services.document_access import require_document


async def report_for(runtime, workspace, subject, report_id):
    report = await runtime.db.one('SELECT * FROM report_publications WHERE id=? AND workspace_id=?', (report_id, workspace))
    member = await runtime.db.membership(workspace, subject)
    if not report or not member or (report['status'] != 'published' and member['role'] != 'admin' and report['author_subject'] != subject):
        raise HTTPException(404, '报告不存在')
    for source in json.loads(report['sources_json']):
        if source.get('kind') == 'local':
            await require_document(runtime.db, workspace, source.get('document_id', ''))
    return report


async def recheck(runtime, workspace, subject, report_id):
    report = await report_for(runtime, workspace, subject, report_id)
    sources = [s for s in json.loads(report['sources_json']) if s.get('kind') == 'web']
    if not sources:
        raise HTTPException(409, '该报告没有可重核验的公开来源')
    if len(sources) > 20:
        raise HTTPException(409, '来源超过本次20条上限，请按报告拆分核验')
    async with runtime.db.guard('source-recheck:' + workspace):
        previous = await runtime.db.one('SELECT checked_at FROM source_rechecks WHERE workspace_id=? ORDER BY checked_at DESC LIMIT 1', (workspace,))
        if previous and (datetime.now(timezone.utc) - datetime.fromisoformat(previous['checked_at'])).total_seconds() < 30:
            raise HTTPException(429, '公开来源检查冷却中，请稍后重试')
        output = []
        deadline = time.monotonic() + 20
        for source in sources:
            old_hash = hashlib.sha256(source.get('text', '').encode()).hexdigest()
            record = {'id': uid(), 'source_id': source['id'], 'old_hash': old_hash,
                      'new_hash': '', 'status': 'unavailable', 'error_code': '', 'checked_at': now()}
            snapshot = {}
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    record['status'] = 'not_checked'
                    raise ServiceError('check deadline')
                async with asyncio.timeout(min(8, remaining)):
                    text = await fetch_text(source.get('url', ''))
                record['new_hash'] = hashlib.sha256(text.encode()).hexdigest()
                record['status'] = ('comparison_unavailable' if source.get('access') != 'fulltext'
                                    else 'unchanged' if record['new_hash'] == old_hash else 'changed')
                snapshot = {'url': source.get('url'), 'text': text, 'scope': 'fetched public page text, not factual re-verification'}
                if record['status'] == 'changed':
                    await runtime.db.execute('DELETE FROM web_cache WHERE url=?', (source.get('url'),))
            except (ServiceError, TimeoutError):
                record['error_code'] = 'check_deadline' if record['status'] == 'not_checked' else 'source_fetch_unavailable'
            await runtime.db.execute('''INSERT INTO source_rechecks(id,workspace_id,report_id,source_id,status,old_hash,new_hash,checked_at,actor_subject,error_code,snapshot_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (record['id'], workspace, report_id, record['source_id'], record['status'],
                old_hash, record['new_hash'], record['checked_at'], subject, record['error_code'], json.dumps(snapshot, ensure_ascii=False)))
            output.append(record)
        if any(item['status'] == 'changed' for item in output):
            await runtime.db.execute("UPDATE report_publications SET evidence_status='needs_review' WHERE id=? AND workspace_id=?", (report_id, workspace))
        await runtime.db.audit(workspace, subject, 'report.source_recheck', 'report', report_id)
        return {'checks': output, 'scope': '网页文本变化检测，不等同事实真伪判断；不可访问不代表不存在', 'report_unchanged': True}


def register_source_rechecks(app, rt, identity, researcher):
    @app.get('/api/reports/{report_id}/source-rechecks')
    async def history(report_id: str, request: Request, runtime=Depends(rt), user=Depends(identity)):
        await report_for(runtime, user, request.state.principal.subject, report_id)
        return await runtime.db.rows('''SELECT source_id,status,old_hash,new_hash,checked_at,error_code FROM source_rechecks
            WHERE workspace_id=? AND report_id=? ORDER BY checked_at DESC LIMIT 100''', (user, report_id))

    @app.post('/api/reports/{report_id}/source-rechecks')
    async def check(report_id: str, request: Request, runtime=Depends(rt), user=Depends(researcher)):
        return await recheck(runtime, user, request.state.principal.subject, report_id)
