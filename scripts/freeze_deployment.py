"""Read-only old-topology/runtime snapshot before an in-place upgrade.

Expanded credentials stay in ignored .cache. No Docker mutation occurs here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError('Read-only deployment snapshot failed; output withheld')
    return result.stdout


def freeze(output, baseline, env_file, overrides=()):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / '.cache'):
        raise ValueError('Expanded private snapshots must stay inside ignored workspace .cache')
    ids = run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=deepresearch']).decode().split()
    if not ids:
        raise ValueError('No current DeepResearch containers to freeze')
    containers = json.loads(run(['docker', 'inspect', *ids]))
    by_service = {}
    excluded = []
    for container in containers:
        # Docker run inherits an image's Compose build labels. A real Compose
        # container additionally has a configuration hash; do not accidentally
        # snapshot a labelled disposable validation fixture as live business data.
        if not container['Config']['Labels'].get('com.docker.compose.config-hash'):
            excluded.append({'container_id': container['Id'], 'reason': 'no_compose_config_hash',
                             'validation': container['Config']['Labels'].get('com.deepresearch.validation', '')})
            continue
        service = container['Config']['Labels']['com.docker.compose.service']
        if service in by_service:
            raise ValueError('Single-host freeze expects one existing container per service')
        by_service[service] = container
    if not overrides:
        listed = next(iter(by_service.values()))['Config']['Labels'].get('com.docker.compose.project.config_files', '').split(',')
        overrides = [value for value in listed if value and Path(value).name != 'compose.yaml']
    overrides = [Path(value).resolve() for value in overrides]
    for path in overrides:
        if not path.is_relative_to(ROOT) or not path.is_file() or path.is_symlink():
            raise ValueError('Existing nonsymlink workspace override required')
    output.mkdir(parents=True, exist_ok=False)
    raw = output / 'baseline-source.compose.yaml'
    raw.write_bytes(run(['git', 'show', baseline + ':compose.yaml']))
    command = ['docker', 'compose', '--project-directory', str(ROOT), '--env-file', str(Path(env_file).resolve()), '-f', str(raw)]
    for path in overrides:
        command += ['-f', str(path)]
    config = json.loads(run([*command, '-p', 'deepresearch', 'config', '--format', 'json']))
    if set(config['services']) != set(by_service):
        raise ValueError('Baseline service topology differs from actual containers; review old overrides')
    services = []
    for name, definition in config['services'].items():
        actual = by_service[name]
        definition['image'] = actual['Image']
        definition.pop('build', None)
        definition['environment'] = dict(item.split('=', 1) for item in actual['Config']['Env'])
        definition['entrypoint'] = actual['Config'].get('Entrypoint')
        definition['command'] = actual['Config'].get('Cmd')
        for mount in definition.get('volumes', []):
            if mount['type'] == 'bind' and not Path(mount['source']).is_absolute():
                raise ValueError('Unresolved relative bind source')
        services.append({'service': name, 'container_id': actual['Id'], 'image_id': actual['Image'],
                         'state': actual['State']['Status'], 'mounts': [
                             {'type': mount['Type'], 'name': mount.get('Name', ''), 'destination': mount['Destination']}
                             for mount in actual['Mounts']]})
    private = output / 'rollback-frozen.private.compose.json'
    private.write_text(json.dumps(config, indent=2), encoding='utf-8')
    shutil.copy2(Path(env_file), output / 'environment.private.snapshot')
    for index, path in enumerate(overrides):
        shutil.copy2(path, output / f'override-{index}.private.snapshot')
    report = {'baseline': baseline, 'project': 'deepresearch', 'services': services,
              'private_compose_path': str(private), 'private_compose_sha256': hashlib.sha256(private.read_bytes()).hexdigest(),
              'operations': 'read-only; no containers rebuilt/started/stopped',
              'excluded_unmanaged_labelled_containers': excluded,
              'source_environment': 'actual container environment, not assumed current .env',
              'binds': 'absolute current sources; cold backup freezes their bytes'}
    (output / 'snapshot-redacted.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--override', action='append', default=[])
    args = parser.parse_args()
    report = freeze(args.output, args.baseline, args.env_file, args.override)
    print(json.dumps({'project': report['project'], 'services': len(report['services']),
                      'private_compose_path': report['private_compose_path'], 'docker_mutations': 0}))
