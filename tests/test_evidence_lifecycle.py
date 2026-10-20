import json
import pytest
from backend.services.evidence_lifecycle import structural_diff
from backend.infrastructure.providers import ServiceError
from backend.core.db import now

async def ready(runtime, text='上限500元。仅限正式员工。'):
    doc = await runtime.documents.add('alice','policy.md',text.encode())
    await runtime.documents.ingest(doc['id'])
    return await runtime.db.one('SELECT * FROM documents WHERE id=?',(doc['id'],))

async def publication(runtime, document, claims=True):
    chunk = await runtime.db.one('SELECT * FROM chunks WHERE document_id=?',(document['id'],))
    evidence = {'id':'L-1','kind':'local','document_id':document['id'],'index_version':document['index_version'],'text':chunk['text']}
    artifact = {'requirements':['报销条件'],'claims':[{'id':'claim-1','text':'上限500元','evidence':[evidence]}] if claims else []}
    await runtime.db.execute('''INSERT INTO report_publications(id,workspace_id,source_run_id,author_subject,topic,report_markdown,sources_json,validation_json,content_sha256,status,submitted_at,artifacts_json,corpus_revision,evidence_status)
        VALUES('report','alice','run','alice','报销条件','old immutable report',?,'{}','fingerprint','published',?,?,1,'current')''',(json.dumps([evidence]),now(),json.dumps(artifact)))

async def test_version_archive_diff_and_change_impacts(runtime):
    doc = await ready(runtime)
    await publication(runtime,doc)
    await runtime.documents.replace(doc['id'],'alice','policy.md','上限800元。实习生不适用。'.encode())
    await runtime.documents.ingest(doc['id'])
    versions = await runtime.documents.lifecycle.versions(doc['id'],'alice')
    assert [v['version'] for v in versions] == [2,1]
    assert versions[0]['current'] and not versions[1]['current']
    difference = await runtime.documents.lifecycle.diff(doc['id'],'alice',1,2)
    assert difference['changes'][0]['kind'] == 'modified'
    assert '500' in difference['changes'][0]['old']['text']
    assert '800' in difference['changes'][0]['new']['text']
    assert difference['semantic_judgment'] is False
    assert await runtime.documents.lifecycle.revision('alice') == 2
    impact = await runtime.db.one('SELECT * FROM report_impacts WHERE report_id=?',('report',))
    assert impact['status'] == 'needs_review'
    assert json.loads(impact['affected_claims_json']) == ['claim-1']
    before = await runtime.db.one("SELECT * FROM report_publications WHERE id='report'")
    assert before['report_markdown'] == 'old immutable report'
    await runtime.documents.lifecycle.process_events()
    assert len(await runtime.db.rows('SELECT * FROM report_impacts')) == 1
    assert await runtime.documents.store.exists('uploads',doc['object_key'])
    with pytest.raises(LookupError):
        await runtime.documents.lifecycle.versions(doc['id'],'mallory')

async def test_new_unreferenced_source_requires_scope_check(runtime):
    doc=await ready(runtime)
    await publication(runtime,doc)
    new=await runtime.documents.add('alice','exception.md','另有例外：实习生不得报销。'.encode())
    await runtime.documents.ingest(new['id'])
    impact=await runtime.db.one("SELECT * FROM report_impacts WHERE report_id='report'")
    assert impact['status'] == 'check_pending'
    assert 'scope_changed_requires_research' in json.loads(impact['reasons_json'])

async def test_revision_rejects_changed_target_and_preserves_budget(runtime):
    doc=await ready(runtime)
    await publication(runtime,doc)
    await runtime.db.execute('''INSERT INTO report_revision_jobs(id,workspace_id,source_report_id,target_run_id,idempotency_key,requested_by,target_corpus_revision,source_content_sha256,status,created_at,updated_at)
        VALUES('job','alice','report','revision-run','request-key','alice',1,'fingerprint','queued',?,?)''',(now(),now()))
    plan=await runtime.documents.lifecycle.revision_context({'id':'revision-run','user_id':'alice'})
    assert plan['strategy']=='full_research'
    await runtime.documents.delete(doc['id'],'alice')
    with pytest.raises(ServiceError) as failure:
        await runtime.documents.lifecycle.revision_context({'id':'revision-run','user_id':'alice'})
    assert failure.value.code == 'revision_stale'

async def test_vector_refills_valid_candidates_with_single_budget(runtime,monkeypatch):
    await ready(runtime,'ALPHA first evidence')
    valid=await runtime.db.one('SELECT id FROM chunks')
    calls=[]
    async def search(*args,limit,**kwargs):
        calls.append(limit)
        return [{'id':f'stale-{i}','score':0.9} for i in range(min(3,limit))]+([{'id':valid['id'],'score':0.8}] if limit>3 else [])
    monkeypatch.setattr(runtime.vectors,'search',search)
    rows=await runtime.documents.search('alice',['ALPHA'],limit=1)
    assert calls==[3,6]
    assert rows[0]['id']==valid['id']

async def test_context_blocks_keep_independent_provenance(runtime):
    doc=await ready(runtime,('第一条：正式员工可以报销。\n\n'+'A'*650+'\n\n第二条：实习生不适用。'))
    rows=await runtime.documents.search('alice',['正式员工'],limit=1)
    assert rows
    for context in rows[0]['context_blocks']:
        assert context['document_id']==doc['id'] and context['index_version']==1
        assert context['id'] != rows[0]['id']

def test_structural_difference_does_not_infer_meaning():
    changes=structural_diff([{'locator':'p1','text':'允许'}],[{'locator':'p1','text':'不允许'}])
    assert changes==[{'kind':'modified','old':{'locator':'p1','text':'允许'},'new':{'locator':'p1','text':'不允许'}}]

async def test_new_material_during_generation_fails_before_artifact_commit(runtime,monkeypatch):
    from unittest.mock import AsyncMock
    from backend.domain.models import RunRequest
    from backend.core.db import uid
    monkeypatch.setattr(runtime,'schedule',AsyncMock())
    run=await runtime.create('alice',RunRequest(topic='查询报销',client_request_id=uid()))
    async def generate(*args,**kwargs):
        await ready(runtime,'新增例外：实习生不得报销。')
        return {'report':'stale report','validation':{},'evidence':[],'final_claims':[]}
    monkeypatch.setattr(runtime.graph,'ainvoke',generate)
    await runtime.execute_run(run['id'])
    result=await runtime.db.owned_run(run['id'],'alice')
    assert result['status']=='failed' and result['error_info']['code']=='source_changed'
    assert result['report'] != 'stale report'
    artifact=await runtime.db.one('SELECT * FROM run_artifacts WHERE run_id=?',(run['id'],))
    assert json.loads(artifact['claims_json'])==[]

async def test_artifacts_commit_with_successful_terminal_state(runtime,monkeypatch):
    from unittest.mock import AsyncMock
    from backend.domain.models import RunRequest
    from backend.core.db import uid
    monkeypatch.setattr(runtime,'schedule',AsyncMock())
    run=await runtime.create('alice',RunRequest(topic='查询事实',client_request_id=uid()))
    evidence={'id':'W1','kind':'web','text':'明确事实'}
    monkeypatch.setattr(runtime.graph,'ainvoke',AsyncMock(return_value={'report':'answer','validation':{'verification_contract':__import__('backend.research.graph',fromlist=['VERIFICATION_CONTRACT']).VERIFICATION_CONTRACT,'verification_wire':__import__('backend.research.graph',fromlist=['VERIFICATION_WIRE']).VERIFICATION_WIRE,'checked_claims':1,'supported_claims':1},'evidence':[evidence],
        'final_claims':[{'text':'明确事实','source_ids':['W1']}],'plan':{'questions':['查询事实']}}))
    await runtime.execute_run(run['id'])
    result=await runtime.db.owned_run(run['id'],'alice')
    artifact=await runtime.db.one('SELECT * FROM run_artifacts WHERE run_id=?',(run['id'],))
    assert result['status']=='completed'
    assert json.loads(artifact['claims_json'])[0]['text']=='明确事实'
    assert artifact['attempt_count']==result['attempt_count']

async def test_versions_and_diff_api_reject_cross_workspace(api_client):
    app,client=api_client
    runtime=app.state.runtime
    doc=await ready(runtime)
    assert (await client.get(f"/api/documents/{doc['id']}/versions")).status_code==200
    assert (await client.get(f"/api/documents/{doc['id']}/versions",headers={'X-User-ID':'mallory'})).status_code==404
    assert (await client.get(f"/api/documents/{doc['id']}/diff?from=1&to=1")).json()['changes']==[]
    assert (await client.get(f"/api/documents/{doc['id']}/diff?from=0&to=1")).status_code==422

async def test_targeted_revision_reuses_only_current_independent_sources(runtime):
    first=await ready(runtime,'甲条款：上限500元。')
    second=await runtime.documents.add('alice','other.md','乙条款：报销须提交凭证。'.encode())
    await runtime.documents.ingest(second['id'])
    await publication(runtime,first)
    chunk=await runtime.db.one('SELECT c.*,d.index_version,d.hash FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.id=?',(second['id'],))
    source={'id':'L-independent','kind':'local','document_id':second['id'],'chunk_id':chunk['id'],
            'index_version':chunk['index_version'],'document_hash':chunk['hash'],'text':chunk['text'],'locator':chunk['locator'],'title':'other'}
    row=await runtime.db.one("SELECT artifacts_json FROM report_publications WHERE id='report'")
    artifact=json.loads(row['artifacts_json'])
    artifact['claims'].append({'id':'claim-2','text':'报销须提交凭证。','evidence':[source]})
    await runtime.db.execute("UPDATE report_publications SET artifacts_json=?,corpus_revision=2 WHERE id='report'",(json.dumps(artifact),))
    await runtime.documents.replace(first['id'],'alice','policy.md','甲条款：上限800元。'.encode())
    await runtime.documents.ingest(first['id'])
    await runtime.db.execute('''INSERT INTO report_revision_jobs(id,workspace_id,source_report_id,target_run_id,idempotency_key,requested_by,target_corpus_revision,source_content_sha256,status,created_at,updated_at)
        VALUES('target-job','alice','report','target-run','target-key','alice',3,'fingerprint','queued',?,?)''',(now(),now()))
    plan=await runtime.documents.lifecycle.revision_context({'id':'target-run','user_id':'alice'})
    assert plan['strategy']=='targeted_revalidation'
    assert plan['affected_claim_ids']==['claim-1']
    assert plan['seed_claims']==[{'text':'报销须提交凭证。','source_ids':['L-independent']}]
    assert plan['seed_sources'][0]['document_id']==second['id']

async def test_change_event_retry_after_processing_failure(runtime,monkeypatch):
    doc=await ready(runtime)
    await publication(runtime,doc)
    original=runtime.db.rows
    async def fail(query,args=()):
        if 'SELECT * FROM report_publications' in query:
            raise OSError('db failure')
        return await original(query,args)
    monkeypatch.setattr(runtime.db,'rows',fail)
    await runtime.documents.replace(doc['id'],'alice','policy.md','新版上限800元。'.encode())
    await runtime.documents.ingest(doc['id'])
    event=await runtime.db.one("SELECT * FROM document_change_events WHERE revision=2")
    assert event['status']=='pending' and event['error']=='impact_processing_failed'
    monkeypatch.setattr(runtime.db,'rows',original)
    await runtime.documents.lifecycle.process_events()
    event=await runtime.db.one("SELECT * FROM document_change_events WHERE revision=2")
    assert event['status']=='processed' and not event['error']
    assert (await runtime.db.one("SELECT status FROM report_impacts WHERE report_id='report'"))['status']=='needs_review'

async def test_upgrade_archives_only_current_known_content(runtime):
    doc=await ready(runtime)
    await runtime.db.execute('DELETE FROM document_versions')
    await runtime.documents.lifecycle.archive_current_versions()
    versions=await runtime.documents.lifecycle.versions(doc['id'],'alice')
    assert len(versions)==1 and versions[0]['version']==1
    assert versions[0]['parser_config']=={'contract':'legacy_current_snapshot','historical_config_known':False}
    await runtime.documents.lifecycle.archive_current_versions()
    assert len(await runtime.db.rows('SELECT * FROM document_versions'))==1

async def test_late_worker_cannot_commit_artifact_after_takeover(runtime,monkeypatch):
    from unittest.mock import AsyncMock
    from backend.domain.models import RunRequest
    from backend.core.db import uid
    monkeypatch.setattr(runtime,'schedule',AsyncMock())
    run=await runtime.create('alice',RunRequest(topic='query',client_request_id=uid()))
    async def late_result(*args,**kwargs):
        await runtime.db.execute('UPDATE runs SET attempt_count=2 WHERE id=?',(run['id'],))
        return {'report':'late report','validation':{},'final_claims':[{'text':'late','source_ids':[]}]}
    monkeypatch.setattr(runtime.graph,'ainvoke',late_result)
    await runtime.execute_run(run['id'])
    current=await runtime.db.owned_run(run['id'],'alice')
    artifact=await runtime.db.one('SELECT * FROM run_artifacts WHERE run_id=?',(run['id'],))
    assert current['status']=='running' and current['attempt_count']==2 and current['report']!='late report'
    assert json.loads(artifact['claims_json'])==[]

def test_identical_claim_text_merges_all_provenance_without_id_collision():
    from backend.services.evidence_lifecycle import EvidenceLifecycle
    result={'evidence':[{'id':'L1','kind':'local','document_id':'a','text':'same'},
                        {'id':'L2','kind':'local','document_id':'b','text':'same'}],
            'final_claims':[{'text':'same','source_ids':['L1'],'kind':'fact'},
                            {'text':'same','source_ids':['L2'],'kind':'fact'}]}
    artifact=EvidenceLifecycle.artifact({'topic':'query'},result,1)
    assert len(artifact['claims'])==1
    assert artifact['claims'][0]['source_ids']==['L1','L2']
    assert [e['document_id'] for e in artifact['claims'][0]['evidence']]==['a','b']

async def test_public_report_ignores_internal_document_changes(runtime):
    doc=await ready(runtime)
    await publication(runtime,doc)
    await runtime.db.execute("UPDATE report_publications SET corpus_revision=-1,evidence_status='current',artifacts_json=? WHERE id='report'",(json.dumps({'data_policy':'public','claims':[]}),))
    await runtime.documents.replace(doc['id'],'alice','policy.md','内部新版内容。'.encode())
    await runtime.documents.ingest(doc['id'])
    assert (await runtime.db.one("SELECT evidence_status FROM report_publications WHERE id='report'"))['evidence_status']=='current'
    assert await runtime.db.rows('SELECT * FROM report_impacts')==[]
