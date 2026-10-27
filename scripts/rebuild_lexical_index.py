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
        if args.all_workspaces:
            workspaces=await db.rows('SELECT DISTINCT user_id AS id FROM documents ORDER BY user_id')
        else:
            workspaces=[{'id':args.workspace}]
        for workspace in workspaces:
            admin=await db.one("SELECT subject FROM memberships WHERE workspace_id=? AND role='admin' ORDER BY subject LIMIT 1",(workspace['id'],))
            if not admin and not settings.demo_mode:
                results.append({'workspace_id':workspace['id'],'complete':False,'reason':'no_current_workspace_admin'})
                continue
            def progress(value):
                if value['batches'] % 10 == 0:
                    print(json.dumps({'phase':'backfill',**value}),flush=True)
            with paid_scope(workspace['id'],admin['subject'] if admin else workspace['id'],'maintenance'):
                result=await documents.lexical_index.ensure(workspace['id'],max_batches=args.max_batches,progress=progress)
            print(json.dumps({'phase':'completed' if result['complete'] else 'resume_required',**result}),flush=True)
            results.append(result)
        if args.output:
            Path(args.output).write_text(json.dumps({'supplier_calls':0,'results':results},indent=2)+'\n',encoding='utf-8')
        return all(result['complete'] for result in results)
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
