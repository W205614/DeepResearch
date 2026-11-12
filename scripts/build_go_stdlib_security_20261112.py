"""Build or verify the separate local MinIO/Grafana Go 1.27.4 subjects."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_source_build_20261110 as old  # noqa: E402
from scripts import build_grafana_source_20261110 as old_builder  # noqa: E402
from scripts.grafana_source_build_facts_20261110 import capture  # noqa: E402
from scripts.image_disposition import load_binding as minio_binding  # noqa: E402
from scripts.retained_source_review_20261112 import snapshot  # noqa: E402

CACHE = ROOT / '.cache/go-stdlib-security-20261112'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def logged(command, output, name):
    write(output / (name + '.command.json'), [str(v) for v in command])
    with (output / (name + '.log')).open('wb') as stream:
        subprocess.run([str(v) for v in command], cwd=ROOT, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)


def source_inputs(artifact, root=ROOT):
    names = {'deployment/docker/Dockerfile.' + artifact + '-20261112',
             'deployment/docker/Dockerfile.' + artifact + '-20261112.dockerignore',
             'scripts/build_go_stdlib_security_20261112.py',
             'scripts/prefetch_go_security_modules_20261112.py',
             'deployment/security/go-stdlib-20261112-evidence/source.lock.json',
             'deployment/security/go-stdlib-20261112-evidence/Go-LICENSE'}
    if artifact == 'grafana':
        names.update(old_builder.source_inputs(root))
        names.add('deployment/vendors/grafana-20261107/candidate_build_backends.py')
        names.add('scripts/retained_source_review_20261112.py')
    else:
        names.add('scripts/prefetch_minio_source_20261112.py')
        names.update(path.relative_to(root).as_posix() for path in (root / 'deployment/vendors/minio').rglob('*')
                     if path.is_file() and '__pycache__' not in path.parts)
    return {name: old.digest(old.checked(root, name)) for name in sorted(names)}


def consumer(artifact):
    if artifact == 'grafana':
        from scripts import check_grafana_source_build_20261112 as gate
    else:
        from scripts import check_minio_source_build_20261112 as gate
    return gate


def image_facts(artifact, output, sources):
    image = 'deepresearch-' + artifact + '-source:20261112'
    facts = capture(image, output / 'actual-payload', save_config=True)
    expected = (old.load_profile(snapshot(ROOT))['expected']['native_sha256'] if artifact == 'grafana'
                else minio_binding()['binary_sha256'])
    files = {row['path']: row for row in facts['runtime_manifest']}
    native = {name: files[name]['sha256'] for name in expected}
    if any(native[name] == previous for name, previous in expected.items()):
        raise ValueError('Every affected Go executable must actually change')
    receipt = {'schema': 'go-stdlib-source-build-receipt-20261112-v1',
        'profile': artifact + '-source-20261112', 'image': image, 'source_inputs': sources,
        'native_sha256': native, 'runtime_manifest_sha256': facts['runtime_manifest_sha256'],
        'runtime_config': old.runtime_config(facts['inspection']['Config']),
        'image_id': facts['image_id'], 'diff_ids': facts['diff_ids'],
        'config_digest': facts['config_digest'], 'built_at_utc': datetime.now(timezone.utc).isoformat(),
        'binary_redistribution_approved': False}
    write(output / 'image-inspect.json', facts['inspection'])
    write(output / 'build-receipt.json', receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', choices=('minio', 'grafana'), required=True)
    parser.add_argument('--output-dir', type=Path, default=CACHE / ('run-' + uuid.uuid4().hex[:12]))
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--no-build', action='store_true')
    for name in ('source-repository', 'module-transport', 'retained-module-transport', 'thrift-transport'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = build(args)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'passed': False, 'error_type': type(error).__name__,
                          'error': str(error)[:240] if isinstance(error, ValueError) else 'Build failed; logs retained'}))
        return 1
    print(json.dumps({'passed': not args.prepare, 'operational_build': True,
                      'preparation_only': args.prepare, 'image_id': receipt['image_id'],
                      'binary_redistribution_approved': False}))
    return 0


def prepare_contexts(args, output):
    transport = args.module_transport or CACHE / 'module-transport'
    logged([sys.executable, '-B', ROOT / 'scripts/prefetch_go_security_modules_20261112.py',
            '--output', transport, '--receipt', output / 'module-transport.json'], output, 'module-prefetch')
    contexts = {'securitymodules': transport}
    if args.artifact == 'minio':
        source = args.source_repository or CACHE / 'minio-source.git'
        logged([sys.executable, '-B', ROOT / 'scripts/prefetch_minio_source_20261112.py',
                '--output', source], output, 'source-prefetch')
        contexts['miniosource'] = source
        return contexts
    baseline = old.load_profile(snapshot(ROOT))
    old_image = 'deepresearch-grafana-source:20261110'
    if subprocess.run(['docker', 'image', 'inspect', old_image], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode:
        logged([sys.executable, '-B', ROOT / 'scripts/build_grafana_source_20261110.py',
                '--prepare', '--output-dir', output / 'previous-runtime-build'], output, 'previous-runtime-build')
    before = capture(old_image, output / 'previous-runtime-actual')
    if (before['runtime_manifest_sha256'] != baseline['expected']['runtime_manifest_sha256']
            or old.runtime_config(before['inspection']['Config']) != baseline['expected']['runtime_config']):
        raise ValueError('The retained non-Go runtime differs from its complete verified payload')
    write(output / 'previous-runtime-identity.json', {key: before[key] for key in
          ('image_id', 'diff_ids', 'runtime_manifest_sha256')})
    source = args.source_repository or CACHE / 'grafana-source.git'
    logged([sys.executable, '-B', ROOT / 'deployment/vendors/grafana-20261110/prefetch_source.py',
            '--output', source, '--evidence', output / 'source-transport'], output, 'source-prefetch')
    empty = output / 'empty-module-transport'
    empty.mkdir()
    contexts.update(grafanasource=source, moduletransport=args.retained_module_transport or empty,
                    thrifttransport=args.thrift_transport or empty)
    return contexts


def build(args):
    if args.prepare and args.no_build:
        raise ValueError('Preparation cannot authorize no-build startup')
    output = args.output_dir.resolve()
    if output.exists() or output.is_symlink() or not output.is_relative_to((ROOT / '.cache').resolve()):
        raise ValueError('Use a new evidence directory under this checkout .cache')
    output.mkdir(parents=True)
    gate = consumer(args.artifact)
    profile = None if args.prepare else gate.load_profile()
    sources = source_inputs(args.artifact)
    if profile is not None and profile['source_inputs'] != sources:
        raise ValueError('Current build inputs differ from the exact reviewed source profile')
    if not args.no_build:
        contexts = prepare_contexts(args, output)
        command = ['docker', 'buildx', 'build', '--progress', 'plain', '--load', '--pull=false',
                   '--metadata-file', str(output / 'build-metadata.json')]
        for name, path in contexts.items():
            if not path.is_dir() or path.is_symlink():
                raise ValueError('An explicit source transport is unavailable')
            command += ['--build-context', name + '=' + str(path.resolve())]
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
        if args.prepare:
            revision += '+uncommitted-validation-inputs'
        command += ['--build-arg', 'PROJECT_SOURCE_REVISION=' + revision,
                    '--build-arg', 'PROJECT_INPUTS_SHA256=' + old.manifest_hash(sources),
                    '--tag', gate.IMAGE, '--file', gate.DOCKERFILE, '.']
        logged(command, output, 'build')
        if args.artifact == 'minio':
            evidence_command = []
            index = 0
            while index < len(command) - 1:
                token = command[index]
                if token == '--load':
                    index += 1
                    continue
                if token in {'--metadata-file', '--tag'}:
                    index += 2
                    continue
                evidence_command.append(token)
                index += 1
            evidence_command += ['--target', 'regression-evidence', '--output',
                                 'type=local,dest=' + str(output / 'regressions'), '.']
            logged(evidence_command, output, 'regression-export')
    if source_inputs(args.artifact) != sources:
        raise ValueError('Build inputs changed during the build; no receipt was issued')
    receipt = image_facts(args.artifact, output, sources)
    receipt['preparation_only'] = args.prepare
    write(output / 'build-receipt.json', receipt)
    if profile is not None:
        inspection, = json.loads(subprocess.check_output(['docker', 'image', 'inspect', gate.IMAGE]))
        gate.verify_receipt(receipt, inspection, profile)
        write(CACHE / args.artifact / 'build-receipt.json', receipt)
    return receipt


if __name__ == '__main__':
    raise SystemExit(main())
