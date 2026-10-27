"""Offline maintenance readiness; source rows/ACL are never repaired by backfill."""
import json
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.core.config import Settings
from backend.core.db import Database, now
from backend.core.usage_governance import paid_scope
from backend.services.document_access import require_document
from scripts import migrate_services, rebuild_lexical_index


@pytest.fixture
async def maintenance(tmp_path, monkeypatch):
    settings=Settings(_env_file=None,demo_mode=False,data_dir=tmp_path,database_url='')
    db=Database(tmp_path/'research.sqlite3')
    await db.init()
    monkeypatch.setattr(rebuild_lexical_index,'Settings',lambda:settings)
    args=Namespace(all_workspaces=True,workspace=None,max_batches=10000,output=str(tmp_path/'receipt.json'))
    return db,args


async def seed(db, *, workspace=False, role=None, status='ready', private=False):
    if workspace:
        await db.execute('INSERT INTO workspaces VALUES(?,?,?,?)',('legacy','Space','owner',now()))
    if role:
        await db.execute('INSERT INTO memberships VALUES(?,?,?,?)',('legacy','owner',role,now()))
    await db.execute('''INSERT INTO documents(id,user_id,name,hash,status,created_at,updated_at,index_version)
        VALUES('doc','legacy','Policy','hash',?,?,?,1)''',(status,now(),now()))
    await db.execute("""INSERT INTO chunks(id,document_id,user_id,text,locator,vector,ordinal)
        VALUES('chunk','doc','legacy','访客登记制度','正文','[]',0)""")
    if private:
        await db.execute("INSERT INTO collections VALUES('locked','legacy','Private','private','owner',0,?)",(now(),))
        await db.execute("INSERT INTO document_policies VALUES('doc','legacy','locked','owner',0)")


def receipt(args):
    with open(args.output,encoding='utf-8') as source:
        return json.load(source)


@pytest.mark.parametrize('state',['deleted','failed','indexed_ready'])
async def test_no_eligible_work_does_not_require_admin(maintenance,state):
    db,args=maintenance
    await seed(db,status='ready' if state=='indexed_ready' else state)
    if state=='indexed_ready':
        await db.execute("INSERT INTO lexical_chunks VALUES('chunk','legacy','doc','digest',1)")
    assert await rebuild_lexical_index.run(args)
    result=receipt(args)
    assert result['results'][0]['status']=='no_work'
    assert result['initial_missing_chunks']==0 and result['all_eligible_indexed']
    assert result['supplier_calls']==0


async def test_orphan_is_deferred_without_marker_and_restored_workspace_blocks(maintenance):
    db,args=maintenance
    await seed(db,private=True)
    original=await db.rows('SELECT * FROM documents')
    policy=await db.rows('SELECT * FROM document_policies')
    assert await rebuild_lexical_index.run(args)
    result=receipt(args)
    assert result['results'][0]['status']=='deferred_unservable'
    assert result['ready_for_currently_servable_corpus'] and not result['all_eligible_indexed']
    assert result['remaining_missing_chunks']==result['deferred_unservable_chunks']==1
    assert not await db.rows('SELECT * FROM lexical_chunks')
    assert await db.rows('SELECT * FROM documents')==original
    assert await db.rows('SELECT * FROM document_policies')==policy
    await db.execute('INSERT INTO workspaces VALUES(?,?,?,?)',('legacy','Restored','owner',now()))
    assert not await rebuild_lexical_index.run(args)
    assert receipt(args)['blocked_workspaces']==['legacy']
    await db.execute('INSERT INTO memberships VALUES(?,?,?,?)',('legacy','owner','admin',now()))
    assert await rebuild_lexical_index.run(args)
    assert receipt(args)['all_eligible_indexed']
    assert (await db.one('SELECT COUNT(*) AS total FROM lexical_chunks'))['total']==1


@pytest.mark.parametrize('role',[None,'viewer','researcher'])
async def test_existing_workspace_missing_private_chunk_requires_admin(maintenance,role):
    db,args=maintenance
    await seed(db,workspace=True,role=role,private=True)
    # A viewer/researcher is the private document owner and can read it; absence
    # of an administrator still cannot turn this maintenance result into ready.
    if role:
        with paid_scope('legacy','owner','maintenance'):
            await require_document(db,'legacy','doc')
    assert not await rebuild_lexical_index.run(args)
    result=receipt(args)
    assert result['blocked_workspaces']==['legacy']
    assert result['deferred_unservable_chunks']==0
    assert result['results'][0]['reason']=='no_current_workspace_admin'
    assert not await db.rows('SELECT * FROM lexical_chunks')


async def test_orphan_admin_membership_is_not_a_current_workspace_admin(maintenance):
    db,args=maintenance
    await seed(db,role='admin')
    assert not await rebuild_lexical_index.run(args)
    assert receipt(args)['blocked_workspaces']==['legacy']
    assert not await db.rows('SELECT * FROM lexical_chunks')


def test_migrate_reports_actual_backfill_exit_and_receipt_without_driver_output(monkeypatch):
    child=Mock(side_effect=[SimpleNamespace(returncode=0),SimpleNamespace(returncode=2,stderr=b'PRIVATE_DSN')])
    monkeypatch.setattr(migrate_services.subprocess,'run',child)
    monkeypatch.setenv('DATABASE_URL','postgresql+asyncpg://private')
    with pytest.raises(SystemExit) as error:
        migrate_services.main()
    assert 'exit 2' in str(error.value) and '/data/lexical-backfill.json' in str(error.value)
    assert 'PRIVATE_DSN' not in str(error.value) and 'postgresql' not in str(error.value)
