import json
import ast
import subprocess
import tarfile
import io

import pytest

from scripts import recovery
from scripts.recovery import load_archive, sha, freeze_binds

IMAGE = 'sha256:' + 'a' * 64
HELPER_IMAGE = 'sha256:' + 'b' * 64


def archive(tmp_path):
    configuration = {'services': {'api': {'image': IMAGE}}, 'volumes': {'data': {}}}
    (tmp_path / 'deployment.private.json').write_text(json.dumps(configuration))
    for name in ('data.tgz', 'images.tar', 'restore.py', 'postgres.dump', 'keycloak-postgres.dump'):
        (tmp_path / name).write_bytes(b'fixture archive')
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
            Path(args[args.index('-o') + 1]).write_bytes(b'images')
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
