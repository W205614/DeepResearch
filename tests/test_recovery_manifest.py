import json
import ast
import subprocess
import tarfile
import io

import pytest

from scripts import recovery
from scripts.recovery import load_archive, sha, freeze_binds
from tests.image_bundle_fixture import bundle

IMAGE, HELPER_IMAGE = bundle()[1]


def archive(tmp_path):
    configuration = {'services': {'api': {'image': IMAGE}}, 'volumes': {'data': {}}}
    (tmp_path / 'deployment.private.json').write_text(json.dumps(configuration))
    for name in ('data.tgz', 'images.tar', 'restore.py', 'postgres.dump', 'keycloak-postgres.dump'):
        (tmp_path / name).write_bytes(b'fixture archive')
    (tmp_path/'images.tar').write_bytes(bundle()[0])
    manifest = {'version': 3, 'volumes': ['data'], 'images': {'api': IMAGE},
                'helper_image': HELPER_IMAGE, 'bind_mounts': [],
                'files': {p.name: sha(p) for p in tmp_path.iterdir()}}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    return manifest


def test_recovery_is_bound_to_backup_configuration_and_image_identity(tmp_path):
    archive(tmp_path)
    _, configuration = load_archive(tmp_path)
    assert configuration['services']['api']['image'] == IMAGE


def test_corrupt_backup_is_rejected_before_creating_resources(tmp_path):
    archive(tmp_path)
    (tmp_path / 'data.tgz').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        load_archive(tmp_path)


def test_path_escape_and_image_mismatch_are_rejected(tmp_path):
    manifest = archive(tmp_path)
    manifest['images']['api'] = 'different'
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='identities'):
        load_archive(tmp_path)
    manifest['files']['../escape'] = 'untrusted'
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='checksum/path'):
        load_archive(tmp_path)


@pytest.mark.parametrize('missing', ['data.tgz', 'images.tar', 'restore.py', 'postgres.dump', 'keycloak-postgres.dump', 'deployment.private.json'])
def test_required_file_cannot_be_omitted_from_manifest_before_any_docker_action(tmp_path, monkeypatch, missing):
    manifest = archive(tmp_path)
    del manifest['files'][missing]
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(recovery, 'run', lambda *a, **k: pytest.fail('No Docker action before complete verification'))
    with pytest.raises(ValueError, match='Required'):
        recovery.restore(tmp_path, 'dr-restore-validation')


def test_read_only_binds_are_frozen_and_original_files_are_not_required(tmp_path):
    source = tmp_path / 'original'
    source.mkdir()
    (source / 'fixture.py').write_text('ORIGINAL = True')
    destination = tmp_path / 'backup'
    destination.mkdir()
    config = {'services': {'agent': {'volumes': [{'type': 'bind', 'source': str(source), 'target': '/app/scripts', 'read_only': True}]}}}
    mounts = freeze_binds(config, destination)
    (source / 'fixture.py').write_text('CHANGED = True')
    assert (destination / mounts[0]['path'] / 'fixture.py').read_text() == 'ORIGINAL = True'
    assert config['services']['agent']['volumes'][0]['source'] == 'backup:binds/0000'
    assert mounts[0]['files'] == ['binds/0000/fixture.py']
    config['services']['agent']['volumes'][0].update(source=str(source), read_only=False)
    with pytest.raises(ValueError, match='read-only'):
        freeze_binds(config, destination)


def backup_commands(monkeypatch, running, fail_query=False):
    calls = []
    config = {'name': 'fixture-source', 'volumes': {'data': {'name': 'fixture-source_data'}},
              'services': {name: {'image': IMAGE} for name in ('postgres', 'keycloak-postgres', 'agent')}}
    def source_compose(*args):
        calls.append(args)
        if args[:1] == ('config',):
            return json.dumps(config).encode()
        if args[:2] == ('ps', '--status'):
            return '\n'.join(running).encode()
        if args[:2] == ('ps', '-aq'):
            return b'fixture-container'
        if args[:1] == ('exec',) and 'psql' in args:
            if fail_query:
                raise RuntimeError('query failed')
            return b'0' if 'count(*)' in args[-1] else b'0013_evidence_lifecycle'
        if 'pg_dump' in args:
            return b'dump'
        return b''
    def command(args, **kwargs):
        if args[:3] == ['docker', 'volume', 'ls']:
            return b'fixture-source_data'
        if args[:3] == ['docker', 'image', 'inspect']:
            return HELPER_IMAGE.encode()
        if args[:2] == ['docker', 'inspect']:
            if '.Mounts' in args[3]:
                return b'[]'
            return IMAGE.encode()
        if args[:3] == ['docker', 'image', 'save']:
            from pathlib import Path
            Path(args[args.index('-o') + 1]).write_bytes(bundle()[0])
            assert HELPER_IMAGE in args
        if args[:2] == ['docker', 'run']:
            from pathlib import Path
            destination = next(value.removesuffix(':/backup') for value in args if value.endswith(':/backup'))
            (Path(destination) / 'data.tgz').write_bytes(b'archive')
            assert HELPER_IMAGE in args
        if args[:2] == ['git', 'rev-parse']:
            return b'checkout-only-sha'
        return b''
    monkeypatch.setattr(recovery, 'compose', source_compose)
    monkeypatch.setattr(recovery, 'run', command)
    return calls


@pytest.mark.parametrize('running', [[], ['postgres', 'keycloak-postgres'], ['postgres', 'keycloak-postgres', 'agent']])
def test_backup_never_stops_empty_service_set_or_recreates_original_containers(tmp_path, monkeypatch, running):
    calls = backup_commands(monkeypatch, running)
    destination = recovery.backup(tmp_path / 'snapshot')
    assert ('stop',) not in calls
    assert not any(call[0] == 'up' for call in calls)
    if running:
        assert calls[-1] == ('start', '--wait', '--wait-timeout', '300', *running)
    else:
        assert calls[-1] == ('stop', 'postgres', 'keycloak-postgres')
    manifest, _ = load_archive(destination)
    assert manifest['helper_image'] == HELPER_IMAGE


def test_stopped_source_databases_return_to_stopped_state_if_preflight_query_fails(tmp_path, monkeypatch):
    calls = backup_commands(monkeypatch, [], fail_query=True)
    with pytest.raises(RuntimeError, match='query failed'):
        recovery.backup(tmp_path / 'failed-snapshot')
    assert calls[-1] == ('stop', 'postgres', 'keycloak-postgres')


def test_restore_failure_retains_every_created_volume_and_only_cleans_its_network(tmp_path, monkeypatch):
    archive(tmp_path)
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        if args[:2] == ['docker', 'run']:
            raise RuntimeError('extraction failed')
        return b''
    monkeypatch.setattr(recovery, 'run', command)
    monkeypatch.setattr(recovery.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0))
    with pytest.raises(RuntimeError, match='extraction failed'):
        recovery.restore(tmp_path, 'dr-restore-failed', subnet='10.254.245.0/24')
    assert not any(call[:3] == ['docker', 'volume', 'rm'] for call in calls)
    assert ['docker', 'network', 'rm', 'dr-restore-failed_isolated'] in calls
    created = next(call for call in calls if call[:3] == ['docker', 'network', 'create'])
    assert '--internal' in created and '--subnet' in created
    record = json.loads((tmp_path / 'restore-dr-restore-failed.json').read_text())
    assert record['created_volumes'] == ['dr-restore-failed_data']
    assert record['volumes_retained'] and not record['passed']


def test_live_bind_and_unpinned_helper_are_rejected(tmp_path):
    manifest = archive(tmp_path)
    config = json.loads((tmp_path / 'deployment.private.json').read_text())
    config['services']['api']['volumes'] = [{'type': 'bind', 'source': '/live/config', 'read_only': True}]
    (tmp_path / 'deployment.private.json').write_text(json.dumps(config))
    manifest['files']['deployment.private.json'] = sha(tmp_path / 'deployment.private.json')
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='live or writable'):
        load_archive(tmp_path)
    manifest['helper_image'] = 'python:latest'
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='immutable image'):
        load_archive(tmp_path)


def test_embedded_recovery_probes_compile_and_hash_real_newlines():
    compile(recovery.RESTORED_BUSINESS, '<business-recovery-probe>', 'exec')
    compile(recovery.RESTORED_RETRIEVAL, '<retrieval-recovery-probe>', 'exec')
    constants = [node.value for node in ast.walk(ast.parse(recovery.RESTORED_BUSINESS)) if isinstance(node, ast.Constant)]
    assert '\n' in constants and r'\n' not in constants


@pytest.mark.parametrize('project,labels', [
    ('deepresearch', {}),
    ('dr-opt-acceptance', {}),
    ('dr-restore-validation', {'com.docker.compose.project':'deepresearch','com.docker.compose.service':'postgres'}),
    ('dr-restore-validation', {'com.docker.compose.project':'dr-restore-validation','com.docker.compose.service':'agent'}),
])
def test_verifier_admin_dsn_cannot_escape_fresh_restore(project,labels,monkeypatch):
    calls=[]
    def inspect(args):
        calls.append(args)
        return json.dumps([{'Config':{'Labels':labels,'Env':['POSTGRES_PASSWORD=fixture-only']}}]).encode()
    monkeypatch.setattr(recovery,'run',inspect)
    with pytest.raises(ValueError):
        recovery.verification_database_environment(project,'fixture-container')
    if not project.startswith('dr-restore-'):
        assert not calls


def test_verifier_dsn_uses_effective_database_env_and_never_argv_password(monkeypatch):
    calls=[]
    def inspect(args):
        calls.append(args)
        return json.dumps([{'Config':{'Labels':{'com.docker.compose.project':'dr-restore-validation',
            'com.docker.compose.service':'postgres'},'Env':['POSTGRES_USER=restore@admin','POSTGRES_DB=restore db',
            'POSTGRES_PASSWORD=fixture$@/%literal']}}]).encode()
    monkeypatch.setattr(recovery,'run',inspect)
    environment=recovery.verification_database_environment('dr-restore-validation','fixture-container')
    assert environment=={'RECOVERY_VERIFICATION_PROJECT':'dr-restore-validation',
        'RECOVERY_DATABASE_URL':'postgresql://restore%40admin:fixture%24%40%2F%25literal@postgres:5432/restore%20db'}
    assert calls==[['docker','inspect','fixture-container']]
    assert 'DATABASE_URL' not in environment


def test_business_probe_audience_and_acl_are_synthetic_subject_scoped():
    source=recovery.RESTORED_BUSINESS
    assert 'included.custom.audience' in source and 'included.client.audience' not in source
    assert "asyncpg.connect(os.environ['RECOVERY_DATABASE_URL'])" in source
    assert "VALUES($1,$2,'read') ON CONFLICT DO NOTHING" in source
    assert "policy['collection_id'],users[1]" in source
    assert "('collection_grants','memberships') if acl_contract['collection_acl'] else ('memberships',)" in source
    assert "WHERE subject=ANY($1::text[])',users" in source
    assert 'UPDATE document_policies' not in source and 'UPDATE report_publications' not in source
    assert "'http://127.0.0.1:9'" in source and "'RECOVERY_ACCEPTANCE_ONLY'" in source


@pytest.mark.asyncio
async def test_recovery_viewer_gets_only_selected_restricted_collection_read():
    module=ast.parse(recovery.RESTORED_BUSINESS)
    loop=next(node for node in ast.walk(module) if isinstance(node,ast.For)
              and isinstance(node.target,ast.Name) and node.target.id=='source'
              and any(isinstance(child,ast.Constant) and isinstance(child.value,str)
                      and 'SELECT p.collection_id' in child.value for child in ast.walk(node)))
    function=ast.parse('async def prepare(db,workspace,original,users):\n granted_collections=set()\n return granted_collections').body[0]
    function.body.insert(1,loop)
    namespace={}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'<recovery-acl-preparation>','exec'),namespace)
    class Database:
        def __init__(self):
            self.reads=[]
            self.writes=[]
        async def fetchrow(self,sql,document,workspace):
            self.reads.append((document,workspace))
            assert 'd.id=$1 AND d.user_id=$2' in sql
            return {'collection_id':'only-selected' if document=='restricted' else 'public-collection',
                    'visibility':'restricted' if document=='restricted' else 'workspace'}
        async def execute(self,sql,*args):
            self.writes.append((sql,args))
    db=Database()
    sources=[{'kind':'local','document_id':'restricted'},{'kind':'local','document_id':'public'},
             {'kind':'web','document_id':'not-an-authorized-local-doc'}]
    granted=await namespace['prepare'](db,'selected-workspace',sources,['new-admin','new-viewer'])
    assert granted=={'only-selected'}
    assert db.reads==[('restricted','selected-workspace'),('public','selected-workspace')]
    assert len(db.writes)==1
    sql,args=db.writes[0]
    assert 'INSERT INTO collection_grants' in sql and "'read'" in sql
    assert args==('only-selected','new-viewer')


@pytest.mark.asyncio
@pytest.mark.parametrize('present,head,mode',[
    ((True,True,True),'0017_product_governance','current_collection_acl'),
    ((False,False,False),'0013_evidence_lifecycle','legacy_workspace_acl'),
    ((False,False,False),'0016_service_privileges','legacy_workspace_acl'),
    ((True,False,False),'0013_evidence_lifecycle',None),
    ((False,False,False),'0017_product_governance',None),
])
async def test_recovery_acl_contract_requires_complete_current_or_explicit_legacy(present,head,mode):
    module=ast.parse(recovery.RESTORED_BUSINESS)
    function=next(node for node in module.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='restored_acl_contract')
    namespace={}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'<recovery-schema-contract>','exec'),namespace)
    class Database:
        async def fetchrow(self,sql):
            assert 'to_regclass' in sql
            return dict(zip(('policies','collections','grants'),present))
        async def fetch(self,sql):
            assert sql=='SELECT version_num FROM alembic_version'
            return [{'version_num':head}]
    if mode is None:
        with pytest.raises(AssertionError):
            await namespace['restored_acl_contract'](Database())
    else:
        result=await namespace['restored_acl_contract'](Database())
        assert result['mode']==mode
        assert result['collection_acl']==all(present)


def test_runtime_volumes_and_clamav_container_layer_are_materialized(monkeypatch):
    config = {'name':'source','volumes':{'data':{'name':'source_data'}},
              'services':{'agent':{'image':IMAGE},'clamav':{'image':IMAGE}}}
    def inspect(args, **kwargs):
        if args[-1] == 'clamav-id':
            return b'[]'
        return json.dumps([{'Type':'volume','Name':'anonymous-data','Destination':'/runtime','RW':True}]).encode()
    monkeypatch.setattr(recovery, 'run', inspect)
    volumes, assets = recovery.materialize_runtime_storage(config, lambda *args: (args[-1]+'-id').encode())
    assert 'anonymous-data' in volumes.values()
    assert assets == [{'volume':'runtime-clamav-database','service':'clamav','container':'clamav-id','path':'/var/lib/clamav'}]
    assert config['services']['clamav']['volumes'][0]['target'] == '/var/lib/clamav'
    assert 'runtime-clamav-database' in config['volumes']


def test_old_snapshot_missing_clamav_database_is_rejected_before_resources(tmp_path, monkeypatch):
    manifest = archive(tmp_path)
    config = json.loads((tmp_path/'deployment.private.json').read_text())
    config['services']['clamav'] = {'image':IMAGE}
    manifest['images']['clamav'] = IMAGE
    (tmp_path/'deployment.private.json').write_text(json.dumps(config))
    manifest['files']['deployment.private.json'] = sha(tmp_path/'deployment.private.json')
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(recovery,'run',lambda *a,**k: pytest.fail('Missing runtime state must fail before Docker actions'))
    with pytest.raises(ValueError,match='ClamAV runtime database'):
        recovery.restore(tmp_path,'dr-restore-incomplete')


def test_failure_diagnostics_exclude_environment_commands_and_health_output(monkeypatch):
    commands = []
    def inspect(args, **kwargs):
        commands.append(args)
        if args[:2] == ['docker','ps']:
            return b'one-container'
        return json.dumps({'service':'clamav','state':'restarting','health':'unhealthy','exit_code':1,'oom_killed':False,'restarts':3}).encode()
    monkeypatch.setattr(recovery,'run',inspect)
    assert recovery.safe_container_states('dr-restore-test')[0]['restarts'] == 3
    assert '"oom_killed":{{json .State.OOMKilled}}' in commands[1][3]
    assert all('.Config.Env' not in arg and '.Config.Cmd' not in arg and '.Health.Log' not in arg for call in commands for arg in call)


@pytest.mark.parametrize('prefix,accepted',[('',True),('./',True),('clamav/',False)])
def test_clamav_archive_exposes_database_files_at_mount_root(tmp_path,prefix,accepted):
    path = tmp_path/'signatures.tgz'
    with tarfile.open(path,'w:gz') as archive:
        for name in ('main.cvd','daily.cld'):
            contents = b'nonempty signature fixture'
            member = tarfile.TarInfo(prefix+name)
            member.size = len(contents)
            archive.addfile(member,io.BytesIO(contents))
    if accepted:
        recovery.validate_clamav_database(path)
    else:
        with pytest.raises(ValueError,match='nested'):
            recovery.validate_clamav_database(path)
