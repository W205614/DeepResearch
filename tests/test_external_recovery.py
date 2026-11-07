import json
from unittest.mock import Mock

import pytest

from scripts.external_recovery import config, digest, manifest, pg_environment, pg_tool


def test_pg_credentials_and_ca_are_passed_only_in_subprocess_environment(monkeypatch):
    invoked = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr('scripts.external_recovery.subprocess.run', invoked)
    url = 'postgresql://recovery:fixture-secret@isolated-pg:5433/restore?sslmode=verify-full&sslrootcert=%2Fca.pem'
    pg_tool('pg_dump', url, ['--format=custom'])
    arguments, options = invoked.call_args.args[0], invoked.call_args.kwargs
    assert 'fixture-secret' not in ' '.join(arguments)
    assert options['env']['PGPASSWORD'] == 'fixture-secret'
    assert options['env']['PGSSLROOTCERT'] == '/ca.pem'
    assert options['env']['PGSSLMODE'] == 'verify-full'
    assert options['env']['PGDATABASE'] == 'restore'


def test_pg_private_driver_failure_is_not_exposed(monkeypatch):
    monkeypatch.setattr('scripts.external_recovery.subprocess.run', Mock(return_value=Mock(returncode=1,
                        stderr=b'super-secret', stdout=b'super-secret')))
    with pytest.raises(RuntimeError) as failure:
        pg_tool('pg_restore', 'postgresql://user:super-secret@isolated-pg/restore', [])
    assert 'super-secret' not in str(failure.value)


def test_pg_environment_does_not_inherit_a_different_target_database(monkeypatch):
    monkeypatch.setenv('PGSERVICE', 'daily-production')
    monkeypatch.setenv('PGDATABASE', 'daily')
    environment = pg_environment('postgresql://user:fixture@isolated/restore')
    assert 'PGSERVICE' not in environment and environment['PGDATABASE'] == 'restore'


def test_manifest_integrity_rejects_tampering_before_restore(tmp_path):
    archive = tmp_path / 'postgres.dump'
    archive.write_bytes(b'fixture custom archive')
    source = tmp_path / 'vectors-documents.jsonl'
    source.write_text('[]', encoding='utf-8')
    value = {'format': 1, 'postgres_sha256': digest(archive), 'objects': [],
             'collections': {'documents': {'path': source.name, 'sha256': digest(source)}}}
    (tmp_path / 'manifest.json').write_text(json.dumps(value), encoding='utf-8')
    args = Mock(bundle=tmp_path)
    assert manifest(args) == value
    source.write_text('tampered', encoding='utf-8')
    with pytest.raises(ValueError):
        manifest(args)


def test_external_restore_config_rejects_silent_settings_typos_and_requires_external_stores(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'settings': {'databse_url': 'fixture'}, 'pg_admin_url': 'fixture'}), encoding='utf-8')
    with pytest.raises(ValueError, match='Unknown Settings'):
        config(path)
    path.write_text(json.dumps({'settings': {'demo_mode': True}, 'pg_admin_url': 'fixture'}), encoding='utf-8')
    with pytest.raises(ValueError, match='PostgreSQL and S3'):
        config(path)


def test_external_restore_admin_and_runtime_cannot_point_at_different_databases(tmp_path):
    path = tmp_path / 'config.json'
    value = {'settings': {'demo_mode': False, 'database_url': 'postgresql://dr_python:fixture@production/app',
                          'object_store_backend': 's3'},
             'pg_admin_url': 'postgresql://admin:fixture@isolated/restore'}
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='same PostgreSQL'):
        config(path)
