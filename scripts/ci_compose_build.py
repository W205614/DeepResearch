"""Bound enterprise build disk pressure on disposable GitHub-hosted Linux runners."""
from __future__ import annotations

import argparse
import json
import os
import subprocess


def require_hosted_runner():
    if (os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or os.environ.get('RUNNER_OS') != 'Linux'):
        raise RuntimeError('Build-cache pruning is restricted to GitHub-hosted Linux CI')


def build_groups(config):
    """Build identical recipes together so all service image tags are produced."""
    groups = {}
    for service, spec in config['services'].items():
        if 'build' in spec:
            key = json.dumps(spec['build'], sort_keys=True)
            groups.setdefault(key, []).append(service)
    if not groups:
        raise ValueError('Expected enterprise Compose build targets')
    return list(groups.values())


def prune_cache():
    require_hosted_runner()
    # No image/system/volume prune: keep every runtime image and all evidence.
    for command in (['df', '-h', '.'], ['docker', 'system', 'df'],
                    ['docker', 'builder', 'prune', '--all', '--force'],
                    ['docker', 'system', 'df'], ['df', '-h', '.']):
        subprocess.run(command, check=True)


def build():
    require_hosted_runner()
    result = subprocess.run(['docker', 'compose', 'config', '--format', 'json'],
                            check=True, capture_output=True, text=True)
    # The resolved configuration may contain credentials; never log it.
    groups = build_groups(json.loads(result.stdout))
    env = dict(os.environ, COMPOSE_BAKE='false')
    prune_cache()
    for services in groups:
        print('Building recipe for: ' + ', '.join(services), flush=True)
        subprocess.run(['docker', 'compose', '--parallel', '1', 'build', *services],
                       check=True, env=env)
        # Only prune after success. Failed build diagnostics remain available.
        prune_cache()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prune-only', action='store_true')
    args = parser.parse_args(argv)
    if args.prune_only:
        prune_cache()
    else:
        build()


if __name__ == '__main__':
    main()
