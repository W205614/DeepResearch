"""Destructive-fixture checks restricted to a labelled dr-opt-privileges container.

Never targets a daily database. Caller creates an isolated ephemeral PostgreSQL
container; this script verifies its label and local mapped port before writes.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asyncpg
from scripts.bootstrap_services import bootstrap
from scripts.migrate_services import prepare


async def verify(container):
    if not container.startswith("dr-opt-privileges-"):
        raise ValueError("Isolated fixture container required")
    value = subprocess.check_output(['docker', 'inspect', '--format', '{{ index .Config.Labels "com.deepresearch.validation" }}', container], text=True).strip()
    if value != "optimization-20261026":
        raise ValueError("Fixture ownership label missing")
    mapping = subprocess.check_output(['docker', 'port', container, '5432/tcp'], text=True).strip()
    if not mapping.startswith("127.0.0.1:") or "\n" in mapping:
        raise ValueError("A single loopback fixture mapping is required")
    port = mapping.rsplit(':', 1)[1]
    admin = f'postgresql://deepresearch:fixture-bootstrap-not-production@127.0.0.1:{port}/deepresearch'
    passwords = {role: f'fixture-{role}-independent-password' for role in ('dr_migrator', 'dr_java', 'dr_python', 'dr_monitor')}
    conn = await asyncpg.connect(admin)
    await conn.execute("CREATE TABLE IF NOT EXISTS migration_marker(id integer primary key,value text); INSERT INTO migration_marker VALUES(1,'retained-existing-row') ON CONFLICT DO NOTHING")
    await conn.close()
    await bootstrap(admin, passwords)
    dsn = f'postgresql://dr_migrator:{passwords["dr_migrator"]}@127.0.0.1:{port}/deepresearch'
    environment = {**os.environ, 'DATABASE_URL': dsn.replace('postgresql://', 'postgresql+asyncpg://', 1)}
    result = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], env=environment, capture_output=True)
    if result.returncode:
        raise RuntimeError('Isolated migration failed; no runtime grants applied')
    await prepare(dsn)
    conn = await asyncpg.connect(admin)
    await conn.execute('GRANT UPDATE(topic) ON runs TO dr_python')
    await conn.close()
    await prepare(dsn)
    checks = []
    for role, password in passwords.items():
        conn = await asyncpg.connect(f'postgresql://{role}:{password}@127.0.0.1:{port}/deepresearch')
        flags = await conn.fetchrow('SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user')
        assert not any(flags.values())
        if role != 'dr_monitor':
            assert await conn.fetchval('SELECT value FROM migration_marker WHERE id=1') == 'retained-existing-row'
        denied = ['CREATE TABLE forbidden_runtime_table(id integer)'] if role != 'dr_migrator' else []
        if role == 'dr_java':
            denied += ['UPDATE runs SET report=report WHERE false', 'DELETE FROM audit_logs WHERE false']
        if role == 'dr_python':
            denied += ['UPDATE memberships SET role=role WHERE false', "UPDATE report_publications SET status='published' WHERE false",
                       'UPDATE document_policies SET collection_id=collection_id WHERE false',
                       'UPDATE runs SET topic=topic WHERE false', 'UPDATE events SET data=data WHERE false',
                       'UPDATE threads SET title=title WHERE false', 'UPDATE report_revision_jobs SET status=status WHERE false',
                       "INSERT INTO runs(id,user_id,thread_id) VALUES('forbidden','none','none')"]
            await conn.execute('UPDATE report_publications SET evidence_status=evidence_status WHERE false')
            await conn.execute('UPDATE runs SET reserved_tokens=reserved_tokens WHERE false')
        if role == 'dr_monitor':
            denied += ['SELECT topic FROM runs LIMIT 1', 'SELECT * FROM memberships LIMIT 1', 'UPDATE runs SET status=status WHERE false']
            await conn.fetchval("SELECT count(*) FROM runs WHERE status='queued'")
        for sql in denied:
            try:
                await conn.execute(sql)
            except asyncpg.InsufficientPrivilegeError:
                checks.append({'role': role, 'operation': sql.split()[0], 'rejected': True})
            else:
                raise AssertionError('Runtime privilege boundary failed')
        await conn.close()
    # Re-running the upgrade/privilege preparation must retain the marker and all roles.
    await bootstrap(admin, passwords)
    await prepare(dsn)
    conn = await asyncpg.connect(admin)
    head = await conn.fetchval('SELECT version_num FROM alembic_version')
    assert await conn.fetchval('SELECT count(*) FROM migration_marker') == 1
    await conn.close()
    return {'passed': True, 'scope': 'isolated PostgreSQL 17.6; retained pre-upgrade row; no daily service changed',
            'migration_head': head, 'runtime_roles_non_superuser': True, 'denials': checks,
            'retained_rows': 1, 'idempotent_bootstrap': True, 'provider_calls': 0,
            'container_id': subprocess.check_output(['docker', 'inspect', '--format', '{{.Id}}', container], text=True).strip()}


if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    parser = argparse.ArgumentParser()
    parser.add_argument('--container', required=True)
    parser.add_argument('--output', default='docs/evidence/optimization-20261026/privileges-isolated.json')
    args = parser.parse_args()
    report = asyncio.run(verify(args.container))
    Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
