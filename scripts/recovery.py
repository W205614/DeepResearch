"""Consistent cold-volume backup and fresh-project recovery for single-node Compose.

Never restore over an existing volume. Archives contain private data; keep them local.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import copy
import ipaddress
import os
import shutil
import gzip
import tarfile
from pathlib import PurePosixPath
from urllib.parse import quote
import re
import sys
import uuid
try:
    from scripts.backup_images import verify_image_bundle
except ModuleNotFoundError:
    from backup_images import verify_image_bundle  # Standalone archived restore.py sidecar.

ROOT = Path(__file__).resolve().parents[1]
HELPER = 'python:3.13-alpine'


def hidden_process_options():
    """Suppress Windows child consoles; leave non-Windows process behavior intact."""
    if os.name != 'nt':
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {'creationflags': subprocess.CREATE_NO_WINDOW, 'startupinfo': startup}


def log_child_failure(executable, code):
    if os.name == 'nt' and sys.stderr is not None:
        print(json.dumps({'event': 'recovery_child_failed', 'executable': Path(executable).name,
                          'exit_code': code}), file=sys.stderr, flush=True)


def run(args, *, data=None, environment=None):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT,
                            env=None if environment is None else {**os.environ, **environment}, **hidden_process_options())
    if result.returncode:
        log_child_failure(args[0], result.returncode)
        raise RuntimeError(f'{args[:3]} failed (exit {result.returncode}); output withheld to protect secrets')
    return result.stdout


def compose(*args):
    return run(['docker', 'compose', *args])


def run_container_helper(arguments, *, retain, project, operation, records, purpose):
    """Retain explicitly owned helpers when deletion is outside the current window."""
    if not retain:
        return run(['docker', 'run', '--rm', *arguments])
    name = f'{project}-helper-{operation}-{len(records):03d}'
    entry = {'name': name, 'purpose': purpose, 'operation': operation, 'project': project,
             'creator': 'scripts/recovery.py', 'creator_proof_verified': False}
    records.append(entry)
    labels = {'operation': operation, 'project': project, 'creator': 'scripts/recovery.py', 'purpose': purpose}
    command = ['docker', 'run', '--name', name]
    for key, value in labels.items():
        command += ['--label', 'com.deepresearch.recovery.'+key+'='+value]
    try:
        output = run([*command, *arguments])
    finally:
        template = ('{"container_id":{{json .Id}},"name":{{json .Name}},"image_id":{{json .Image}},'
                    '"operation":{{json (index .Config.Labels "com.deepresearch.recovery.operation")}},'
                    '"project":{{json (index .Config.Labels "com.deepresearch.recovery.project")}},'
                    '"creator":{{json (index .Config.Labels "com.deepresearch.recovery.creator")}},'
                    '"purpose":{{json (index .Config.Labels "com.deepresearch.recovery.purpose")}}}')
        try:
            actual = json.loads(run(['docker', 'inspect', '--format', template, name]))
            entry['container_id'], entry['image_id'] = actual['container_id'], actual['image_id']
            entry['creator_proof_verified'] = (actual['name'] == '/'+name
                                               and all(actual.get(key) == value for key, value in labels.items()))
        except Exception:
            entry['creator_proof_error'] = 'inspection_unavailable'
    if not entry['creator_proof_verified']:
        raise RuntimeError('Retained helper creator proof could not be verified; resource preserved')
    return output


def retained_helpers_report(project, operation, records):
    return {'intentional_retention': True, 'cleanup_performed': False, 'project': project,
            'operation': operation, 'creator': 'scripts/recovery.py', 'temporary_containers': records}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verification_database_environment(project, container):
    """Short-lived verifier privilege, only after inspecting a fresh restore DB."""
    if not re.fullmatch(r'dr-restore-[a-z0-9-]+', project):
        raise ValueError('Verifier administrator access requires a dr-restore project')
    value = json.loads(run(['docker', 'inspect', container]))[0]
    labels = value['Config'].get('Labels') or {}
    if (labels.get('com.docker.compose.project') != project
            or labels.get('com.docker.compose.service') != 'postgres'):
        raise ValueError('Verifier database container is outside this restore project')
    environment = dict(item.split('=', 1) for item in value['Config']['Env'] if '=' in item)
    if not environment.get('POSTGRES_PASSWORD'):
        raise ValueError('Restore database has no inspectable verifier credential')
    user = quote(environment.get('POSTGRES_USER', 'postgres'), safe='')
    password = quote(environment['POSTGRES_PASSWORD'], safe='')
    database = quote(environment.get('POSTGRES_DB', environment.get('POSTGRES_USER', 'postgres')), safe='')
    return {'RECOVERY_DATABASE_URL': f'postgresql://{user}:{password}@postgres:5432/{database}',
            'RECOVERY_VERIFICATION_PROJECT': project}


def freeze_binds(config, destination):
    mounts = []
    for definition in config['services'].values():
        for mount in definition.get('volumes', []):
            if mount.get('type') != 'bind':
                continue
            original = Path(mount['source'])
            if not mount.get('read_only') or original.is_symlink() or not original.exists():
                raise ValueError('Recovery requires existing, nonsymlink, read-only bind mounts')
            relative = f'binds/{len(mounts):04d}'
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if original.is_dir():
                if any(path.is_symlink() for path in original.rglob('*')):
                    raise ValueError('Symlinks inside configuration bind mounts are unsupported')
                shutil.copytree(original, target)
            else:
                shutil.copy2(original, target)
            files = [p.relative_to(destination).as_posix() for p in target.rglob('*') if p.is_file()] if target.is_dir() else [relative]
            mounts.append({'path': relative, 'directory': target.is_dir(), 'files': files})
            mount['source'] = 'backup:' + relative
    if config.get('configs') or config.get('secrets'):
        raise ValueError('External Compose configs/secrets must be converted to archived read-only mounts')
    return mounts


def materialize_runtime_storage(config, source_compose):
    """Include actual anonymous volumes and known required container-layer data."""
    volumes = {key: value['name'] for key, value in config['volumes'].items()}
    by_name = {name: key for key, name in volumes.items()}
    assets = []
    for service, definition in config['services'].items():
        containers = source_compose('ps', '-aq', service).decode().split()
        if not containers:
            if service == 'clamav':
                raise ValueError('ClamAV has no existing container/database to snapshot')
            continue
        mounts = json.loads(run(['docker', 'inspect', '--format', '{{json .Mounts}}', containers[0]]))
        configured_targets = {mount['target'] for mount in definition.get('volumes', [])}
        for mount in mounts:
            if mount['Destination'] in configured_targets:
                continue
            if mount['Type'] != 'volume':
                raise ValueError('Runtime bind mount is absent from its deployment snapshot')
            name = mount['Name']
            key = by_name.get(name) or 'runtime-' + service + '-' + hashlib.sha256(mount['Destination'].encode()).hexdigest()[:12]
            by_name[name], volumes[key] = key, name
            config['volumes'][key] = {'name': name}
            definition.setdefault('volumes', []).append({'type': 'volume', 'source': key, 'target': mount['Destination'],
                                                       'read_only': not mount.get('RW', True)})
        database_mounted = any(PurePosixPath('/var/lib/clamav').is_relative_to(PurePosixPath(mount['Destination'])) for mount in mounts)
        if service == 'clamav' and not database_mounted:
            key = 'runtime-clamav-database'
            config['volumes'][key] = {'name': config['name'] + '_' + key}
            definition.setdefault('volumes', []).append({'type': 'volume', 'source': key, 'target': '/var/lib/clamav'})
            assets.append({'volume': key, 'service': service, 'container': containers[0], 'path': '/var/lib/clamav'})
    return volumes, assets


def export_runtime_asset(asset, destination):
    """Stream docker cp's tar into gzip without allocating the database in RAM."""
    with subprocess.Popen(['docker', 'cp', asset['container'] + ':' + asset['path'] + '/.', '-'],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=ROOT, **hidden_process_options()) as process:
        with gzip.open(destination, 'wb') as output:
            shutil.copyfileobj(process.stdout, output, length=1024 * 1024)
        exit_code = process.wait()
        if exit_code:
            log_child_failure('docker', exit_code)
            raise RuntimeError(f'Runtime database export failed (exit {exit_code}); output withheld')
    if asset['service'] == 'clamav':
        validate_clamav_database(destination)


def validate_clamav_database(destination):
    """A mount at /var/lib/clamav must expose the databases directly, not one level deeper."""
    with tarfile.open(destination, 'r:gz') as archive:
        files = set()
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('ClamAV archive has unsafe paths')
            if member.isfile() and member.size > 0 and len(path.parts) == 1:
                files.add(path.name)
    if 'main.cvd' not in files or not files.intersection({'daily.cvd', 'daily.cld'}):
        raise ValueError('ClamAV signature database is incomplete or nested; backup is not recoverable offline')


def safe_container_states(project):
    """No environment, command, health log output, or arbitrary error strings."""
    template = ('{"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
                '"state":{{json .State.Status}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"none"{{end}},'
                '"exit_code":{{.State.ExitCode}},"oom_killed":{{json .State.OOMKilled}},"restarts":{{.RestartCount}}}')
    rows = []
    containers = run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project]).decode().split()
    for container in containers:
        try:
            rows.append(json.loads(run(['docker', 'inspect', '--format', template, container])))
        except Exception:
            rows.append({'state': 'inspection_unavailable'})
    return rows


def backup(destination, compose_files=(), source_project=None, retain_temporary_resources=False):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    prefix = [part for filename in compose_files for part in ('-f', str(filename))]
    if source_project:
        prefix += ['-p', source_project]
    def source_compose(*args):
        return compose(*prefix, *args)
    config = json.loads(source_compose('config', '--format', 'json'))
    if source_project and config['name'] != source_project:
        raise ValueError('Source project does not match resolved deployment')
    recovery_config = copy.deepcopy(config)
    volumes, runtime_assets = materialize_runtime_storage(recovery_config, source_compose)
    bind_mounts = freeze_binds(recovery_config, destination)
    helper_image = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', HELPER]).decode().strip()
    existing = set(run(['docker', 'volume', 'ls', '--format', '{{.Name}}']).decode().splitlines())
    if not set(volumes.values()) <= existing:
        raise ValueError('A source volume is absent; refusing to create an empty substitute')
    running = source_compose('ps', '--status', 'running', '--services').decode().split()
    temporary_databases = not running
    operation, helpers = uuid.uuid4().hex, []
    try:
        if temporary_databases:
            # Start the existing database containers; never recreate an older deployment.
            source_compose('start', '--wait', '--wait-timeout', '120', 'postgres', 'keycloak-postgres')
        active = source_compose('exec', '-T', 'postgres', 'psql', '-U', 'deepresearch', '-d', 'deepresearch',
                     '-Atc', "SELECT (SELECT count(*) FROM runs WHERE status IN ('queued','running')) + "
                     "(SELECT count(*) FROM documents WHERE status IN ('scanning','indexing','rebuilding'))").decode().strip()
        if active != '0' and not temporary_databases:
            raise RuntimeError('Active research/indexing detected; retry after tasks finish')
        migration_head = source_compose('exec', '-T', 'postgres', 'psql', '-U', 'deepresearch', '-d', 'deepresearch',
                             '-Atc', 'SELECT version_num FROM alembic_version').decode().strip()
        applications = [name for name in running if name not in {'postgres', 'keycloak-postgres'}]
        if applications:
            source_compose('stop', *applications)
        for service, database in [('postgres', 'deepresearch'), ('keycloak-postgres', 'keycloak')]:
            dump = source_compose('exec', '-T', service, 'pg_dump', '-U', database, '-d', database, '--format=custom')
            (destination / f'{service}.dump').write_bytes(dump)
        source_compose('stop', 'postgres', 'keycloak-postgres')
        for key, volume in volumes.items():
            print('Backing up ' + key, flush=True)
            run_container_helper(['--network', 'none', '-v', f'{volume}:/source:ro',
                 '-v', f'{destination}:/backup', helper_image, 'tar', '-czpf', f'/backup/{key}.tgz', '-C', '/source', '.'],
                 retain=retain_temporary_resources, project=config['name'], operation=operation, records=helpers, purpose='backup-tar')
        for asset in runtime_assets:
            print('Backing up runtime database: ' + asset['service'], flush=True)
            export_runtime_asset(asset, destination / (asset['volume'] + '.tgz'))
        # This private backup already contains database data and authentication
        # material. Bind the resolved deployment and exact images to it too;
        # never print this configuration or commit the backup directory.
        image_ids = {}
        for service, definition in recovery_config['services'].items():
            containers = source_compose('ps', '-aq', service).decode().split()
            if containers:
                image_id = run(['docker', 'inspect', '--format', '{{.Image}}', containers[0]]).decode().strip()
            else:
                image_name = definition.get('image', config['name'] + '-' + service)
                image_id = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image_name]).decode().strip()
            image_ids[service] = image_id
            definition['image'] = image_id
            definition.pop('build', None)
        (destination / 'deployment.private.json').write_text(json.dumps(recovery_config), encoding='utf-8')
        shutil.copy2(Path(__file__), destination / 'restore.py')
        shutil.copy2(Path(__file__).with_name('backup_images.py'), destination / 'backup_images.py')
        run(['docker', 'image', 'save', '-o', str(destination / 'images.tar'), *sorted(set(image_ids.values()) | {helper_image})])
        image_bundle = verify_image_bundle(destination / 'images.tar', set(image_ids.values()) | {helper_image})
        retained = retained_helpers_report(config['name'], operation, helpers) if retain_temporary_resources else None
        if retained:
            (destination / 'retained-resources.json').write_text(json.dumps(retained, indent=2)+'\n', encoding='utf-8')
        manifest = {'version': 3, 'created_at': time.time(), 'source_project': config['name'],
                    'source_sha': run(['git', 'rev-parse', 'HEAD']).decode().strip(),
                    'worktree_dirty': bool(run(['git', 'status', '--porcelain', '--untracked-files=no']).strip()),
                    'original_running_services': running, 'images': image_ids,
                    'helper_image': helper_image, 'bind_mounts': bind_mounts,
                    'runtime_assets': [{key: asset[key] for key in ('volume', 'service', 'path')} for asset in runtime_assets],
                    'migration_head': migration_head,
                    'image_bundle_verification': image_bundle,
                    'retained_resources': retained,
                    'volumes': list(recovery_config['volumes']), 'files': {path.relative_to(destination).as_posix(): sha(path) for path in destination.rglob('*') if path.is_file()}}
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    finally:
        try:
            if running:
                source_compose('start', '--wait', '--wait-timeout', '300', *running)
            elif temporary_databases:
                source_compose('stop', 'postgres', 'keycloak-postgres')
        finally:
            if retain_temporary_resources:
                retained_path = destination / 'retained-resources.json'
                # Success already hashed this file into manifest. Only failures
                # before completion need an orphan-resource ownership receipt.
                if not retained_path.exists():
                    retained = retained_helpers_report(config['name'], operation, helpers)
                    retained_path.write_text(json.dumps(retained, indent=2)+'\n', encoding='utf-8')
    return destination


def load_archive(source):
    """Validate the whole archive before creating any recovery resources."""
    source = source.resolve()
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != 3:
        raise ValueError('Version 3 backup required: immutable deployment and image identities')
    required = {'deployment.private.json', 'restore.py', 'images.tar', 'postgres.dump', 'keycloak-postgres.dump'}
    required.update(f'{key}.tgz' for key in manifest['volumes'])
    if not manifest.get('helper_image') or 'bind_mounts' not in manifest:
        raise ValueError('Backup is missing immutable helper/configuration snapshots')
    for mount in manifest['bind_mounts']:
        required.update(mount['files'])
    if not required <= set(manifest['files']):
        raise ValueError('Required backup files are missing from the checksum manifest')
    if manifest.get('image_bundle_verification') and 'backup_images.py' not in manifest['files']:
        raise ValueError('New standalone recovery bundle omits its image verifier')
    for name, digest in manifest['files'].items():
        path = source / name
        if not path.resolve().is_relative_to(source) or path.is_symlink() or not path.is_file() or sha(path) != digest:
            raise ValueError('Backup checksum/path validation failed')
    for mount in manifest['bind_mounts']:
        path = source / mount['path']
        if not path.resolve().is_relative_to(source) or path.is_symlink() or not path.exists() or path.is_dir() != mount['directory']:
            raise ValueError('Archived configuration bind is invalid')
    config = json.loads((source / 'deployment.private.json').read_text(encoding='utf-8'))
    if set(manifest['volumes']) != set(config['volumes']):
        raise ValueError('Backup volume set does not match its deployment snapshot')
    if set(manifest['images']) != set(config['services']):
        raise ValueError('Backup image set does not match its deployment snapshot')
    if any(service.get('image') != manifest['images'][name] for name, service in config['services'].items()):
        raise ValueError('Backup image identities do not match its deployment snapshot')
    import re
    if any(not re.fullmatch(r'sha256:[a-f0-9]{64}', image) for image in [*manifest['images'].values(), manifest['helper_image']]):
        raise ValueError('Backup requires immutable image digests')
    binds = {mount['path'] for mount in manifest['bind_mounts']}
    for service in config['services'].values():
        for mount in service.get('volumes', []):
            if mount.get('type') == 'bind' and (not mount.get('read_only') or mount['source'] not in {'backup:' + path for path in binds}):
                raise ValueError('Deployment references a live or writable host configuration')
    if 'clamav' in config['services'] and not any(
        mount.get('type') == 'volume' and mount.get('source') in manifest['volumes']
        and PurePosixPath('/var/lib/clamav').is_relative_to(PurePosixPath(mount['target']))
        for mount in config['services']['clamav'].get('volumes', [])
    ):
        raise ValueError('Backup omits ClamAV runtime database; create a fresh complete backup')
    # Always inspect archive content, even when restore later finds every image in Docker cache.
    verify_image_bundle(source / 'images.tar', set(manifest['images'].values()) | {manifest['helper_image']})
    return manifest, config


def restore(source, project, subnet='10.254.245.0/24', retain_temporary_resources=False):
    started = time.monotonic()
    import re
    if not re.fullmatch(r'dr-restore-[a-z0-9-]+', project):
        raise ValueError('Fresh recovery projects must start with dr-restore-')
    source = source.resolve()
    manifest, config = load_archive(source)
    recovery_subnet = ipaddress.ip_network(subnet, strict=True)
    if recovery_subnet.version != 4 or not recovery_subnet.is_private or recovery_subnet.prefixlen < 24:
        raise ValueError('Recovery requires an explicit small private IPv4 subnet')
    image_set = set(manifest['images'].values()) | {manifest['helper_image']}
    for image_id in image_set:
        result = subprocess.run(['docker', 'image', 'inspect', image_id], cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **hidden_process_options())
        if result.returncode:
            run(['docker', 'image', 'load', '-i', str(source / 'images.tar')])
            break
    for image_id in image_set:
        run(['docker', 'image', 'inspect', image_id])
    existing = run(['docker', 'volume', 'ls', '--format', '{{.Name}}']).decode().splitlines()
    targets = {key: f'{project}_{key}' for key in manifest['volumes']}
    if any(name in existing for name in targets.values()):
        raise ValueError('Target volumes already exist; refusing to overwrite')
    if run(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}']).strip():
        raise ValueError('Target project already exists')
    config['name'] = project
    for key, name in targets.items():
        config['volumes'][key] = {'name': name, 'external': True}
    network_name = project + '_isolated'
    if network_name in run(['docker', 'network', 'ls', '--format', '{{.Name}}']).decode().splitlines():
        raise ValueError('Recovery network already exists')
    config['networks'] = {'recovery': {'name': network_name, 'external': True}}
    for name, service in config['services'].items():
        service.pop('ports', None)
        service.pop('build', None)
        service.pop('container_name', None)
        service.pop('network_mode', None)
        service.pop('external_links', None)
        service['networks'] = {'recovery': {}}
        for mount in service.get('volumes', []):
            if mount.get('type') == 'bind':
                mount['source'] = str(source / mount['source'].removeprefix('backup:'))
        service['image'] = manifest['images'][name]
        if name == 'clamav':
            service.setdefault('environment', {})['CLAMAV_NO_FRESHCLAMD'] = 'true'
        if name in {'agent', 'worker', 'document-worker', 'alert-relay'}:
            service['environment']['FEISHU_WEBHOOK_URL'] = ''
            service['environment']['LLM_API_KEY'] = ''
            service['environment']['EMBEDDING_API_KEY'] = ''
            service['environment']['BOCHA_API_KEY'] = ''
            service['environment']['ALLOW_INTERNAL_MODEL_PROCESSING'] = 'false'
    # Use stdin configuration; expanded credentials are never written to disk.
    encoded = json.dumps(config).encode()

    def target(*args, environment=None):
        return run(['docker', 'compose', '-f', '-', '-p', project, *args], data=encoded, environment=environment)

    created_volumes, network_created, helpers = [], False, []
    operation = uuid.uuid4().hex
    report = {'passed': False, 'project': project, 'volumes': targets, 'created_volumes': created_volumes,
              'volumes_retained': True, 'cleanup_complete': False, 'stage': 'creating_network'}
    report['intentional_retention'] = bool(retain_temporary_resources)
    report['container_states_phase'] = 'before_final_stop_or_cleanup'
    try:
        network_id = run(['docker', 'network', 'create', '--driver', 'bridge', '--internal', '--subnet', str(recovery_subnet),
             '--label', 'com.deepresearch.recovery.project=' + project, network_name]).decode().strip()
        network_created = True
        report['stage'] = 'restoring_volumes'
        for key, name in targets.items():
            run(['docker', 'volume', 'create', name])
            created_volumes.append(name)
            run_container_helper(['--network', 'none', '-v', f'{name}:/target',
                 '-v', f'{source}:/backup:ro', manifest['helper_image'], 'tar', '-xzpf', f'/backup/{key}.tgz', '-C', '/target'],
                 retain=retain_temporary_resources, project=project, operation=operation, records=helpers, purpose='restore-extract')
            # Compare every archived file before any service can modify it.
            run_container_helper(['--network', 'none', '-v', f'{name}:/target:ro',
                 '-v', f'{source}:/backup:ro', manifest['helper_image'], 'python', '-c',
                 "import tarfile,hashlib,pathlib; t=tarfile.open('/backup/" + key + ".tgz'); "
                 "[(lambda p,m: (_ for _ in ()).throw(RuntimeError('restored file mismatch')) "
                 "if hashlib.sha256(p.read_bytes()).digest()!=hashlib.sha256(t.extractfile(m).read()).digest() "
                 "else None)(pathlib.Path('/target')/m.name,m) for m in t if m.isfile()]"],
                 retain=retain_temporary_resources, project=project, operation=operation, records=helpers, purpose='restore-verify')
        report['stage'] = 'starting_databases'
        target('up', '-d', '--wait', '--wait-timeout', '120', 'postgres', 'keycloak-postgres')
        for service, database in [('postgres', 'deepresearch'), ('keycloak-postgres', 'keycloak')]:
            container = target('ps', '-q', service).decode().strip()
            run(['docker', 'cp', str(source / f'{service}.dump'), container + ':/tmp/recovery.dump'])
            target('exec', '-T', service, 'pg_restore', '--exit-on-error', '--clean', '--if-exists',
                   '-U', database, '-d', database, '/tmp/recovery.dump')
        # Restored pending jobs must not run or contact upstream providers.
        recovery_services = [name for name in config['services']
                             if name not in {'worker', 'document-worker'}]
        report['stage'] = 'starting_recovery_services'
        target('up', '-d', '--wait', '--wait-timeout', '300', *recovery_services)
        report['stage'] = 'verifying_services_and_business'
        target('exec', '-T', 'agent', 'python', '-c',
               "import httpx; c=httpx.Client(); assert c.get('http://backend:8080/readyz').status_code==200; "
               "assert c.get('http://backend:8080/api/metrics').status_code==401; "
               "assert c.get('http://keycloak:8080/realms/deepresearch/.well-known/openid-configuration').status_code==200")
        probe = target('exec', '-T', 'agent', 'python', '-c', RESTORED_RETRIEVAL).decode().strip()
        # Compose's canonical JSON preserves $$ escapes. Read the effective
        # restored environment so a literal dollar in the password is not doubled.
        identity_container = target('ps', '-q', 'keycloak').decode().strip()
        effective_environment = json.loads(run(['docker', 'inspect', '--format', '{{json .Config.Env}}', identity_container]))
        identity_environment = dict(item.split('=', 1) for item in effective_environment if '=' in item)
        admin_password = identity_environment.get('KC_BOOTSTRAP_ADMIN_PASSWORD') or identity_environment.get('KEYCLOAK_ADMIN_PASSWORD')
        if not admin_password:
            raise ValueError('Snapshot has no configured recovery identity administrator')
        database_container = target('ps', '-q', 'postgres').decode().strip()
        verifier_environment = verification_database_environment(project, database_container)
        verifier_environment['RECOVERY_ADMIN_PASSWORD'] = admin_password
        business_probe = target('exec', '-T', '-e', 'RECOVERY_ADMIN_PASSWORD', '-e', 'RECOVERY_DATABASE_URL',
                                '-e', 'RECOVERY_VERIFICATION_PROJECT', 'agent', 'python', '-c', RESTORED_BUSINESS,
                                environment=verifier_environment).decode().strip()
        elapsed = round(time.monotonic() - started, 2)
        if elapsed > 3600:
            raise RuntimeError('Recovery exceeded 60 minute RTO target')
        report.update({'passed': True, 'project': project, 'volumes_verified': len(targets),
                  'source_sha': manifest['source_sha'], 'images_bound': True,
                  'rto_seconds': elapsed, 'retrieval': json.loads(probe), 'business': json.loads(business_probe),
                  'checks': ['all archived files match', 'both SQL dumps restored with exit-on-error',
                             'readyz', 'anonymous 401', 'OIDC discovery', 'Milvus search',
                             'real OIDC tokens', 'viewer/revoked permissions', 'published report SHA and citations',
                             'authorized SQL-filtered document retrieval (keyword fallback, upstream disabled)',
                             'offline ClamAV signatures: clean content accepted and EICAR rejected',
                             'new research accepted and explicitly cancelled']})
        report['stage'] = 'verified'
    finally:
        try:
            report['container_states'] = safe_container_states(project)
        except Exception:
            report['container_states'] = [{'state': 'inspection_unavailable'}]
        cleanup_errors = []
        try:
            target('stop' if retain_temporary_resources else 'down')
        except Exception:
            cleanup_errors.append('recovery_container_cleanup_failed')
        if network_created and not retain_temporary_resources:
            try:
                run(['docker', 'network', 'rm', network_name])
            except Exception:
                cleanup_errors.append('recovery_network_cleanup_failed')
        report['cleanup_complete'] = not cleanup_errors and not retain_temporary_resources
        report['cleanup_performed'] = not retain_temporary_resources
        if retain_temporary_resources:
            report['retained_resources'] = {**retained_helpers_report(project, operation, helpers),
                'compose_project': project, 'created_volumes': created_volumes,
                'network': network_name if network_created else None,
                'network_id': network_id if network_created else None,
                'compose_stop_complete': not cleanup_errors}
        report['cleanup_errors'] = cleanup_errors
        report['passed'] = report['passed'] and not cleanup_errors
        (source / f'restore-{project}.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (source / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)
    if not report['passed']:
        raise RuntimeError('Recovery retained resources could not be stopped; names are recorded' if retain_temporary_resources
                           else 'Recovery cleanup incomplete; preserved resource names are recorded')


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


RESTORED_BUSINESS = r'''
import asyncio, hashlib, json, os, re, uuid
import asyncpg, httpx

async def restored_acl_contract(db):
    tables = await db.fetchrow("""SELECT to_regclass('public.document_policies') IS NOT NULL AS policies,
        to_regclass('public.collections') IS NOT NULL AS collections,
        to_regclass('public.collection_grants') IS NOT NULL AS grants""")
    present = [bool(tables[name]) for name in ('policies','collections','grants')]
    if all(present):
        return {'collection_acl':True,'mode':'current_collection_acl'}
    assert not any(present), 'Partial document ACL schema; recovery verification must fail closed'
    heads = await db.fetch('SELECT version_num FROM alembic_version')
    supported = {'0013_evidence_lifecycle','0014_runtime_reliability',
                 '0015_usage_governance','0016_service_privileges'}
    assert len(heads)==1 and heads[0]['version_num'] in supported, 'Legacy ACL requires a known0013-0016 migration head'
    return {'collection_acl':False,'mode':'legacy_workspace_acl','migration_head':heads[0]['version_num']}

async def verify():
    project = os.environ.get('RECOVERY_VERIFICATION_PROJECT', '')
    acceptance = project == 'dr-opt-acceptance' and os.environ.get('RECOVERY_ACCEPTANCE_ONLY') == '1'
    assert re.fullmatch(r'dr-restore-[a-z0-9-]+', project) or acceptance, 'Explicit isolated verifier project required'
    from backend.core.config import Settings
    settings = Settings()
    if acceptance:
        assert settings.llm_base_url == settings.embedding_base_url == 'http://127.0.0.1:9', 'Acceptance providers must be unreachable fixtures'
    db = await asyncpg.connect(os.environ['RECOVERY_DATABASE_URL'])
    users, client_id, master = [], None, None
    checks = {}
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        identity = 'http://keycloak:8080'
        api = 'http://backend:8080'
        def require(response, status):
            assert response.status_code == status, 'Recovery HTTP check failed: ' + str(response.status_code)
            return response
        try:
            acl_contract = await restored_acl_contract(db)
            from backend.core.clamav import scan, ScanUnavailable, ScanRejected
            assert settings.document_scan_mode == 'clamav'
            for attempt in range(24):
                try:
                    await scan(settings, b'Recovery clean document test')
                    break
                except (ScanUnavailable, ScanRejected):
                    if attempt == 23:
                        raise
                    await asyncio.sleep(5)
            marker = b'X5O!P%@AP[4' + bytes([92]) + b'PZX54(P^)7CC)7}$' + b'EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'
            try:
                await scan(settings, marker)
            except ScanRejected:
                pass
            else:
                raise AssertionError('Restored scanner accepted the antivirus test marker')
            master = require(await client.post(identity + '/realms/master/protocol/openid-connect/token', data={
                'client_id':'admin-cli', 'grant_type':'password', 'username':'admin',
                'password':os.environ['RECOVERY_ADMIN_PASSWORD']}), 200).json()['access_token']
            headers = {'Authorization':'Bearer ' + master}
            tag = uuid.uuid4().hex
            client_name = 'restore-check-' + tag
            response = require(await client.post(identity + '/admin/realms/deepresearch/clients', headers=headers, json={
                'clientId':client_name, 'enabled':True, 'publicClient':True, 'protocol':'openid-connect',
                'standardFlowEnabled':False, 'directAccessGrantsEnabled':True,
                'protocolMappers':[{'name':'recovery-audience','protocol':'openid-connect',
                    'protocolMapper':'oidc-audience-mapper','config':{
                        'included.custom.audience':os.environ.get('OIDC_AUDIENCE','deepresearch-api'),
                        'id.token.claim':'false','access.token.claim':'true'}}]}), 201)
            client_id = response.headers['location'].rsplit('/',1)[-1]
            tokens = []
            for role in ('admin','viewer'):
                name, password = 'restore-' + role + '-' + tag, uuid.uuid4().hex + uuid.uuid4().hex
                created = require(await client.post(identity + '/admin/realms/deepresearch/users', headers=headers, json={
                    'username':name,'enabled':True,'emailVerified':True,'email':name+'@example.invalid',
                    'firstName':'Recovery','lastName':'Verification',
                    'credentials':[{'type':'password','value':password,'temporary':False}]}), 201)
                subject = created.headers['location'].rsplit('/',1)[-1]
                users.append(subject)
                token = require(await client.post(identity + '/realms/deepresearch/protocol/openid-connect/token', data={
                    'grant_type':'password','client_id':client_name,'username':name,'password':password}), 200).json()['access_token']
                tokens.append({'Authorization':'Bearer ' + token})
                require(await client.get(api + '/api/workspaces', headers=tokens[-1]), 200)
            report = await db.fetchrow("SELECT * FROM report_publications WHERE status='published' ORDER BY reviewed_at DESC,id DESC LIMIT 1")
            assert report, 'No published report; business recovery cannot be claimed'
            workspace = report['workspace_id']
            for subject, role in zip(users, ('admin','viewer')):
                await db.execute("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES($1,$2,$3,'recovery-check')", workspace, subject, role)
            original = json.loads(report['sources_json'])
            granted_collections = set()
            if acl_contract['collection_acl']:
                for source in original:
                    if source.get('kind') != 'local':
                        continue
                    policy = await db.fetchrow("""SELECT p.collection_id,c.visibility FROM documents d
                        LEFT JOIN document_policies p ON p.document_id=d.id AND p.workspace_id=d.user_id
                        LEFT JOIN collections c ON c.id=p.collection_id AND c.workspace_id=p.workspace_id
                        WHERE d.id=$1 AND d.user_id=$2""", source.get('document_id',''), workspace)
                    assert policy, 'Selected published source is no longer present'
                    if policy['collection_id'] and policy['visibility'] != 'workspace':
                        await db.execute("""INSERT INTO collection_grants(collection_id,subject,permission)
                            VALUES($1,$2,'read') ON CONFLICT DO NOTHING""",policy['collection_id'],users[1])
                        granted_collections.add(policy['collection_id'])
            scoped = [{**header,'X-Workspace-ID':workspace} for header in tokens]
            body = require(await client.get(api + '/api/reports/' + report['id'], headers=scoped[1]), 200).json()
            snapshot = report['report_markdown']+'\n'+report['sources_json']+'\n'+report['validation_json']
            hashes = {hashlib.sha256((snapshot+'\n'+report['artifacts_json']).encode()).hexdigest()}
            if not json.loads(report['artifacts_json']):
                hashes.add(hashlib.sha256(snapshot.encode()).hexdigest())
            assert report['content_sha256'] in hashes and body['content_sha256'] == report['content_sha256']
            assert body['report_markdown'] == report['report_markdown']
            visible = {source['id']:source for source in body['sources']}
            assert original and any('['+source['id']+']' in body['report_markdown'] for source in original)
            for source in original:
                assert source['id'] in visible and all(visible[source['id']].get(k) == v for k,v in source.items())
            denied = await client.get(api + '/api/research/runs/' + report['source_run_id'], headers=scoped[1])
            assert denied.status_code in (403,404)
            denied = await client.post(api + '/api/reports/' + report['id'] + '/revisions', headers=scoped[1], json={
                'client_request_id':uuid.uuid4().hex,'expected_content_sha256':report['content_sha256'],
                'expected_corpus_revision':report['corpus_revision']})
            assert denied.status_code == 403
            seed = await db.fetchrow("""SELECT c.document_id,c.user_id,c.text,d.hash,d.index_version
                FROM chunks c JOIN documents d ON d.id=c.document_id AND d.user_id=c.user_id
                WHERE d.status IN ('ready','rebuilding') AND length(c.text)>=10 ORDER BY c.id LIMIT 1""")
            assert seed, 'No current document chunk; application retrieval was not verified'
            await db.execute("""INSERT INTO memberships(workspace_id,subject,role,created_at)
                VALUES($1,$2,'admin','recovery-check') ON CONFLICT DO NOTHING""",seed['user_id'],users[0])
            found = require(await client.post(api + '/api/documents/search',headers={**tokens[0],'X-Workspace-ID':seed['user_id']},
                json={'query':seed['text'][:1000],'limit':20}),200).json()
            assert any(hit['document_id'] == seed['document_id'] and hit['index_version'] == seed['index_version']
                       and hit['document_hash'] == seed['hash'] for hit in found)
            await db.execute('DELETE FROM memberships WHERE workspace_id=$1 AND subject=$2',workspace,users[1])
            assert (await client.get(api + '/api/reports/' + report['id'],headers=scoped[1])).status_code in (403,404)
            # Use the new administrator's personal workspace: old user data remains read-only.
            personal = {**tokens[0], 'X-Workspace-ID':users[0]}
            created = require(await client.post(api + '/api/research/runs', headers=personal, json={
                'topic':('slow-recovery ' if acceptance else '')+'恢复验收：证据追溯和研究交付','mode':'quick','data_policy':'public',
                'client_request_id':'restore-'+uuid.uuid4().hex}), 202).json()
            queued = await db.fetchrow('SELECT user_id,created_by,status FROM runs WHERE id=$1',created['id'])
            assert queued and queued['user_id'] == users[0] and queued['created_by'] == users[0]
            assert await db.fetchval('SELECT COUNT(*) FROM business_outbox WHERE aggregate_id=$1',created['id']) == 1
            require(await client.post(api + '/api/research/runs/' + created['id'] + '/cancel',headers=personal),200)
            assert await db.fetchval('SELECT status FROM runs WHERE id=$1',created['id']) == 'cancelled'
            checks = {'oidc_tokens_verified':2,'permission_boundaries_verified':3,'reports_sha_verified':1,
                      'citation_snapshots_verified':len(original),'new_research_accepted':1,'new_research_cancelled':1,
                      'authorized_document_retrieval_verified':1,'query_embedding_mode':'keyword fallback; upstream disabled',
                      'offline_antivirus_checks_verified':2,
                      'synthetic_viewer_collection_grants':len(granted_collections),
                      'restored_acl_contract':acl_contract,
                      'execution_completed':False,'upstream_calls_enabled':False}
        finally:
            cleanup_failed = False
            try:
                if users:
                    cleanup_tables = ('collection_grants','memberships') if acl_contract['collection_acl'] else ('memberships',)
                    for table in cleanup_tables:
                        try:
                            await db.execute('DELETE FROM '+table+' WHERE subject=ANY($1::text[])',users)
                        except Exception:
                            cleanup_failed = True
                if master:
                    paths = ['/users/'+subject for subject in users]
                    if client_id:
                        paths.append('/clients/'+client_id)
                    for path in paths:
                        try:
                            response = await client.delete(identity+'/admin/realms/deepresearch'+path,headers={'Authorization':'Bearer '+master})
                            cleanup_failed |= response.status_code not in (204,404)
                        except Exception:
                            cleanup_failed = True
            finally:
                await db.close()
            if cleanup_failed:
                raise RuntimeError('Recovery synthetic identity cleanup incomplete')
    print(json.dumps(checks))

asyncio.run(verify())
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['backup', 'restore'])
    parser.add_argument('--path', type=Path, required=True)
    parser.add_argument('--project', default='dr-restore-verification')
    parser.add_argument('--compose-file', action='append', default=[], help='Explicit source Compose files, repeatable')
    parser.add_argument('--source-project', help='Explicit source Compose project for backup')
    parser.add_argument('--subnet', default='10.254.245.0/24', help='Fresh explicit recovery-only private IPv4 subnet')
    parser.add_argument('--retain-temporary-resources', action='store_true', help='Keep helper containers and restored project/network; no temporary Docker deletion')
    args = parser.parse_args()
    if args.action == 'backup':
        backup(args.path, args.compose_file, args.source_project, retain_temporary_resources=args.retain_temporary_resources)
    else:
        restore(args.path, args.project, args.subnet, retain_temporary_resources=args.retain_temporary_resources)
