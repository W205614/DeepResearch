"""Resumable derived-index backfill; no model, embedding, vector or object-store calls."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core.config import Settings  # noqa: E402
from backend.core.db import Database  # noqa: E402
from backend.core.postgres import PostgresDatabase  # noqa: E402
from backend.core.usage_governance import paid_scope  # noqa: E402
from backend.services.documents import Documents  # noqa: E402


async def pending_work(db, workspace=None):
    # Discovery is deliberately independent of the caller's document ACL: an
    # invisible private document must not make a live workspace look complete.
    return await db.rows('''SELECT c.user_id AS id,COUNT(*) AS missing_chunks
        FROM chunks c JOIN documents d ON d.id=c.document_id AND d.user_id=c.user_id
        LEFT JOIN lexical_chunks l ON l.chunk_id=c.id
        WHERE d.status IN ('ready','rebuilding') AND l.chunk_id IS NULL'''
        + (' AND c.user_id=?' if workspace is not None else '')
        + ' GROUP BY c.user_id ORDER BY c.user_id', (workspace,) if workspace is not None else ())


async def unservable_orphan(db, workspace):
    state=await db.one('''SELECT EXISTS(SELECT 1 FROM workspaces WHERE id=?) AS workspace_exists,
        (SELECT COUNT(*) FROM memberships WHERE workspace_id=?) AS member_count''', (workspace,workspace))
    return not state['workspace_exists'] and state['member_count']==0


async def run(args):
    settings=Settings()
    if settings.database_url:
        db=PostgresDatabase(settings.database_url)
        await db.init()  # Connection pool only; the migrator owns schema DDL.
    else:
        path=settings.data_dir/'research.sqlite3'
        if not path.is_file():
            raise ValueError('Migrate/create the database first; this command does not initialize source tables')
        db=Database(path)
    documents=Documents(settings,db,None,None)
    results=[]
    try:
        selected_workspace=None if args.all_workspaces else args.workspace
        workspaces=await pending_work(db,selected_workspace)
        initial_missing=sum(workspace['missing_chunks'] for workspace in workspaces)
        for workspace in workspaces:
            admin=await db.one("""SELECT m.subject FROM memberships m JOIN workspaces w ON w.id=m.workspace_id
                WHERE m.workspace_id=? AND m.role='admin' ORDER BY m.subject LIMIT 1""",(workspace['id'],))
            if not admin and not settings.demo_mode:
                deferred=await unservable_orphan(db,workspace['id'])
                results.append({'workspace_id':workspace['id'],'complete':False,
                                'status':'deferred_unservable' if deferred else 'blocked',
                                'remaining':workspace['missing_chunks'],
                                'reason':'workspace_absent_and_no_members' if deferred else 'no_current_workspace_admin'})
                continue
            def progress(value):
                if value['batches'] % 10 == 0:
                    print(json.dumps({'phase':'backfill',**value}),flush=True)
            with paid_scope(workspace['id'],admin['subject'] if admin else workspace['id'],'maintenance'):
                result=await documents.lexical_index.ensure(workspace['id'],max_batches=args.max_batches,progress=progress)
            print(json.dumps({'phase':'completed' if result['complete'] else 'resume_required',**result}),flush=True)
            results.append(result)
        # A second unfiltered census prevents ACL-filtered ensure() or new work
        # from being mistaken for complete coverage. No deferred completion flag
        # is persisted: restoring a workspace/member makes the next check block.
        remaining=await pending_work(db,selected_workspace)
        deferred_chunks=0
        blocked=[]
        for workspace in remaining:
            if await unservable_orphan(db,workspace['id']):
                deferred_chunks+=workspace['missing_chunks']
            else:
                blocked.append(workspace['id'])
        ready=not blocked
        if not workspaces and not remaining:
            results.append({'workspace_id':selected_workspace,'status':'no_work','complete':True,'remaining':0})
        receipt={'supplier_calls':0,'results':results,'initial_missing_chunks':initial_missing,
                 'remaining_missing_chunks':sum(workspace['missing_chunks'] for workspace in remaining),
                 'deferred_unservable_chunks':deferred_chunks,'blocked_workspaces':blocked,
                 'all_eligible_indexed':not remaining,'ready_for_currently_servable_corpus':ready,
                 'coverage':'ready_and_rebuilding_tenant_matched_chunks'}
        if args.output:
            Path(args.output).write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'phase':'readiness',**receipt}),flush=True)
        return ready
    finally:
        await documents.close()
        if hasattr(db,'close'):
            await db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--workspace')
    group.add_argument('--all-workspaces',action='store_true')
    parser.add_argument('--max-batches',type=int,default=1000)
    parser.add_argument('--output')
    args=parser.parse_args()
    if not 1<=args.max_batches<=10000:
        parser.error('--max-batches must be between 1 and 10000')
    if sys.platform=='win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(0 if asyncio.run(run(args)) else 2)
