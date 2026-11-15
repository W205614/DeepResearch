"""Rebuild MinIO's Go programs over the exact retained native-repair runtime."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_minio_os_20261113 as retained  # noqa: E402
from scripts.grafana_source_build_facts_20261110 import capture  # noqa: E402

IMAGE = 'deepresearch-minio-source:20261115'
DOCKERFILE = 'deployment/docker/Dockerfile.minio-otel-20261115'
PROFILE = 'minio-otel-source-20261115'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def source_inputs(root=ROOT):
    names = set(retained.source_inputs(root)) | {
        DOCKERFILE, DOCKERFILE + '.dockerignore',
        'scripts/build_minio_otel_20261115.py',
        'deployment/vendors/minio-otel-20261115/go.mod',
        'deployment/vendors/minio-otel-20261115/go.sum',
    }
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(names)}


def build(args):
    output = args.output_dir.resolve()
    if output.exists() or output.is_symlink() or not output.is_relative_to(ROOT / '.cache'):
        raise ValueError('Use a new build directory below the workspace cache')
    output.mkdir(parents=True)
    inputs = source_inputs()
    from scripts import check_minio_otel_20261115 as gate
    profile = None if args.prepare else gate.load_profile()
    if not args.no_build:
        # The native-repair builder remains unchanged and validates its full payload.
        base_output = output / 'retained-native-build'
        command = [sys.executable, '-B', ROOT / 'scripts/build_minio_os_20261113.py',
                   '--output-dir', base_output]
        if subprocess.run(['docker', 'image', 'inspect', retained.IMAGE],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            command.append('--no-build')
        for name in ('module_transport', 'source_repository', 'input_dir'):
            if getattr(args, name):
                command.extend(['--' + name.replace('_', '-'), getattr(args, name)])
        retained.logged(command, output, 'retained-native-runtime')
        transport_args = argparse.Namespace(artifact='minio', module_transport=args.module_transport,
                                            source_repository=args.source_repository)
        contexts = retained.baseline.prepare_contexts(transport_args, output / 'go-source-inputs')
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
        if args.prepare:
            revision += '+uncommitted-validation-inputs'
        command = ['docker', 'buildx', 'build', '--pull=false', '--progress', 'plain', '--load',
                   '--build-arg', 'PROJECT_SOURCE_REVISION=' + revision,
                   '--build-arg', 'PROJECT_INPUTS_SHA256=' + retained.baseline.old.manifest_hash(inputs),
                   '--file', ROOT / DOCKERFILE,
                   '--tag', IMAGE]
        for name, path in contexts.items():
            command += ['--build-context', name + '=' + str(path.resolve())]
        retained.logged([*command, ROOT], output, 'build')
    facts = capture(IMAGE, output / 'actual-payload', save_config=True)
    if inputs != source_inputs():
        raise ValueError('MinIO otel build inputs changed during capture')
    receipt = {'schema': 'minio-otel-source-build-receipt-20261115-v1',
               'profile': PROFILE, 'image': IMAGE, 'image_id': facts['image_id'],
               'config_digest': facts['config_digest'], 'diff_ids': facts['diff_ids'],
               'source_inputs': inputs, 'runtime_manifest_sha256': facts['runtime_manifest_sha256'],
               'runtime_config': retained.baseline.old.runtime_config(facts['inspection']['Config']),
               'native_sha256': {row['path']: row['sha256'] for row in facts['runtime_manifest']
                                 if row['path'] in ('usr/bin/silo', 'usr/bin/mcli')},
               'built_at_utc': datetime.now(timezone.utc).isoformat(),
               'preparation_only': args.prepare, 'binary_redistribution_approved': False}
    labels = facts['inspection']['Config']['Labels']
    receipt['project_source_revision'] = labels['org.opencontainers.image.revision']
    receipt['source_inputs_sha256'] = labels['org.deepresearch.project.inputs_sha256']
    write(output / 'build-receipt.json', receipt)
    write(output / 'image-inspect.json', facts['inspection'])
    if profile is not None:
        gate.verify_build(facts, receipt, profile)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', choices=('minio',), default='minio')
    parser.add_argument('--output-dir', type=Path,
                        default=ROOT / '.cache/minio-otel-20261115' / uuid.uuid4().hex)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--no-build', action='store_true')
    for name in ('module-transport', 'source-repository', 'input-dir'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    if args.prepare and args.no_build:
        raise ValueError('Preparation cannot authorize no-build startup')
    receipt = build(args)
    print(json.dumps({'passed': not args.prepare, 'image_id': receipt['image_id'],
                      'binary_redistribution_approved': False}))


if __name__ == '__main__':
    main()
