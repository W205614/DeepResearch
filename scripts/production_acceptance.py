"""Reproducible, non-paid acceptance in the literal dr-production-test project.

prepare is offline. Other actions require --isolated and retain services, data,
tokens and reports. No private .env is read; the fixture env is created only if
absent and subsequently must match this tool's public fake configuration.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'dr-production-test'
ENV_NAME = '.env.production-test'
RECEIPTS = Path('.cache/production-acceptance')
COMPOSE_FILES = ('compose.yaml', 'deployment/compose/compose.core.yaml',
                 'deployment/compose/compose.replicas.yaml', 'deployment/compose/compose.production-test.yaml')
BUILD_SERVICES = ('agent', 'backend', 'web', 'postgres', 'keycloak', 'storage-init')
ACTIVE_SERVICES = (*BUILD_SERVICES, 'worker', 'document-worker', 'migrate', 'bootstrap-db',
                   'keycloak-postgres', 'redis', 'etcd', 'minio', 'milvus', 'clamav',
                   'alert-token-init', 'edge', 'agent-gateway')
MARKER = '# Managed public fake fixture: scripts/production_acceptance.py; never production'


def fixture_values():
    values = {name: 'fixture-' + name.lower().replace('_', '-') + '-only'
              for name in ('POSTGRES_PASSWORD', 'MIGRATOR_DB_PASSWORD', 'JAVA_DB_PASSWORD',
                           'PYTHON_DB_PASSWORD', 'MONITOR_DB_PASSWORD', 'KEYCLOAK_DB_PASSWORD',
                           'KEYCLOAK_ADMIN_PASSWORD', 'MINIO_ROOT_PASSWORD', 'OBJECT_STORE_SECRET_KEY',
                           'MILVUS_STORAGE_PASSWORD', 'GRAFANA_ADMIN_PASSWORD')}
    values.update(LLM_PROVIDER='compatible', LLM_MODEL_ID='fixture-research', VISION_MODEL_ID='fixture-vision',
                  LLM_BASE_URL='http://fixture.invalid/v1', LLM_API_KEY='fixture-no-external-calls',
                  LLM_EXTRA_BODY='{}', EMBEDDING_BASE_URL='http://fixture.invalid/v1',
                  EMBEDDING_MODEL='fixture-hash-3072', EMBEDDING_DIMENSION='3072',
                  EMBEDDING_API_KEY='fixture-no-external-calls', SEND_EMBEDDING_DIMENSIONS='true',
                  WEB_SEARCH_PROVIDER='openai', BOCHA_API_KEY='', RERANKER_ENABLED='false', RERANKER_API_KEY='',
                  AUTH_MODE='oidc', OIDC_ISSUER='http://localhost:28180/realms/deepresearch-perf',
                  OIDC_JWKS_URL='http://keycloak:8080/realms/deepresearch-perf/protocol/openid-connect/certs',
                  OIDC_AUDIENCE='deepresearch-api', OIDC_CLIENT_ID='performance-load',
                  OIDC_SCOPES='openid profile email', OIDC_REGISTRATION_ENABLED='false',
                  OIDC_CONNECT_ORIGINS='http://localhost:28180', OIDC_CA_CERTS='',
                  ALLOW_INTERNAL_MODEL_PROCESSING='true', PLATFORM_OPERATOR_SUBJECTS='',
                  DEPLOYMENT_ID=PROJECT, MAX_CONCURRENT_RUNS='3', RESEARCH_WORKER_MAX_JOBS='3',
                  PLATFORM_ACTIVE_RUN_LIMIT='5', MAX_QUEUED_RUNS='20', WORKSPACE_CONCURRENT_RUN_LIMIT='2',
                  APP_PORT='28080', REPLICA_APP_PORT='28080', MINIO_ROOT_USER='deepresearch',
                  OBJECT_STORE_ACCESS_KEY='dr-objects', OBJECT_STORE_REGION='us-east-1',
                  OBJECT_STORE_AUTO_CREATE_BUCKET='true', OBJECT_STORE_ADDRESSING_STYLE='path',
                  DOCUMENT_SCAN_MODE='clamav', DEMO_MODE='false', QUEUE_BACKEND='redis',
                  FEISHU_WEBHOOK_URL='', OTEL_EXPORTER_OTLP_ENDPOINT='',
                  REDIS_SSL_CA_CERTS='', MILVUS_TOKEN='', MILVUS_DB_NAME='default', MILVUS_SERVER_PEM_PATH='')
    return values


def prepare_environment(root=ROOT):
    path = root / ENV_NAME
    body = MARKER + '\n' + '\n'.join(f'{key}={value}' for key, value in sorted(fixture_values().items())) + '\n'
    if path.exists():
        # Refuse an unknown existing file before reading anything beyond its marker.
        with path.open(encoding='utf-8-sig') as stream:
            if stream.readline().rstrip('\n') != MARKER:
                raise ValueError('Existing .env.production-test is unmanaged; preserve it and use a dedicated checkout')
        if path.read_text(encoding='utf-8-sig') != body:
            raise ValueError('Managed fixture env differs; preserve it before preparing a new isolated checkout')
    else:
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(body)
        path.chmod(0o600)
    return path


def child_environment():
    # Compose process variables override --env-file. Keep tooling/OS variables,
    # never inherited application keys, cloud credentials or COMPOSE_* overrides.
    allowed = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'TMPDIR',
               'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA',
               'HOMEDRIVE', 'HOMEPATH', 'USERNAME', 'ALLUSERSPROFILE', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
               'PROGRAMW6432', 'COMMONPROGRAMFILES', 'COMMONPROGRAMFILES(X86)', 'COMMONPROGRAMW6432',
               'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'DOCKER_CERT_PATH', 'DOCKER_TLS_VERIFY',
               'DOCKER_BUILDKIT', 'BUILDKIT_PROGRESS', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY'}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def compose_command(root=ROOT):
    command = ['docker', 'compose', '--project-directory', str(root), '--env-file', str(root / ENV_NAME), '-p', PROJECT]
    for file in COMPOSE_FILES:
        command.extend(['-f', str(root / file)])
    return command


def source_identity(root, environment):
    files = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard'],
                           cwd=root, env=environment, check=True, capture_output=True, text=True).stdout.splitlines()
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, env=environment,
                          check=True, capture_output=True, text=True).stdout.strip()
    hasher, count = hashlib.sha256(), 0
    for name in sorted(set(files)):
        relative = Path(name)
        if any(part in {'.git', '.cache', '.venv', 'node_modules', '__pycache__'} or
               part == '.env' or part.startswith('.env.') for part in relative.parts):
            continue
        path = root / relative
        if path.is_file():
            hasher.update(name.encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
            count += 1
    return {'git_head': head, 'working_tree_sha256': hasher.hexdigest(), 'source_files': count}


def validate_configuration(config):
    if config.get('name') != PROJECT:
        raise ValueError('Unexpected Compose project')
    services = config['services']
    for role in ('agent', 'worker', 'document-worker', 'acceptance'):
        env = services[role]['environment']
        if (env.get('LLM_API_KEY') != 'fixture-no-external-calls'
                or env.get('EMBEDDING_API_KEY') != 'fixture-no-external-calls'
                or env.get('LLM_BASE_URL') != 'http://fixture.invalid/v1'
                or env.get('EMBEDDING_BASE_URL') != 'http://fixture.invalid/v1'
                or env.get('ALLOW_INTERNAL_MODEL_PROCESSING') != 'true'
                or env.get('INTEGRATION') != '1' or env.get('RERANKER_ENABLED') != 'false'):
            raise ValueError('Acceptance must use public fake credentials and non-paid fixture providers')
    if services['backend']['environment'].get('ALLOW_INTERNAL_MODEL_PROCESSING') != 'true':
        raise ValueError('Java fixture must permit the controlled internal-data flow')
    for role in ('web', 'backend', 'agent', 'worker', 'document-worker'):
        if services[role].get('deploy', {}).get('replicas') != 2:
            raise ValueError('Acceptance requires two replicas of every application role')
    for resource in (*config.get('volumes', {}).values(), *config.get('networks', {}).values()):
        if resource.get('external') or not resource.get('name', '').startswith(PROJECT + '_'):
            raise ValueError('Acceptance resources must be project-owned and isolated')
    for role in ACTIVE_SERVICES:
        for port in services[role].get('ports', []):
            if role not in {'edge', 'keycloak'} or port.get('host_ip') not in {'127.0.0.1', '::1'}:
                raise ValueError('Only isolated loopback fixture entrypoints may publish ports')


class Runner:
    def __init__(self, root, report_dir):
        self.root, self.report_dir = root, report_dir
        self.environment = child_environment()
        self.steps = []

    def run(self, label, command, *, capture=False, timeout=900):
        log = self.report_dir / (label + '.log')
        print(json.dumps({'step': label, 'log': str(log)}), flush=True)
        if capture:
            result = subprocess.run(command, cwd=self.root, env=self.environment,
                                    capture_output=True, text=True, encoding='utf-8', timeout=timeout)
        else:
            with log.open('w', encoding='utf-8') as stream:
                result = subprocess.run(command, cwd=self.root, env=self.environment,
                                        stdout=stream, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        self.steps.append({'step': label, 'exit_code': result.returncode, 'log': str(log) if not capture else None})
        if result.returncode:
            raise RuntimeError(label + ' failed; inspect the retained log')
        return result.stdout if capture else ''


def containers(runner, compose):
    ids = runner.run('container-ids', compose + ['ps', '-aq'], capture=True).split()
    if not ids:
        return []
    template = '{{json .Id}}|{{json .Image}}|{{json .Name}}|{{json .Config.Labels}}'
    rows = runner.run('container-identities', ['docker', 'inspect', '--format', template, *ids], capture=True)
    result = []
    for line in rows.splitlines():
        ident, image, name, labels = map(json.loads, line.split('|', 3))
        if (labels.get('com.docker.compose.project') != PROJECT or
                Path(labels.get('com.docker.compose.project.working_dir', '')).resolve() != runner.root.resolve()):
            raise ValueError('Existing project belongs to a different checkout; no service was changed')
        result.append({'id': ident, 'image_id': image, 'name': name,
                       'service': labels.get('com.docker.compose.service', '')})
    return result


def execute(args, *, root=ROOT):
    env_file = prepare_environment(root)
    if args.action == 'prepare':
        return {'prepared': True, 'project': PROJECT, 'fixture_env': str(env_file), 'supplier_http_requests': 0,
                'docker_commands_executed': 0, 'compose_files': list(COMPOSE_FILES)}
    if not args.isolated:
        raise ValueError('Launching acceptance requires --isolated')
    if args.reuse_images and not args.image_baseline.strip():
        raise ValueError('--reuse-images requires --image-baseline describing the actual reused build')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:8]
    report_dir = root / RECEIPTS / run_id
    report_dir.mkdir(parents=True)
    runner, compose = Runner(root, report_dir), compose_command(root)
    report = {'project': PROJECT, 'run_id': run_id, 'passed': False, 'suite': args.action,
              'production_capacity_passed': False,
              'provider': 'non-paid deterministic fixture; real OIDC/PostgreSQL/Redis/Milvus/S3/ClamAV',
              'resources_retained': True, 'vendor_image_approval': 'existing repository artifact contract; not granted by rebuilding',
              'build': 'explicit image reuse' if args.reuse_images else 'canonical repository Dockerfiles',
              'reused_image_baseline': args.image_baseline if args.reuse_images else None,
              'reuse_does_not_verify_latest_source': args.reuse_images}
    container_receipts = '/receipts/' + run_id
    try:
        report['source'] = source_identity(root, runner.environment)
        config = json.loads(runner.run('compose-config', compose + ['--profile', 'acceptance', 'config', '--format', 'json'], capture=True))
        validate_configuration(config)
        existing = containers(runner, compose)
        report['containers_before'] = existing
        if existing and not args.reuse_stack:
            raise ValueError('Existing isolated project retained; pass --reuse-stack explicitly to reuse it')
        if not existing:
            volume_names = set(runner.run('volume-inventory', ['docker', 'volume', 'ls', '--format', '{{.Name}}'],
                                          capture=True).splitlines())
            retained_volumes = sorted(resource['name'] for resource in config.get('volumes', {}).values()
                                      if resource['name'] in volume_names)
            report['detached_volumes_before'] = retained_volumes
            if retained_volumes:
                raise ValueError('Existing isolated volumes have no matching containers; preserve them and use separate volume names')
        if not args.reuse_images:
            runner.run('canonical-build', compose + ['build', *BUILD_SERVICES], timeout=args.build_timeout)
            if source_identity(root, runner.environment) != report['source']:
                raise RuntimeError('Source changed during the build; do not claim a canonical acceptance baseline')
        images = {}
        report['image_ids'] = images
        for name in sorted({config['services'][role]['image'] for role in ACTIVE_SERVICES}):
            # Approved local artifact tags must already be loaded by the documented
            # verification tools. Only immutable digest references may be pulled.
            check = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', name],
                                   cwd=root, env=runner.environment, capture_output=True, text=True)
            if check.returncode and not args.reuse_images and '@sha256:' in name:
                runner.run('pull-' + hashlib.sha256(name.encode()).hexdigest()[:10], ['docker', 'pull', name], timeout=900)
                check = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', name],
                                       cwd=root, env=runner.environment, capture_output=True, text=True)
            if check.returncode:
                raise RuntimeError('Required fixture image ' + name + ' is missing; build candidates or load the approved vendor artifact first')
            images[name] = check.stdout.strip()
        runner.run('startup', compose + ['up', '-d', '--no-build', '--pull', 'never', '--wait',
                                         '--wait-timeout', str(args.startup_timeout)], timeout=args.startup_timeout + 60)
        report['containers_started'] = containers(runner, compose)
        acceptance = compose + ['run', '--rm', '--no-deps', '-T', 'acceptance']
        provision = compose + ['run', '--rm', '--no-deps', '-T', '-e', 'PERFORMANCE_ADMIN_USERNAME=admin',
                                '-e', 'PERFORMANCE_ADMIN_PASSWORD=' + fixture_values()['KEYCLOAK_ADMIN_PASSWORD'],
                                'acceptance', 'python', '-m', 'scripts.prepare_production_fixture', '--isolated',
                                '--accounts', '50', '--output', container_receipts + '/tokens.json']
        runner.run('identities', provision, timeout=600)
        for suite in (('smoke', 'load', 'faults') if args.action == 'all' else (args.action,)):
            if suite in {'smoke', 'load'}:
                formal = suite == 'load'
                command = acceptance + ['python', 'scripts/production_load.py', '--isolated', '--base-url', 'http://edge:8080',
                    '--tokens-json', container_receipts + '/tokens.json', '--seconds', '1800' if formal else str(args.smoke_seconds),
                    '--accounts', '50', '--clients', '10', '--research-concurrency', '5',
                    '--documents', '500' if formal else '2', '--chunks-per-document', '100' if formal else '4',
                    '--report', container_receipts + '/' + suite + '.json']
                runner.run(suite, command, timeout=7200 if formal else 900)
                result = json.loads((report_dir / (suite + '.json')).read_text(encoding='utf-8'))
                if not result.get('functional_passed') or (formal and not result.get('production_capacity_passed')):
                    raise RuntimeError(suite + ' did not meet its acceptance contract')
            else:
                runner.run('faults', [sys.executable, str(root / 'scripts/production_faults.py'),
                    '--tokens-json', container_receipts + '/tokens.json', '--report', str(report_dir / 'faults.json')], timeout=1800)
                result = json.loads((report_dir / 'faults.json').read_text(encoding='utf-8'))
                if not result.get('passed'):
                    raise RuntimeError('Fault acceptance did not pass')
        report['passed'] = True
        report['production_capacity_passed'] = args.action in {'all', 'load'}
        return report
    except BaseException as error:
        report['failure_type'] = type(error).__name__
        raise
    finally:
        report['steps'] = runner.steps
        (report_dir / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'manifest': str(report_dir / 'manifest.json'), 'passed': report['passed'],
                          'resources_retained': True}), flush=True)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('action', choices=('prepare', 'smoke', 'load', 'faults', 'all'))
    result.add_argument('--isolated', action='store_true')
    result.add_argument('--reuse-stack', action='store_true', help='Explicitly reuse this checkout\'s existing isolated project')
    result.add_argument('--reuse-images', action='store_true', help='Skip canonical builds; record actual image IDs and baseline')
    result.add_argument('--image-baseline', default='', help='Required with --reuse-images; never claim it verifies latest source')
    result.add_argument('--smoke-seconds', type=int, default=30)
    result.add_argument('--build-timeout', type=int, default=5400)
    result.add_argument('--startup-timeout', type=int, default=900)
    return result


def main():
    arguments = parser().parse_args()
    if min(arguments.smoke_seconds, arguments.build_timeout, arguments.startup_timeout) < 1:
        raise SystemExit('Durations must be positive')
    try:
        report = execute(arguments)
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'passed': False, 'failure_type': type(error).__name__,
                          'message': str(error) if isinstance(error, (ValueError, RuntimeError)) else
                          'Acceptance stopped; existing data, services and reports are retained'}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
