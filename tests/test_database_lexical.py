"""Frozen, offline SQL lexical equivalence, capacity and authorization tests."""
import json
import math
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from rank_bm25 import BM25Plus

from backend.core.db import now
from backend.core.usage_governance import paid_scope
from backend.services.document_parsing import tokens
from backend.services.lexical_retrieval import ranking_text


async def seed(runtime, rows):
    async with runtime.db.connection() as conn:
        for item in rows:
            await conn.execute("""INSERT INTO documents(id,user_id,name,hash,status,created_at,updated_at,index_version)
                VALUES(?,'alice',?,?,'ready',?,?,1)""", (item['id'], item['title'], item['id'], now(), now()))
            await conn.execute("""INSERT INTO chunks(id,document_id,user_id,text,locator,vector,ordinal)
                VALUES(?,?,'alice',?,'正文','[]',0)""", (item['id'], item['id'], item['text']))
        await conn.commit()


async def test_database_bm25_matches_frozen_chinese_reference(runtime):
    fixture = json.loads((Path(__file__).parents[1]/'eval/lexical/optimization-20261026.json').read_text(encoding='utf-8'))
    await seed(runtime, fixture['documents'])
    await runtime.documents.lexical_index.ensure('alice')
    rows = [dict(row, locator='正文') for row in fixture['documents']]
    reference = BM25Plus([tokens(ranking_text(row)) for row in rows], delta=0)
    for query in fixture['queries']:
        expected = {row['id']: float(value) for row,value in zip(rows,reference.get_scores(tokens(query))) if value>0}
        actual = await runtime.documents.lexical_index.scores('alice',query,128)
        assert actual.keys() == expected.keys()
        for key,value in expected.items():
            assert math.isclose(actual[key],value,rel_tol=1e-10,abs_tol=1e-10), (query,key,actual[key],value)
        assert sorted(actual,key=lambda key:(-actual[key],key)) == sorted(expected,key=lambda key:(-expected[key],key))


async def test_more_than_old_4000_chunks_searches_without_loading_corpus_text(runtime,monkeypatch):
    await seed(runtime,[{'id':'large','title':'资料','text':'普通背景'}])
    async with runtime.db.connection() as conn:
        await conn.executemany("INSERT INTO chunks(id,document_id,user_id,text,locator,vector,ordinal) VALUES(?,'large','alice',?,'正文','[]',?)",
                              [(f'c{i:05d}','实习生报销上限八百元' if i==4200 else '普通无关背景说明',i+1) for i in range(4201)])
        await conn.commit()
    monkeypatch.setattr(runtime.providers,'embed',AsyncMock(return_value=[[0.0,0.0,1.0]]))
    monkeypatch.setattr(runtime.vectors,'search',AsyncMock(return_value=[]))
    original=runtime.db.rows
    fetched=[]

    async def observed(sql,args=()):
        result=await original(sql,args)
        if result and any('text' in row for row in result):
            fetched.append(len(result))
        return result

    monkeypatch.setattr(runtime.db,'rows',observed)
    result=await runtime.documents.search('alice',['实习生报销上限'],limit=3)
    assert result[0]['id']=='c04200'
    assert max(fetched)<=128
    assert 'alice' not in runtime.documents._corpora


async def test_large_corpus_vector_and_cached_provenance_obey_revoked_acl(runtime,monkeypatch):
    runtime.settings.rag_small_corpus_chunks=1
    await seed(runtime,[{'id':'public','title':'公开条款','text':'ALPHA public'}, {'id':'private','title':'私有条款','text':'ALPHA private'}, {'id':'public2','title':'普通资料','text':'OTHER policy'}])
    await runtime.db.execute("INSERT INTO collections VALUES('locked','alice','Locked','private','owner',0,?)",(now(),))
    await runtime.db.execute("INSERT INTO document_policies VALUES('private','alice','locked','owner',0)")
    await runtime.db.execute("INSERT INTO collection_grants VALUES('locked','reader','read')")
    monkeypatch.setattr(runtime.providers,'embed',AsyncMock(return_value=[[0.0,0.0,1.0]]))
    monkeypatch.setattr(runtime.vectors,'search',AsyncMock(return_value=[{'id':'private','score':1.0,'text':'FORGED'},{'id':'public','score':0.8}]))
    with paid_scope('alice','reader','evaluation'):
        before=await runtime.documents.search('alice',['ALPHA'])
        assert any(row['document_id']=='private' for row in before)
        assert await runtime.db.one("SELECT result FROM document_search_cache WHERE user_id='alice'")
        await runtime.db.execute("DELETE FROM collection_grants WHERE collection_id='locked'")
        result=await runtime.documents.search('alice',['ALPHA'])
    assert result and all(row['document_id']=='public' for row in result)
    assert all('FORGED' not in row['text'] for row in result)


async def test_upload_capacity_fails_before_storing_and_duplicate_is_idempotent(runtime,monkeypatch):
    runtime.settings.workspace_document_limit=1
    first=await runtime.documents.add('alice','first.txt',b'first policy')
    store=AsyncMock(wraps=runtime.documents.store.put)
    monkeypatch.setattr(runtime.documents.store,'put',store)
    from backend.core.reliability import ServiceError
    with pytest.raises(ServiceError) as failure:
        await runtime.documents.add('alice','second.txt',b'second policy')
    assert failure.value.code=='document_capacity'
    store.assert_not_awaited()
    assert (await runtime.documents.add('alice','first.txt',b'first policy'))['id']==first['id']


async def test_sql_candidate_dedup_does_not_starve_other_documents(runtime,monkeypatch):
    runtime.settings.rag_small_corpus_chunks=1
    await seed(runtime,[{'id':'duplicate','title':'重复资料','text':'ALPHA same'}, {'id':'second','title':'另一资料','text':'ALPHA other'}])
    async with runtime.db.connection() as conn:
        await conn.executemany("INSERT INTO chunks(id,document_id,user_id,text,locator,vector,ordinal) VALUES(?,'duplicate','alice','ALPHA same','正文','[]',?)",
                              [(f'duplicate-{i:04d}',i+1) for i in range(160)])
        await conn.commit()
    monkeypatch.setattr(runtime.providers,'embed',AsyncMock(return_value=[[0.0,0.0,1.0]]))
    monkeypatch.setattr(runtime.vectors,'search',AsyncMock(return_value=[]))
    result=await runtime.documents.search('alice',['ALPHA'],limit=2)
    assert {row['document_id'] for row in result}=={'duplicate','second'}


async def test_operational_backfill_resumes_small_transactions_idempotently(runtime):
    await seed(runtime,[{'id':f'doc-{i:02d}','title':'迁移资料','text':'制度与证据'} for i in range(65)])
    first=await runtime.documents.lexical_index.ensure('alice',max_batches=1)
    assert first['indexed_this_run']==32 and first['remaining']==33 and not first['complete']
    second=await runtime.documents.lexical_index.ensure('alice',max_batches=2)
    assert second['indexed_this_run']==33 and second['complete']
    third=await runtime.documents.lexical_index.ensure('alice',max_batches=1)
    assert third['indexed_this_run']==0 and third['complete']
    assert (await runtime.db.one('SELECT COUNT(*) AS total FROM lexical_chunks'))['total']==65


async def test_same_hash_upload_does_not_disclose_restricted_document_metadata(runtime):
    document=await runtime.documents.add('alice','private-name.txt',b'restricted bytes')
    await runtime.db.execute("INSERT INTO collections VALUES('locked','alice','Locked','private','owner',0,?)",(now(),))
    await runtime.db.execute("UPDATE document_policies SET collection_id='locked',owner_subject='owner' WHERE document_id=?",(document['id'],))
    from backend.core.reliability import ServiceError
    with paid_scope('alice','reader','evaluation'):
        with pytest.raises(ServiceError) as denied:
            await runtime.documents.add('alice','guessed-name.txt',b'restricted bytes')
    assert denied.value.code=='source_permission_denied'
    assert (await runtime.db.one("SELECT name FROM documents WHERE id=?",(document['id'],)))['name']=='private-name.txt'
