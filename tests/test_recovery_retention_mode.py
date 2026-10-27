"""Direct owned-resource retention mocks; never invokes Docker or a real backup."""
import json
import subprocess
from pathlib import Path

import pytest

from scripts import recovery, scheduled_backup
from tests.test_recovery_manifest import archive, backup_commands


def test_helper_retention_preserves_names_labels_and_creator_proof(monkeypatch):
    calls=[]
    actual={}
    def command(args,**kwargs):
        calls.append(args)
        if args[:2]==['docker','run'] and '--name' in args:
            actual['name']=args[args.index('--name')+1]
            for index,value in enumerate(args):
                if value=='--label':
                    key,data=args[index+1].split('=',1)
                    actual[key.rsplit('.',1)[-1]]=data
        if args[:2]==['docker','inspect']:
            return json.dumps({**actual,'name':'/'+actual['name'],'container_id':'fixture-container',
                               'image_id':'fixture-image'}).encode()
        return b'output'
    monkeypatch.setattr(recovery,'run',command)
    assert recovery.run_container_helper(['fixture-image','true'],retain=False,project='deepresearch',operation='op',records=[],purpose='test')==b'output'
    assert '--rm' in calls[0]
    records=[]
    recovery.run_container_helper(['fixture-image','true'],retain=True,project='dr-restore-fixture',operation='uniqueop',records=records,purpose='restore-extract')
    retained_run=next(args for args in calls if '--name' in args)
    assert '--rm' not in retained_run
    assert records[0]['name']=='dr-restore-fixture-helper-uniqueop-000'
    assert records[0]['creator_proof_verified'] and records[0]['container_id']=='fixture-container'


def test_restore_retention_failure_only_stops_new_project_without_removal(tmp_path,monkeypatch):
    archive(tmp_path)
    calls=[]
    def command(args,**kwargs):
        calls.append(args)
        return b'fixture-network-id' if args[:3]==['docker','network','create'] else b''
    def extraction(*args,**kwargs):
        assert kwargs['retain'] is True and kwargs['project']=='dr-restore-retained'
        raise RuntimeError('synthetic extraction failure')
    monkeypatch.setattr(recovery,'run',command)
    monkeypatch.setattr(recovery,'run_container_helper',extraction)
    monkeypatch.setattr(recovery.subprocess,'run',lambda *args,**kwargs:subprocess.CompletedProcess(args,0))
    with pytest.raises(RuntimeError,match='synthetic extraction failure'):
        recovery.restore(tmp_path,'dr-restore-retained',retain_temporary_resources=True)
    assert any(args[:2]==['docker','compose'] and args[-1]=='stop' for args in calls)
    assert not any('--rm' in args or 'down' in args or args[:3]==['docker','network','rm'] for args in calls)
    report=json.loads((tmp_path/'restore-dr-restore-retained.json').read_bytes())
    assert report['intentional_retention'] is True and report['cleanup_performed'] is False
    assert report['cleanup_complete'] is False
    assert report['retained_resources']['network_id']=='fixture-network-id'
    assert report['created_volumes']==['dr-restore-retained_data']


def test_scheduler_explicitly_forwards_boolean_retention_without_real_backup(tmp_path,monkeypatch):
    captured={}
    monkeypatch.setattr(scheduled_backup,'within_window',lambda _:True)
    monkeypatch.setattr(scheduled_backup,'backup',lambda *args,**kwargs:captured.update(kwargs))
    monkeypatch.setattr(scheduled_backup,'load_archive',lambda _:({},{}))
    monkeypatch.setattr(scheduled_backup,'probe',lambda *args:{'version':1})
    result=scheduled_backup.execute({'enabled':True,'maintenance_window_acknowledged':True,
        'backup_root':str(tmp_path/'backup'),'host_status_file':str(tmp_path/'host.json'),
        'retain_temporary_resources':True})
    assert captured['retain_temporary_resources'] is True
    assert result['retain_temporary_resources'] is True
    source=recovery.__file__
    assert "parser.add_argument('--retain-temporary-resources'" in Path(source).read_text(encoding='utf-8')


def test_completed_backup_retention_receipt_is_hashed_once_and_not_rewritten_in_finally(tmp_path,monkeypatch):
    backup_commands(monkeypatch,[])
    writes=[]
    original=Path.write_text
    def write(path,*args,**kwargs):
        if path.name=='retained-resources.json':
            writes.append(path)
        return original(path,*args,**kwargs)
    def helper(arguments,**kwargs):
        assert kwargs['retain'] is True
        directory=next(value.removesuffix(':/backup') for value in arguments if value.endswith(':/backup'))
        (Path(directory)/'data.tgz').write_bytes(b'synthetic archive')
        kwargs['records'].append({'name':'owned-fixture','container_id':'fixture-id','creator_proof_verified':True})
        return b''
    monkeypatch.setattr(Path,'write_text',write)
    monkeypatch.setattr(recovery,'run_container_helper',helper)
    destination=recovery.backup(tmp_path/'snapshot',retain_temporary_resources=True)
    manifest,_=recovery.load_archive(destination)
    assert len(writes)==1
    assert manifest['files']['retained-resources.json']==recovery.sha(destination/'retained-resources.json')
    assert manifest['retained_resources']['intentional_retention'] is True
