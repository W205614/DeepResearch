"""Explicit isolated PostgreSQL/checkpoint race probe; no supplier calls or workers."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
import subprocess
from typing import TypedDict
from urllib.parse import quote
from unittest.mock import AsyncMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from langgraph.graph import StateGraph, END  # noqa:E402
from backend.core.config import Settings  # noqa:E402
from backend.core.db import uid,now  # noqa:E402
from backend.domain.models import RunRequest  # noqa:E402
from backend.services.runtime import Runtime,ConflictError  # noqa:E402


async def verify():
    settings=Settings()
    if os.environ.get('ISOLATED_RELIABILITY_PROBE')!='dr-opt-acceptance':
        raise ValueError('Explicit isolated candidate marker required')
    settings=settings.model_copy(update={'checkpoint_schema_managed':True,'vector_collection_prefix':'optreliability20261026',
        'milvus_uri':'http://milvus:19530','embedding_dimension':64,'embedding_model':'fixture-hash-64'})
    creator,deleter=Runtime(settings,worker_mode=True),Runtime(settings,worker_mode=True)
    workspace='reliability-probe-'+uid()
    results={'workspace':workspace,'supplier_http':0,'provider_calls':0}
    try:
        for runtime in (creator,deleter):
            await runtime.db.init()
            role=(await runtime.db.one('SELECT current_user AS role'))['role']
            results['database_role']=role
            if role!='deepresearch':
                raise ValueError('Probe uses only the explicitly isolated database owner fixture')
            await runtime._open_postgres_checkpointer(settings.database_url.replace('postgresql+asyncpg://','postgresql://',1))
            runtime.schedule=AsyncMock()
        await creator.db.ensure_personal_workspace(workspace,'Isolated reliability fixture',(0,4))
        # Delete wins after the creator has already resolved the old thread.
        thread=await creator.new_thread(workspace,'delete wins')
        found,release=asyncio.Event(),asyncio.Event()
        original_find=creator.find_thread
        async def paused_find(ref,user):
            value=await original_find(ref,user)
            found.set()
            await release.wait()
            return value
        creator.find_thread=paused_find
        pending=asyncio.create_task(creator.create(workspace,RunRequest(topic='fixture delete wins',thread_id=thread['id'],data_policy='public',client_request_id=uid())))
        await asyncio.wait_for(found.wait(),5)
        await deleter.delete_thread(thread['id'],workspace)
        release.set()
        try:
            await pending
            raise AssertionError('Create accepted a deleted thread')
        except ConflictError:
            pass
        creator.find_thread=original_find
        results['delete_before_insert_rejected']=True
        # Create wins the row lock first; deletion must wait, then rescan runs.
        thread=await creator.new_thread(workspace,'create wins')
        locked,release=asyncio.Event(),asyncio.Event()
        original_connection=creator.db.connection
        @asynccontextmanager
        async def paused_connection():
            async with original_connection() as conn:
                original_rows=conn.rows
                async def rows(sql,args=()):
                    value=await original_rows(sql,args)
                    if 'FOR KEY SHARE' in sql:
                        locked.set()
                        await release.wait()
                    return value
                conn.rows=rows
                yield conn
        creator.db.connection=paused_connection
        pending=asyncio.create_task(creator.create(workspace,RunRequest(topic='fixture create wins',thread_id=thread['id'],data_policy='public',client_request_id=uid())))
        await asyncio.wait_for(locked.wait(),5)
        deleting=asyncio.create_task(deleter.delete_thread(thread['id'],workspace))
        await asyncio.sleep(.15)
        assert not deleting.done()
        release.set()
        await asyncio.wait_for(asyncio.gather(pending,deleting),10)
        creator.db.connection=original_connection
        assert not await creator.db.one('SELECT r.id FROM runs r LEFT JOIN threads t ON t.id=r.thread_id WHERE r.user_id=? AND t.id IS NULL',(workspace,))
        results['create_row_lock_blocks_delete_then_rescans']=True
        # Use a real Postgres checkpoint written by a tiny local graph.
        run=await creator.create(workspace,RunRequest(topic='fixture checkpoint retry',data_policy='public',client_request_id=uid()))
        await creator.db.execute("UPDATE runs SET status='completed' WHERE id=?",(run['id'],))
        class State(TypedDict):
            value:int
        graph=StateGraph(State)
        graph.add_node('local',lambda state:{'value':state['value']+1})
        graph.set_entry_point('local')
        graph.add_edge('local',END)
        config={'configurable':{'thread_id':run['id']}}
        await graph.compile(checkpointer=creator.checkpointer).ainvoke({'value':0},config)
        assert await creator.checkpointer.aget_tuple(config)
        original_delete=deleter.checkpointer.adelete_thread
        deleter.checkpointer.adelete_thread=AsyncMock(side_effect=OSError('injected component outage'))
        deleted=await deleter.delete_thread(run['thread_id'],workspace)
        assert deleted['cleanup_pending']
        assert await creator.checkpointer.aget_tuple(config)
        assert await creator.db.one('SELECT run_id FROM run_cleanup WHERE run_id=?',(run['id'],))
        deleter.checkpointer.adelete_thread=original_delete
        await deleter.cleanup_runs()
        assert not await creator.checkpointer.aget_tuple(config)
        assert not await creator.db.one('SELECT run_id FROM run_cleanup WHERE run_id=?',(run['id'],))
        results['checkpoint_failure_keeps_tombstone_then_real_retry_cleans']=True
        # Real Milvus stale hit must be rejected by SQL hydration, while unknown
        # semantic-memory provenance is retained instead of guessed/deleted.
        run=await creator.create(workspace,RunRequest(topic='fixture memory boundary',data_policy='public',client_request_id=uid()))
        await creator.db.execute("UPDATE runs SET status='completed' WHERE id=?",(run['id'],))
        memory_id='s'+uid()
        content='orphan-source-fixture-never-include'
        vector=[1.0]+[0.0]*63
        await creator.db.execute("INSERT INTO memories(id,user_id,kind,content,run_id,created_at,vector) VALUES(?,?,'semantic',?,?,?,'[]')",
            (memory_id,workspace,content,uid(),now()))
        await creator.db.execute("INSERT INTO memories(id,user_id,kind,content,run_id,created_at,vector,owner_subject) VALUES(?,?,'preference',?,'',?,'[]',?)",
            (uid(),workspace,'independent preference retained',now(),workspace))
        await creator.vectors.upsert('memories',[{'id':memory_id,'user_id':workspace,'thread_id':run['thread_id'],
            'document_id':'','locator':'fixture','title':'fixture','text':content,'vector':vector}])
        hits=await creator.vectors.search('memories',workspace,vector,3,run['thread_id'])
        assert any(hit['id']==memory_id for hit in hits)
        creator.providers.embed=AsyncMock(return_value=[vector])
        context=await creator.context(dict(run,data_policy='internal'))
        assert content not in context and 'independent preference retained' in context
        assert await creator.db.one('SELECT id FROM memories WHERE id=?',(memory_id,))
        results['real_milvus_orphan_hit_rejected_by_sql_context']=True
        results['unknown_semantic_provenance_physically_retained']=True
        results['independent_preference_retained']=True
        # Document vector tombstones keep their retry record until the real
        # isolated collection deletion succeeds, without touching any original.
        document_id=uid()
        import hashlib
        chunk_id=hashlib.sha256((document_id+':1:0').encode()).hexdigest()[:32]
        await creator.vectors.upsert('documents',[{'id':chunk_id,'user_id':workspace,'document_id':document_id,
            'locator':'fixture','title':'fixture','text':'obsolete derived vector','vector':vector}])
        await creator.db.execute("INSERT INTO document_cleanup(document_id,user_id,version,object_key) VALUES(?,?,1,'')",(document_id,workspace))
        real_delete=creator.vectors.delete_document_version
        creator.vectors.delete_document_version=AsyncMock(side_effect=OSError('injected vector outage'))
        await creator.documents.cleanup_pending()
        assert await creator.db.one('SELECT document_id FROM document_cleanup WHERE document_id=?',(document_id,))
        creator.vectors.delete_document_version=real_delete
        await creator.documents.cleanup_pending()
        assert not await creator.db.one('SELECT document_id FROM document_cleanup WHERE document_id=?',(document_id,))
        client,collection=creator.vectors.ensure('documents')
        assert not await asyncio.to_thread(client.get,collection_name=collection,ids=[chunk_id])
        results['document_vector_failure_keeps_tombstone_then_real_retry_cleans']=True
        results['passed']=True
        results['scope']='Real isolated PG owner fixture transactions, AsyncPostgresSaver and independent Milvus collections; not production dr_python admission permission evidence. Synthetic local graph/embedding, no model HTTP, worker startup or service restart.'
        return results
    finally:
        for runtime in (creator,deleter):
            await runtime.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--initialize',action='store_true')
    args=parser.parse_args()
    if os.environ.get('ISOLATED_RELIABILITY_PROBE')!='dr-opt-acceptance':
        raise SystemExit('Explicit isolated marker required')
    os.environ['DATABASE_URL']='postgresql://deepresearch:'+quote(os.environ['POSTGRES_PASSWORD'],safe='')+'@postgres:5432/dr_opt_reliability'
    if args.initialize:
        migration_env={**os.environ,'DATABASE_URL':os.environ['DATABASE_URL'].replace('postgresql://','postgresql+psycopg://',1)}
        result=subprocess.run([sys.executable,'-m','alembic','upgrade','head'],capture_output=True,env=migration_env)
        if result.returncode:
            import re
            detail=result.stderr.decode(errors='replace')
            classes=sorted(set(re.findall(r'\b[A-Z][A-Za-z]*(?:Error|Exception)\b|No module named \'[a-zA-Z0-9_.]+\'',detail)))
            raise SystemExit('Isolated schema initialization failed: '+','.join(classes)+'; private driver output withheld')
        async def checkpoints():
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            async with AsyncPostgresSaver.from_conn_string(os.environ['DATABASE_URL']) as saver:
                await saver.setup()
        asyncio.run(checkpoints())
    report=asyncio.run(verify())
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
