"""Consistent cold-volume backup and fresh-project recovery for single-node Compose.

Never restore over an existing volume. Archives contain private data; keep them local.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
HELPER = 'python:3.13-alpine'


def run(args, *, data=None):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f'{args[:3]} failed (exit {result.returncode}); output withheld to protect secrets')
    return result.stdout


def compose(*args):
    return run(['docker', 'compose', *args])


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def backup(destination):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    config = json.loads(compose('config', '--format', 'json'))
    volumes = {key: value['name'] for key, value in config['volumes'].items()}
    running = compose('ps', '--status', 'running', '--services').decode().split()
    if not running:
        raise RuntimeError('Start the source stack before taking a controlled cold backup')
    # Refuse to silently interrupt user research or indexing.
    active = compose('exec', '-T', 'postgres', 'psql', '-U', 'deepresearch', '-d', 'deepresearch',
                     '-Atc', "SELECT (SELECT count(*) FROM runs WHERE status IN ('queued','running')) + "
                     "(SELECT count(*) FROM documents WHERE status IN ('scanning','indexing'))").decode().strip()
    if active != '0':
        raise RuntimeError('Active research/indexing detected; retry after tasks finish')
    try:
        compose('stop', *[name for name in running if name not in {'postgres', 'keycloak-postgres'}])
        for service, database in [('postgres', 'deepresearch'), ('keycloak-postgres', 'keycloak')]:
            dump = compose('exec', '-T', service, 'pg_dump', '-U', database, '-d', database, '--format=custom')
            (destination / f'{service}.dump').write_bytes(dump)
        compose('stop', 'postgres', 'keycloak-postgres')
        for key, volume in volumes.items():
            print('Backing up ' + key, flush=True)
            run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{volume}:/source:ro',
                 '-v', f'{destination}:/backup', HELPER, 'tar', '-czpf', f'/backup/{key}.tgz', '-C', '/source', '.'])
        manifest = {'version': 2, 'created_at': time.time(), 'source_project': config['name'],
                    'volumes': list(volumes), 'files': {path.name: sha(path) for path in destination.iterdir()}}
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    finally:
        compose('up', '-d', '--wait', '--wait-timeout', '300', *running)
    return destination


def restore(source, project):
    import re
    if not re.fullmatch(r'dr-restore-[a-z0-9-]+', project):
        raise ValueError('Fresh recovery projects must start with dr-restore-')
    source = source.resolve()
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != 2:
        raise ValueError('Version 2 backup required')
    for name, digest in manifest['files'].items():
        path = source / name
        if path.resolve().parent != source or sha(path) != digest:
            raise ValueError('Backup checksum/path validation failed')
    config = json.loads(compose('config', '--format', 'json'))
    if set(manifest['volumes']) != set(config['volumes']):
        raise ValueError('Backup volume set does not match current Compose')
    existing = run(['docker', 'volume', 'ls', '--format', '{{.Name}}']).decode().splitlines()
    targets = {key: f'{project}_{key}' for key in manifest['volumes']}
    if any(name in existing for name in targets.values()):
        raise ValueError('Target volumes already exist; refusing to overwrite')
    if run(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}']).strip():
        raise ValueError('Target project already exists')
    config['name'] = project
    for key, name in targets.items():
        config['volumes'][key] = {'name': name, 'external': True}
    for network in config.get('networks', {}).values():
        network['name'] = project + '_default'
    for name, service in config['services'].items():
        service.pop('ports', None)
        service.pop('build', None)
        if name in {'backend', 'worker', 'migrate', 'web'}:
            service['image'] = 'deepresearch-' + name
        if name in {'backend', 'worker'}:
            service['environment']['FEISHU_WEBHOOK_URL'] = ''
            service['environment']['LLM_API_KEY'] = ''
            service['environment']['EMBEDDING_API_KEY'] = ''
    # Use stdin configuration; expanded credentials are never written to disk.
    encoded = json.dumps(config).encode()

    def target(*args):
        return run(['docker', 'compose', '-f', '-', '-p', project, *args], data=encoded)

    started = time.monotonic()
    try:
        for key, name in targets.items():
            run(['docker', 'volume', 'create', name])
            run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{name}:/target',
                 '-v', f'{source}:/backup:ro', HELPER, 'tar', '-xzpf', f'/backup/{key}.tgz', '-C', '/target'])
            # Compare every archived file before any service can modify it.
            run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{name}:/target:ro',
                 '-v', f'{source}:/backup:ro', HELPER, 'python', '-c',
                 "import tarfile,hashlib,pathlib; t=tarfile.open('/backup/" + key + ".tgz'); "
                 "[(lambda p,m: (_ for _ in ()).throw(RuntimeError('restored file mismatch')) "
                 "if hashlib.sha256(p.read_bytes()).digest()!=hashlib.sha256(t.extractfile(m).read()).digest() "
                 "else None)(pathlib.Path('/target')/m.name,m) for m in t if m.isfile()]"])
        target('up', '-d', '--wait', '--wait-timeout', '120', 'postgres', 'keycloak-postgres')
        for service, database in [('postgres', 'deepresearch'), ('keycloak-postgres', 'keycloak')]:
            container = target('ps', '-q', service).decode().strip()
            run(['docker', 'cp', str(source / f'{service}.dump'), container + ':/tmp/recovery.dump'])
            target('exec', '-T', service, 'pg_restore', '--exit-on-error', '--clean', '--if-exists',
                   '-U', database, '-d', database, '/tmp/recovery.dump')
        target('up', '-d', '--wait', '--wait-timeout', '300')
        target('exec', '-T', 'backend', 'python', '-c',
               "import httpx; c=httpx.Client(); assert c.get('http://backend:8000/readyz').status_code==200; "
               "assert c.get('http://backend:8000/api/metrics').status_code==401; "
               "assert c.get('http://keycloak:8080/realms/deepresearch/.well-known/openid-configuration').status_code==200")
        probe = target('exec', '-T', 'backend', 'python', '-c', RESTORED_RETRIEVAL).decode().strip()
        elapsed = round(time.monotonic() - started, 2)
        if elapsed > 3600:
            raise RuntimeError('Recovery exceeded 60 minute RTO target')
        report = {'passed': True, 'project': project, 'volumes_verified': len(targets),
                  'rto_seconds': elapsed, 'retrieval': json.loads(probe),
                  'checks': ['all archived files match', 'both SQL dumps restored with exit-on-error',
                             'readyz', 'anonymous 401', 'OIDC discovery', 'Milvus search']}
        (source / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
    finally:
        try:
            target('down')
        finally:
            # Attempt every exact temporary target even when Compose cleanup fails.
            # Source volumes and the checked backup directory are never touched.
            for name in targets.values():
                subprocess.run(['docker', 'volume', 'rm', name], cwd=ROOT,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


RESTORED_RETRIEVAL = '''
import json
from pymilvus import MilvusClient
client = MilvusClient(uri='http://milvus:19530')
verified = 0
rows_seen = 0
for name in client.list_collections():
    client.load_collection(name)
    rows = client.query(collection_name=name, filter='id != ""', output_fields=['id','vector','user_id'], limit=1)
    if not rows:
        continue
    row = rows[0]
    hits = client.search(collection_name=name, data=[row['vector']],
        filter='user_id == ' + json.dumps(row['user_id']), limit=1, output_fields=['user_id'])
    assert hits[0] and hits[0][0]['entity']['user_id'] == row['user_id']
    verified += 1
    rows_seen += len(hits[0])
assert verified > 0, 'No nonempty vector collection; recovery search was not verified'
print(json.dumps({'nonempty_collections_searched': verified, 'results_checked': rows_seen}))
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['backup', 'restore'])
    parser.add_argument('--path', type=Path, required=True)
    parser.add_argument('--project', default='dr-restore-verification')
    args = parser.parse_args()
    if args.action == 'backup':
        backup(args.path)
    else:
        restore(args.path, args.project)
